"""High-level embedding service."""

from dataclasses import dataclass
from typing import List, Dict, Any

from adjacent.embeddings.providers import EmbeddingProvider


@dataclass
class EmbeddingResult:
    """Container for embedding results with metadata."""

    text: str
    embedding: List[float]
    model: str
    dimensions: int
    product_id: str | None = None



class EmbeddingService:
    """High-level service for embedding operations.
    
    Wraps a provider and adds metadata to results.
    """

    def __init__(self, provider: EmbeddingProvider):
        self.provider = provider

    def embed_query(self, query: str) -> EmbeddingResult:
        """Embed a single query string."""
        embedding = self.provider.embed(query)
        return EmbeddingResult(
            text=query,
            embedding=embedding,
            model=self.provider.get_model_name(),
            dimensions=self.provider.get_dimension(),
        )

    def embed_products(
        self, products: List[Dict[str, Any]], text_field: str = "embed_text"
    ) -> List[EmbeddingResult]:
        """Embed a batch of products.
        
        Args:
            products: List of product dictionaries
            text_field: Key to extract text from each product
            
        Returns:
            List of EmbeddingResult objects, aligned with input products
        """
        try:
            texts = [p[text_field] for p in products]

        except KeyError as e:
            raise KeyError(f"Missing required field '{text_field}' in one or more products") from e

        product_ids = [p["id"] for p in products]

        embeddings = self.provider.embed_batch(texts)

        # Guarantee alignment
        if len(embeddings) != len(texts):
            raise ValueError(f"Embedding count mismatch: got {len(embeddings)} for {len(texts)} texts")


        return [
            EmbeddingResult(
                product_id=pid,
                text=text,
                embedding=emb,
                model=self.provider.get_model_name(),
                dimensions=self.provider.get_dimension(),
            )
            for pid, text, emb in zip(product_ids, texts, embeddings)
        ]
