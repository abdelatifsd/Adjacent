"""MCP server configuration.

Uses the same environment variables as the API so the MCP server
can connect to Neo4j and Redis without separate config.
"""

from __future__ import annotations

import os

from adjacent.async_inference.config import AsyncConfig


def get_config() -> AsyncConfig:
    """Build AsyncConfig from environment variables.

    Same env vars as the API: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    REDIS_URL, EMBEDDING_PROVIDER, OPENAI_API_KEY, etc.
    """
    return AsyncConfig(
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7688"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", "adjacent123"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "huggingface"),
        embedding_model=os.getenv("EMBEDDING_MODEL"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
    )
