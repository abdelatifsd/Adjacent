# src/adjacent/graph/ingest.py
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Tuple, List, Optional
from neo4j import GraphDatabase
from tqdm import tqdm

# NOTE: add this to your dependencies:
#   uv add jsonschema
from jsonschema import Draft202012Validator

@dataclass(frozen=True)
class Neo4jConfig:
    uri: str
    user: str
    password: str


# ----------------------------
# Expected input format
# ----------------------------
# The ingest expects a JSONL file where each line is a JSON object that conforms
# to schemas/product.json. Minimum required fields:
#   {"id": "...", "description": "..."}
#
# Optional fields (recommended when available):
#   title, brand, category, tags[], price, currency, image_url, metadata{}
#
# Records that do not validate against the schema will FAIL ingestion.


# ----------------------------
# IO
# ----------------------------
def iter_records(input_path: Path) -> Iterator[Tuple[int, Dict[str, Any]]]:
    """
    Yield (lineno, record) from either:
      - .jsonl (one object per line)
      - .json  (a JSON array of objects)

    We use 'lineno' for error messages. For .json, lineno is the array index + 1.
    """
    suffix = input_path.suffix.lower()

    if suffix == ".jsonl":
        with input_path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid JSON on line {lineno} in {input_path}: {e}") from e
                if not isinstance(rec, dict):
                    raise ValueError(f"Line {lineno} in {input_path} must be a JSON object.")
                yield lineno, rec
        return

    if suffix == ".json":
        with input_path.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in {input_path}: {e}") from e

        if not isinstance(data, list):
            raise ValueError(f"{input_path} must be a JSON array of objects (list).")

        for idx, rec in enumerate(data, start=1):
            if not isinstance(rec, dict):
                raise ValueError(f"Item {idx} in {input_path} must be a JSON object.")
            yield idx, rec
        return

    raise ValueError(f"Unsupported input format: {input_path}. Expected .json or .jsonl")



def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------
# Schema validation
# ----------------------------
def build_validator(schema_path: Path) -> Draft202012Validator:
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    schema = load_json(schema_path)
    return Draft202012Validator(schema)


def validate_record(
    validator: Draft202012Validator,
    rec: Dict[str, Any],
    lineno: int,
    input_path: Path,
) -> None:
    errors = sorted(validator.iter_errors(rec), key=lambda e: e.path)
    if not errors:
        return

    # Produce a compact, actionable error message
    parts: List[str] = []
    for e in errors[:5]:  # cap to avoid huge dumps
        loc = ".".join([str(p) for p in e.path]) if e.path else "<root>"
        parts.append(f"- {loc}: {e.message}")

    more = "" if len(errors) <= 5 else f"\n(… {len(errors) - 5} more validation errors)"
    raise ValueError(
        f"Schema validation failed for line {lineno} in {input_path}:\n"
        + "\n".join(parts)
        + more
        + "\n\nExpected canonical record format per schema, e.g.\n"
          '{"id":"sku_123","description":"...","title":"...","category":"...","tags":["..."]}\n'
    )


# ----------------------------
# Normalization
# ----------------------------
def _norm_str(x: Any) -> str:
    return str(x).strip()


