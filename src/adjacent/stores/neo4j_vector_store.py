"""Neo4j vector storage and similarity search.

This provides vector-only similarity search. For hybrid search combining
vector similarity with keyword relevance, you would need to add a fulltext
index and score blending (not yet implemented).
"""

import logging
from typing import List, Dict, Any, Tuple
import re
from neo4j import GraphDatabase


logger = logging.getLogger(__name__)


class Neo4jVectorStore:
    """Handles vector storage and vector-based similarity search in Neo4j.
    
    Note: This implements pure vector similarity search. True "hybrid search"
    (vector + keyword/fulltext) requires additional indices and score blending.
    """

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        index_name: str = "product_embedding",
    ):
        """Initialize vector store.
        
        Args:
            uri: Neo4j connection URI
            user: Neo4j username
            password: Neo4j password
            index_name: Name for the vector index (stored for consistency)
        """
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", index_name):
            raise ValueError("index_name must be a valid Neo4j identifier (letters, digits, underscore)")

        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        
        self.index_name = index_name


    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - closes driver."""
        self.close()

    def close(self):
        """Close the Neo4j driver connection. Idempotent."""
        if self.driver:
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
        self, query_embedding: List[float], top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """Find similar products using vector similarity.
        
        Args:
            query_embedding: Query vector
            top_k: Number of results to return
            
        Returns:
            List of dicts with product (node projection) and similarity score.
            Product includes: id, title, description, category, brand, tags.
        """
        cypher = """
        CALL db.index.vector.queryNodes($index_name, $top_k, $embedding)
        YIELD node, score
        RETURN node {
            .id,
            .title,
            .description,
            .category,
            .brand,
            .tags,
            .price,
            .currency
        } AS product,
        score
        ORDER BY score DESC
        """
        with self.driver.session() as session:
            result = session.run(
                cypher,
                index_name=self.index_name,  # Use stored index_name
                embedding=query_embedding,
                top_k=top_k,
            )
            return [dict(record) for record in result]
