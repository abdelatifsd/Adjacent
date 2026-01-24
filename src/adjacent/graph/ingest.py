# src/adjacent/graph/ingest.py
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple, List
from neo4j import Driver
from tqdm import tqdm
from adjacent.graph.io import iter_records
from adjacent.graph.validate import build_product_validator, validate_product_record
from adjacent.graph.normalize import ProductNormalizer, RequiredConfig
from commons.utils import load_json
from jsonschema import Draft202012Validator
from adjacent.db import Neo4jContext

logger = logging.getLogger(__name__)

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


def load_products(
    input_path: Path,
    validator: Draft202012Validator,
    normalizer: ProductNormalizer,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    logger.info("Loading products from %s", input_path)
    products: list[dict[str, Any]] = []
    for lineno, rec in iter_records(input_path):
        validate_product_record(validator, rec, lineno, input_path)
        products.append(normalizer.normalize(rec))
        if limit is not None and len(products) >= limit:
            break
    logger.info("Loaded %d products", len(products))
    return products


# ----------------------------
# Neo4j
# ----------------------------
CONSTRAINT_CYPHER = """
CREATE CONSTRAINT product_id_unique IF NOT EXISTS
FOR (p:Product) REQUIRE p.id IS UNIQUE;
"""


def build_upsert_cypher(optional_fields: set[str]) -> str:
    """Build UPSERT Cypher dynamically from schema.

    Required fields are always SET.
    Optional fields use FOREACH for conditional updates.
    embed_text (derived field) is always SET.
    """

    # Get required fields dynamically (excluding 'id' which is in MERGE)
    required_fields = [f for f in RequiredConfig.FIELDS if f != "id"]

    # Build SET clause: required fields + embed_text
    set_parts = [f"p.{field} = row.{field}" for field in required_fields]
    set_parts.append("p.embed_text = row.embed_text")  # Always include embed_text

    cypher_parts = [
        "UNWIND $rows AS row",
        "MERGE (p:Product {id: row.id})",  # id is the unique constraint
        "SET " + ",\n    ".join(set_parts),
    ]

    # Add FOREACH blocks for each optional field (sorted for determinism)
    for field in sorted(optional_fields):
        cypher_parts.append(
            f"FOREACH (_ IN CASE WHEN row.{field} IS NULL THEN [] ELSE [1] END |\n"
            f"  SET p.{field} = row.{field}\n"
            f")"
        )

    return "\n".join(cypher_parts)


def ensure_constraints(driver: Driver) -> None:
    """Create Neo4j constraints using the provided driver."""
    logger.info("Creating Neo4j constraints...")
    with driver.session() as session:
        session.run(CONSTRAINT_CYPHER)
    logger.info("Constraints created")


def upsert_products(
    driver: Driver,
    products: List[Dict[str, Any]],
    optional_fields: set[str],
    batch_size: int = 25,
) -> Tuple[int, int]:
    """Upsert products to Neo4j using the provided driver."""
    if not products:
        logger.info("No products to upsert")
        return (0, 0)

    logger.info("Upserting %d products to Neo4j", len(products))
    batch_count = 0

    # Ensure keys exist for optional fields to simplify Cypher FOREACH checks
    def _with_optionals(row: Dict[str, Any]) -> Dict[str, Any]:
        out = {**row}
        for k in optional_fields:
            out.setdefault(k, None)
        return out

    rows = [_with_optionals(p) for p in products]

    # Build Cypher dynamically
    upsert_cypher = build_upsert_cypher(optional_fields)

    with driver.session() as session:
        for i in tqdm(range(0, len(rows), batch_size), desc="Product batches"):
            batch = rows[i : i + batch_size]
            session.run(upsert_cypher, rows=batch)
            batch_count += 1

    logger.info("Upserted %d products in %d batches", len(products), batch_count)
    return (len(products), batch_count)


# ----------------------------
# CLI
# ----------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ingest Product nodes into Neo4j from JSONL (schema-enforced)."
    )
    p.add_argument(
        "--input", required=True, help="Path to products.jsonl (canonical schema)"
    )
    p.add_argument(
        "--schema",
        default="schemas/product.json",
        help="Path to product JSON schema (default: schemas/product.json)",
    )
    p.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    p.add_argument("--neo4j-user", default="neo4j")
    p.add_argument("--neo4j-password", default="adjacent123")
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument(
        "--limit", type=int, default=None, help="Optional limit for quick tests"
    )
    p.add_argument(
        "--no-constraints", action="store_true", help="Skip creating constraints"
    )
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    args = parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    schema_path = Path(args.schema)
    logger.info("Loading schema from %s", schema_path)
    validator = build_product_validator(schema_path)

    # Load schema once for normalization
    schema = load_json(schema_path)
    normalizer: ProductNormalizer = ProductNormalizer(schema)
    optional_fields = normalizer.optional_fields

    # Create shared Neo4j context
    with Neo4jContext(
        uri=args.neo4j_uri,
        user=args.neo4j_user,
        password=args.neo4j_password,
    ) as neo4j_ctx:
        if not args.no_constraints:
            ensure_constraints(neo4j_ctx.driver)

        products = load_products(input_path, validator, normalizer, limit=args.limit)
        n, batches = upsert_products(
            neo4j_ctx.driver, products, optional_fields, batch_size=args.batch_size
        )

        logger.info("✓ Ingested %d products in %d batches into Neo4j (%s)", n, batches, args.neo4j_uri)
        logger.info("Schema: %s", schema_path)


if __name__ == "__main__":
    main()