def _norm_str_opt(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return s if s else None


def normalize_product(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Input: already schema-validated record.
    Output: normalized record for Neo4j + embedding.
    """
    pid = _norm_str(rec["id"])
    desc = _norm_str(rec["description"])

    title = _norm_str_opt(rec.get("title"))
    brand = _norm_str_opt(rec.get("brand"))
    category = _norm_str_opt(rec.get("category"))

    tags_raw = rec.get("tags", [])
    tags: List[str] = []
    if isinstance(tags_raw, list):
        tags = [t for t in (_norm_str_opt(x) for x in tags_raw) if t]

    # Deterministic text document for embeddings (stable format)
    # v1: description is required, so it's always present.
    parts: List[str] = []
    if title:
        parts.append(f"TITLE: {title}")
    parts.append(f"DESCRIPTION: {desc}")
    if category:
        parts.append(f"CATEGORY: {category}")
    if brand:
        parts.append(f"BRAND: {brand}")
    if tags:
        parts.append("TAGS: " + ", ".join(tags))

    embed_text = "\n".join(parts)

    out: Dict[str, Any] = {
        "id": pid,
        "description": desc,
        "embed_text": embed_text,
    }

    # Store optionals if present (useful later for prompting)
    if title:
        out["title"] = title
    if brand:
        out["brand"] = brand
    if category:
        out["category"] = category
    if tags:
        out["tags"] = tags

    # Keep optional structured fields if you want them in Neo4j later
    # (schema allows them, but we don't require them)
    if "price" in rec and rec["price"] is not None:
        out["price"] = rec["price"]
    if "currency" in rec and rec["currency"] is not None:
        out["currency"] = _norm_str(rec["currency"])
    if "image_url" in rec and rec["image_url"] is not None:
        out["image_url"] = _norm_str(rec["image_url"])
    if "metadata" in rec and isinstance(rec["metadata"], dict) and rec["metadata"]:
        out["metadata"] = rec["metadata"]

    return out


def load_products(
    input_path: Path,
    validator,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for lineno, rec in iter_records(input_path):
        validate_record(validator, rec, lineno, input_path)
        products.append(normalize_product(rec))
        if limit is not None and len(products) >= limit:
            break
    return products



# ----------------------------
# Neo4j
# ----------------------------
CONSTRAINT_CYPHER = """
CREATE CONSTRAINT product_id_unique IF NOT EXISTS
FOR (p:Product) REQUIRE p.id IS UNIQUE;
"""

UPSERT_PRODUCTS_CYPHER = """
UNWIND $rows AS row
MERGE (p:Product {id: row.id})
SET p.description = row.description,
    p.embed_text = row.embed_text
FOREACH (_ IN CASE WHEN row.title IS NULL THEN [] ELSE [1] END |
  SET p.title = row.title
)
FOREACH (_ IN CASE WHEN row.brand IS NULL THEN [] ELSE [1] END |
  SET p.brand = row.brand
)
FOREACH (_ IN CASE WHEN row.category IS NULL THEN [] ELSE [1] END |
  SET p.category = row.category
)
FOREACH (_ IN CASE WHEN row.tags IS NULL THEN [] ELSE [1] END |
  SET p.tags = row.tags
)
FOREACH (_ IN CASE WHEN row.price IS NULL THEN [] ELSE [1] END |
  SET p.price = row.price
)
FOREACH (_ IN CASE WHEN row.currency IS NULL THEN [] ELSE [1] END |
  SET p.currency = row.currency
)
FOREACH (_ IN CASE WHEN row.image_url IS NULL THEN [] ELSE [1] END |
  SET p.image_url = row.image_url
)
FOREACH (_ IN CASE WHEN row.metadata IS NULL THEN [] ELSE [1] END |
  SET p.metadata = row.metadata
)
"""


def ensure_constraints(cfg: Neo4jConfig) -> None:
    driver = GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))
    with driver:
        with driver.session() as session:
            session.run(CONSTRAINT_CYPHER)


def upsert_products(cfg: Neo4jConfig, products: List[Dict[str, Any]], batch_size: int = 500) -> Tuple[int, int]:
    if not products:
        return (0, 0)

    driver = GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))
    batch_count = 0

    # Ensure keys exist for optional fields to simplify Cypher FOREACH checks
    def _with_optionals(row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        for k in ["title", "brand", "category", "tags", "price", "currency", "image_url", "metadata"]:
            out.setdefault(k, None)
        return out

    rows = [_with_optionals(p) for p in products]

    with driver:
        with driver.session() as session:
            for i in tqdm(range(0, len(rows), batch_size), desc="Product batches"):
                batch = rows[i : i + batch_size]
                session.run(UPSERT_PRODUCTS_CYPHER, rows=batch)
                batch_count += 1

    return (len(products), batch_count)


# ----------------------------
# CLI
# ----------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest Product nodes into Neo4j from JSONL (schema-enforced).")
    p.add_argument("--input", required=True, help="Path to products.jsonl (canonical schema)")
    p.add_argument(
        "--schema",
        default="schemas/product.json",
        help="Path to product JSON schema (default: schemas/product.json)",
    )
    p.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    p.add_argument("--neo4j-user", default="neo4j")
    p.add_argument("--neo4j-password", default="adjacent123")
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--limit", type=int, default=None, help="Optional limit for quick tests")
    p.add_argument("--no-constraints", action="store_true", help="Skip creating constraints")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    schema_path = Path(args.schema)
    validator = build_validator(schema_path)

    cfg = Neo4jConfig(
        uri=args.neo4j_uri,
        user=args.neo4j_user,
        password=args.neo4j_password,
    )

    if not args.no_constraints:
        ensure_constraints(cfg)

    products = load_products(input_path, validator, limit=args.limit)
    n, batches = upsert_products(cfg, products, batch_size=args.batch_size)

    print(f"Ingested {n} products in {batches} batches into Neo4j ({cfg.uri}).")
    print("Input format was schema-enforced via:", schema_path)


if __name__ == "__main__":
    main()
