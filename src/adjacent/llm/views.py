# src/adjacent/llm/views.py
"""
LLM-safe product views for edge inference.

Projects stored Product records to a consistent format for LLM input.
Assumes products have been normalized during ingest.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class LLMProductView:
    """
    Immutable projection of a Product for LLM edge inference.
    
    Excluded from full Product:
    - embedding, embed_text, embedding_* (vector/internal)
    - image_url (not used in text inference)
    - metadata (unpredictable structure)
    """
    
    # Required
    id: str
    description: str
    
    # Optional (explicit defaults for consistent JSON shape)
    title: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    price: Optional[float] = None
    currency: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Full dict with all fields (nulls included)."""
        return asdict(self)
    
    def to_compact_dict(self) -> Dict[str, Any]:
        """Dict with null/empty fields omitted (saves tokens)."""
        return {k: v for k, v in asdict(self).items() if v not in (None, [], "")}


def project(product: Dict[str, Any]) -> LLMProductView:
    """
    Project a normalized product to LLMProductView.
    
    Assumes product was ingested through the standard pipeline.
    """
    if not product.get("id") or not product.get("description"):
        raise ValueError(f"Product missing required field(s): {product.get('id', '<no id>')}")
    
    return LLMProductView(
        id=product["id"],
        description=product["description"],
        title=product.get("title"),
        brand=product.get("brand"),
        category=product.get("category"),
        tags=product.get("tags") or [],
        price=product.get("price"),
        currency=product.get("currency"),
    )


def project_many(products: List[Dict[str, Any]]) -> List[LLMProductView]:
    """Project multiple products."""
    return [project(p) for p in products]
