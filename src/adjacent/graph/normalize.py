from dataclasses import dataclass
from typing import Dict, Any
from typing import Optional

from typing import Tuple


def _norm_str(x: Any) -> str:
    """Normalize a string. Expects validation to have passed."""
    if x is None:
        raise ValueError("Cannot normalize None (validation should have caught this)")
    if not isinstance(x, str):
        # This should never happen if validation worked
        raise TypeError(
            f"Expected string after validation, got {type(x).__name__}: {x}. "
            "This indicates a validation bug."
        )
    return x.strip()


def _norm_str_opt(x: Any) -> Optional[str]:
    """Normalize an optional string. Returns None if input is None or empty after stripping.

    Raises TypeError if input is not None and not a string (validation should prevent this).
    """
    if x is None:
        return None

    if not isinstance(x, str):
        raise TypeError(
            f"Expected string or None after validation, got {type(x).__name__}: {x}"
        )

    s = x.strip()
    return s if s else None


# ----------------------------
# Field Configuration
# ----------------------------


@dataclass(frozen=True)
class RequiredConfig:
    """Required fields that must always be present and normalized.

    These fields are guaranteed to exist in every normalized record.
    Changes here require schema updates and validation changes.
    """

    FIELDS: Tuple[str, ...] = ("id", "description")

    @staticmethod
    def normalize_required(rec: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize all required fields."""
        return {field: _norm_str(rec[field]) for field in RequiredConfig.FIELDS}


@dataclass(frozen=True)
class EmbeddingConfig:
    """VERSIONED embedding specification.

    Changes here require re-embedding all products and updating the vector DB.
    Kept frozen to prevent accidental modifications.
    """

    VERSION: str = "v1"
    FIELDS: Tuple[str, ...] = ("description",)

    @staticmethod
    def embed_text(rec: Dict[str, Any]) -> str:
        """Extract and normalize embedding text from record."""
        parts = [_norm_str(rec[field]) for field in EmbeddingConfig.FIELDS]
        return "\n".join(parts)


class ProductNormalizer:
    """Transforms schema-validated product records into ingest format.
    Also acts as an interface for schema.

    Encapsulates schema knowledge and embedding configuration.
    Initialize once with schema, then call normalize() for each record.
    """

    def __init__(self, schema: Dict[str, Any]):
        """Initialize normalizer with schema.

        Args:
            schema: Product schema (used to determine storage fields)
        """
        self.schema = schema
        self._all_fields = set(self.schema.get("properties", {}).keys())
        self._optional_fields = self._compute_optional_fields()

    def _compute_optional_fields(self) -> set:
        """Compute optional fields to store (excluding required fields)."""
        required_fields = self.get_required_fields()
        return self._all_fields - required_fields

    @staticmethod
    def get_required_fields() -> set[str]:
        return set(RequiredConfig.FIELDS)

    @staticmethod
    def get_embedding_fields() -> set[str]:
        return set(EmbeddingConfig.FIELDS)

    # --- Properties (Accessible via Instance as attributes) ---
    @property
    def all_fields(self) -> set[str]:
        """Returns all fields defined in the schema."""
        return self._all_fields

    @property
    def optional_fields(self) -> set[str]:
        """Returns fields that are in the schema but not required."""
        return self._optional_fields

    def normalize(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a schema-validated product record.

        Args:
            rec: Schema-validated product record

        Returns:
            Normalized record with embedded text and all valid schema fields
        """
        # Required fields (always present, always normalized)
        out = RequiredConfig.normalize_required(rec)

        # Add embedding text
        out["embed_text"] = EmbeddingConfig.embed_text(rec)

        # Optional fields (conditionally added if present)
        for field in self._optional_fields:
            if field in rec and rec[field] is not None:
                if field == "tags" and isinstance(rec["tags"], list):
                    out["tags"] = [
                        t for t in (_norm_str_opt(x) for x in rec["tags"]) if t
                    ]
                else:
                    out[field] = rec[field]

        return out
