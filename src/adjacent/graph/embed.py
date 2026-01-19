"""Embedding orchestration for product nodes.

This module handles the embedding pipeline:
1. Fetch products from Neo4j
2. Generate embeddings
3. Store vectors with metadata

Parallel to ingest.py but for the embedding phase.
"""

import argparse
import logging
from typing import Dict, Any, List, Tuple
from neo4j import GraphDatabase

from adjacent.embeddings import HuggingFaceEmbedding, OpenAIEmbedding, EmbeddingService
from adjacent.stores import Neo4jVectorStore

logger = logging.getLogger(__name__)


# ----------------------------
# Product Fetching
# ----------------------------
def fetch_products_needing_embeddings(
    uri: str, user: str, password: str, limit: int | None = None
) -> List[Dict[str, Any]]:
    """Fetch products that need embeddings from Neo4j.
    
    Returns products where embedding IS NULL or embed_text exists but embedding doesn't.
    """
    driver = GraphDatabase.driver(uri, auth=(user, password))

    cypher = """
    MATCH (p:Product)
    WHERE p.embedding IS NULL
      AND p.embed_text IS NOT NULL
    RETURN p.id as id, p.embed_text as embed_text
    """

    if limit:
        cypher += f" LIMIT {limit}"

    with driver:
        with driver.session() as session:
            result = session.run(cypher)
            products = [dict(record) for record in result]

    return products


# ----------------------------
# Embedding Pipeline
# ----------------------------
def embed_products(
    uri: str,
    user: str,
    password: str,
    provider_name: str = "huggingface",
    api_key: str | None = None,
    model_name: str | None = None,
    limit: int | None = None,
    batch_size: int = 32,  # Reserved for future use (providers handle batching internally)
) -> Tuple[int, int]:
    """Run the embedding pipeline.
    
    Args:
        uri: Neo4j connection URI
        user: Neo4j username
        password: Neo4j password
        provider_name: "huggingface" or "openai"
        api_key: API key for OpenAI (required if provider=openai)
        model_name: Optional model override
        limit: Optional limit for number of products to embed
        batch_size: Batch size for embedding (reserved for future use)
        
    Returns:
        Tuple of (products_embedded, products_missing)
    """
    # Initialize provider
    if provider_name == "openai":
        if not api_key:
            raise ValueError("api_key required when using openai provider")
        provider = OpenAIEmbedding(api_key=api_key, model=model_name or "text-embedding-3-small")
    elif provider_name == "huggingface":
        provider = HuggingFaceEmbedding(
            model_name=model_name or "sentence-transformers/all-MiniLM-L6-v2"
        )
    else:
        raise ValueError(f"Unknown provider: {provider_name}")

    # Create service
    service = EmbeddingService(provider)
    logger.info("Using provider: %s", provider.get_model_name())

    # Fetch products
    logger.info("Fetching products from Neo4j...")
    products = fetch_products_needing_embeddings(uri, user, password, limit)
    logger.info("Found %d products needing embeddings", len(products))

    if not products:
        logger.info("No products need embeddings. Done!")
        return (0, 0)

    # Embed
    logger.info("Generating embeddings...")
    results = service.embed_products(products, text_field="embed_text")
    logger.info("Embeddings dimension: %d", results[0].dimensions)

    # Store in Neo4j
    with Neo4jVectorStore(uri, user, password) as vector_store:
        # Create index if needed
        logger.info("Creating vector index...")
        vector_store.create_vector_index(dimension=results[0].dimensions)

        # Upsert embeddings with metadata
        logger.info("Storing embeddings in Neo4j...")
        product_ids = [r.product_id for r in results]
        embeddings = [r.embedding for r in results]
        model_name = results[0].model

        updated, missing = vector_store.upsert_embeddings(
            product_ids, embeddings, model_name=model_name
        )

        logger.info("✓ Successfully embedded %d products", updated)
        if missing > 0:
            logger.warning("⚠ %d product IDs were not found", missing)

        return (updated, missing)


# ----------------------------
# CLI
# ----------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Embed products in Neo4j (parallel to ingest.py)"
    )
    p.add_argument(
        "--provider",
        choices=["huggingface", "openai"],
        default="huggingface",
        help="Embedding provider to use",
    )
    p.add_argument(
        "--model",
        help="Model name override (provider-specific)",
    )
    p.add_argument("--api-key", help="API key for OpenAI (required if provider=openai)")
    p.add_argument("--neo4j-uri", default="bolt://localhost:7688")
    p.add_argument("--neo4j-user", default="neo4j")
    p.add_argument("--neo4j-password", default="adjacent123")
    p.add_argument("--limit", type=int, help="Limit number of products to embed")
    p.add_argument("--batch-size", type=int, default=32, help="Batch size for embedding")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()

    try:
        updated, missing = embed_products(
            uri=args.neo4j_uri,
            user=args.neo4j_user,
            password=args.neo4j_password,
            provider_name=args.provider,
            api_key=args.api_key,
            model_name=args.model,
            limit=args.limit,
            batch_size=args.batch_size,
        )

        logger.info("✓ Embedded %d products", updated)
        if missing > 0:
            logger.warning("⚠ %d product IDs not found", missing)

    except Exception as e:
        logger.error("Embedding failed: %s", e)
        raise


if __name__ == "__main__":
    main()
