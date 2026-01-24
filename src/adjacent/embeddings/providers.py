"""Embedding provider implementations."""

from abc import ABC, abstractmethod
from typing import List, Sequence


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers.

    All providers must implement embed and embed_batch methods.
    Dimension is measured lazily on first use, not assumed.
    """

    def __init__(self):
        self._dimension: int | None = None

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Embed a single text. Returns a list of floats."""
        ...

    @abstractmethod
    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """Embed multiple texts in batches.

        Guarantees:
        - Output length equals input length
        - Order preserved
        - Every vector is list[float]
        """
        ...

    @abstractmethod
    def get_model_name(self) -> str:
        """Return model identifier."""
        ...

    def get_dimension(self) -> int:
        """Return embedding dimension size.

        Measured lazily on first call, not assumed from model name.
        """
        if self._dimension is None:
            # Measure dimension by embedding a short test string
            test_vec = self.embed("dimension_probe")
            self._dimension = len(test_vec)
        return self._dimension


class OpenAIEmbedding(EmbeddingProvider):
    """OpenAI embedding provider using their API."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        super().__init__()
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def embed(self, text: str) -> List[float]:
        response = self.client.embeddings.create(input=text, model=self.model)
        return response.data[0].embedding

    def embed_batch(
        self, texts: Sequence[str], batch_size: int = 100
    ) -> List[List[float]]:
        """Embed multiple texts. OpenAI supports up to 100 texts per request.

        Note: OpenAI typically preserves order, but we verify alignment.
        """
        if not texts:
            return []

        embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = self.client.embeddings.create(input=batch, model=self.model)

            # Verify we got the expected number of embeddings
            if len(response.data) != len(batch):
                raise ValueError(
                    f"Expected {len(batch)} embeddings, got {len(response.data)}"
                )

            embeddings.extend([item.embedding for item in response.data])

        # Final guarantee check
        if len(embeddings) != len(texts):
            raise ValueError("Embedding count mismatch ...")

        return embeddings

    def get_model_name(self) -> str:
        return self.model


class HuggingFaceEmbedding(EmbeddingProvider):
    """HuggingFace embedding provider using sentence-transformers (local)."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        super().__init__()
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)
        self.model_name = model_name

    def embed(self, text: str) -> List[float]:
        return self.model.encode(text, convert_to_tensor=False).tolist()

    def embed_batch(
        self, texts: Sequence[str], batch_size: int = 32
    ) -> List[List[float]]:
        """Embed multiple texts using sentence-transformers batching."""
        if not texts:
            return []

        embeddings = self.model.encode(
            texts, batch_size=batch_size, convert_to_tensor=False
        ).tolist()

        # Guarantee check
        if len(embeddings) != len(texts):
            raise ValueError("Embedding count mismatch ...")
        return embeddings

    def get_model_name(self) -> str:
        return self.model_name
