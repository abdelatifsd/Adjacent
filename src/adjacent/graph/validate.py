from pathlib import Path
from typing import Dict, Any, List
from jsonschema import Draft202012Validator
from commons.utils import load_json


# ----------------------------
# Schema validation
# ----------------------------
def build_product_validator(schema_path: Path) -> Draft202012Validator:
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    schema = load_json(schema_path)
    return Draft202012Validator(schema)


def validate_product_record(
    validator: Draft202012Validator,
    rec: Dict[str, Any],
    lineno: int,
    input_path: Path,
) -> None:
    errors = sorted(
        validator.iter_errors(rec), key=lambda e: e.path
    )  # ordering to ensure consistent structure
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
