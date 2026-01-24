"""Neo4j vector storage and similarity search.

This provides vector-only similarity search. For hybrid search combining
vector similarity with keyword relevance, you would need to add a fulltext
index and score blending (not yet implemented).
"""

import logging
from typing import List, Dict, Any, Tuple, Optional
import re
from neo4j import GraphDatabase, Driver


logger = logging.getLogger(__name__)


class Neo4jVectorStore:
    """Handles vector storage and vector-based similarity search in Neo4j.
    
    Note: This implements pure vector similarity search. True "hybrid search"
    (vector + keyword/fulltext) requires additional indices and score blending.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        index_name: str = "product_embedding",
        driver: Optional[Driver] = None,
    ):
        """Initialize vector store.

        Args:
            uri: Neo4j connection URI (required if driver not provided)
            user: Neo4j username (required if driver not provided)
            password: Neo4j password (required if driver not provided)
            index_name: Name for the vector index (stored for consistency)
            driver: Optional shared Neo4j driver. If provided, uri/user/password are ignored.
                    When providing a driver, the caller is responsible for closing it.
        """
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", index_name):
            raise ValueError("index_name must be a valid Neo4j identifier (letters, digits, underscore)")

        self._owns_driver = driver is None
        if driver is not None:
            self.driver = driver
        else:
            if not all([uri, user, password]):
                raise ValueError("Either driver or (uri, user, password) must be provided")
            self.driver = GraphDatabase.driver(uri, auth=(user, password))

        self.index_name = index_name


    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - closes driver."""
        self.close()

    def close(self):
        """Close the Neo4j driver connection if this instance owns it. Idempotent."""
        if self._owns_driver and self.driver:
            self.driver.close()
            self.driver = None

    def create_vector_index(self, dimension: int) -> None:
        """Create a vector index for similarity search.
        
        Args:
            dimension: Embedding vector dimension size
        """
        create_index = f"""
        CREATE VECTOR INDEX {self.index_name} IF NOT EXISTS
        FOR (p:Product)
        ON p.embedding
        OPTIONS {{
          indexConfig: {{
            `vector.dimensions`: {dimension},
            `vector.similarity_function`: 'cosine'
          }}
        }}
        """
        with self.driver.session() as session:
            session.run(create_index)
        logger.info("Created vector index '%s' with dimension %d", self.index_name, dimension)

    def upsert_embeddings(
        self,
        product_ids: List[str],
        embeddings: List[List[float]],
        model_name: str | None = None,
        batch_size: int = 500,
    ) -> Tuple[int, int]:
        """Update products with embeddings and metadata.
        
        Args:
            product_ids: List of product IDs
            embeddings: List of embedding vectors (must align with product_ids)
            model_name: Model identifier (stored for reproducibility)
            batch_size: Number of products to update per batch
            
        Returns:
            Tuple of (successful_updates, missing_ids_count)
        """
        if len(product_ids) != len(embeddings):
            raise ValueError(
                f"ID count ({len(product_ids)}) must match embedding count ({len(embeddings)})"
            )

        # Determine dimension from first embedding
        first_dim = len(embeddings[0])
        if any(len(e) != first_dim for e in embeddings):
            raise ValueError("Embedding dimension mismatch within batch")
        dimension = first_dim

        cypher = """
        UNWIND $rows AS row
        MATCH (p:Product {id: row.id})
        SET p.embedding = row.embedding,
            p.embedding_dim = row.dimension,
            p.embedding_updated_at = datetime({timezone: 'UTC'})
        FOREACH (_ IN CASE WHEN row.model IS NULL THEN [] ELSE [1] END |
          SET p.embedding_model = row.model
        )
        RETURN count(p) as updated
        """

        total_updated = 0
        expected_total = len(product_ids)

        with self.driver.session() as session:
            for i in range(0, len(product_ids), batch_size):
                batch_ids = product_ids[i : i + batch_size]
                batch_emb = embeddings[i : i + batch_size]
                rows = [
                    {
                        "id": pid,
                        "embedding": emb,
                        "dimension": dimension,
                        "model": model_name,
                    }
                    for pid, emb in zip(batch_ids, batch_emb)
                ]
                result = session.run(cypher, rows=rows)
                batch_updated = result.single()["updated"]
                total_updated += batch_updated

                # Warn if some IDs were not found in this batch
                if batch_updated < len(batch_ids):
                    missing = len(batch_ids) - batch_updated
                    logger.warning(
                        "Batch %d: %d product IDs not found",
                        i // batch_size + 1, missing
                    )

        missing_total = expected_total - total_updated
        if missing_total > 0:
            logger.warning(
                "Total: %d product IDs were not found in Neo4j", missing_total
            )

        logger.info("Updated %d products with embeddings", total_updated)
        return (total_updated, missing_total)

    def similarity_search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Find similar products using vector similarity.

        Args:
            query_embedding: Query vector
            top_k: Number of results to return
            fields: Optional list of product fields to return. If None, returns all standard fields.
                    Use ["id"] for minimal projection (id only).
                    Defaults to full projection: id, title, description, category, brand, tags, price, currency.

        Returns:
            List of dicts with product (node projection) and similarity score.
        """
        # NOTE: `fields` are interpolated into Cypher as map projection keys.
        # Keep this safe by validating against a whitelist and identifier regex.
        allowed_fields = {
            "id",
            "title",
            "description",
            "category",
            "brand",
            "tags",
            "price",
            "currency",
        }

        # Default to full projection for backward compatibility
        if fields is None:
            fields = ["id", "title", "description", "category", "brand", "tags", "price", "currency"]
        else:
            if not fields:
                raise ValueError("fields must be a non-empty list, or None for default projection")

            # De-duplicate while preserving order.
            seen: set[str] = set()
            normalized_fields: List[str] = []
            for f in fields:
                if not isinstance(f, str):
                    raise TypeError(f"Field names must be strings, got {type(f).__name__}")
                if f in seen:
                    continue
                seen.add(f)
                normalized_fields.append(f)
            fields = normalized_fields

            ident = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
            for f in fields:
                if not ident.fullmatch(f):
                    raise ValueError(f"Invalid field name for projection: {f!r}")
                if f not in allowed_fields:
                    raise ValueError(
                        f"Unsupported field for projection: {f!r}. "
                        f"Allowed: {sorted(allowed_fields)}"
                    )

        # Build Cypher projection dynamically
        field_projections = ", ".join(f".{field}" for field in fields)
        cypher = f"""
        CALL db.index.vector.queryNodes($index_name, $top_k, $embedding)
        YIELD node, score
        RETURN node {{
            {field_projections}
        }} AS product,
        score
        ORDER BY score DESC
        """
        with self.driver.session() as session:
            result = session.run(
                cypher,
                index_name=self.index_name,
                embedding=query_embedding,
                top_k=top_k,
            )
            return [dict(record) for record in result]
