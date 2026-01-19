"""Embedding providers and services."""

from adjacent.embeddings.providers import (
    EmbeddingProvider,
    OpenAIEmbedding,
    HuggingFaceEmbedding,
)
from adjacent.embeddings.service import EmbeddingService, EmbeddingResult

__all__ = [
    "EmbeddingProvider",
    "OpenAIEmbedding",
    "HuggingFaceEmbedding",
    "EmbeddingService",
    "EmbeddingResult",
]
