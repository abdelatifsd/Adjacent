# src/adjacent/api/app.py
"""
FastAPI reference server for Adjacent QueryService.

Exposes querying and job status endpoints with minimal overhead.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from adjacent.async_inference.config import AsyncConfig
from adjacent.async_inference.query_service import QueryService
from adjacent.api.routes import router

# Configure logging to console, file, and Loki
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
log_file = log_dir / "api.log"

# Root logger for general application logs (with prefix format)
root_handlers = [
    logging.StreamHandler(sys.stdout),  # Console output
    logging.FileHandler(log_file, mode="a"),  # File output
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=root_handlers,
)

# Configure "adjacent" logger for metrics (clean JSON format, direct to Loki)
# This logger is used by commons.metrics for structured metrics
try:
    from commons.loki_handler import LokiHandler

    metrics_logger = logging.getLogger("adjacent")
    metrics_logger.setLevel(logging.INFO)
    metrics_logger.propagate = False  # Don't propagate to root (avoid duplicate)

    # Loki handler for direct push (clean JSON, no prefix)
    loki_handler = LokiHandler(
        url=os.getenv("LOKI_URL", "http://localhost:3100/loki/api/v1/push"),
        job="api",
        enabled=os.getenv("LOKI_ENABLED", "true").lower() in ("true", "1", "yes", "on"),
    )
    loki_handler.setFormatter(logging.Formatter("%(message)s"))  # Clean JSON only
    metrics_logger.addHandler(loki_handler)

    # Also keep file handler for metrics (with clean format)
    metrics_file_handler = logging.FileHandler(log_file, mode="a")
    metrics_file_handler.setFormatter(logging.Formatter("%(message)s"))  # Clean JSON
    metrics_file_handler.setLevel(logging.INFO)
    metrics_logger.addHandler(metrics_file_handler)

except ImportError:
    # Loki handler not available (requests not installed)
    import sys

    print(
        "Warning: Loki handler not available. Install requests: pip install requests",
        file=sys.stderr,
    )
except Exception as e:
    # If Loki handler fails, continue without it
    import sys

    print(f"Warning: Failed to configure Loki handler: {e}", file=sys.stderr)

logger = logging.getLogger(__name__)


def build_config_from_env() -> AsyncConfig:
    """
    Build AsyncConfig from environment variables.

    Environment variables:
        NEO4J_URI: Neo4j connection URI (default: bolt://localhost:7688)
        NEO4J_USER: Neo4j username (default: neo4j)
        NEO4J_PASSWORD: Neo4j password (default: adjacent123)
        REDIS_URL: Redis connection URL (default: redis://localhost:6379/0)
        EMBEDDING_PROVIDER: Embedding provider (default: huggingface)
        EMBEDDING_MODEL: Embedding model name (optional)
        OPENAI_API_KEY: OpenAI API key (optional, required for inference)
        LLM_MODEL: LLM model name (default: gpt-4o-mini)

    Returns:
        AsyncConfig instance

    Note:
        If OPENAI_API_KEY is not set, async inference will be automatically
        disabled (inference_status will be "skipped" in query responses).
    """
    config = AsyncConfig(
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7688"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", "adjacent123"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "huggingface"),
        embedding_model=os.getenv("EMBEDDING_MODEL"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
    )

    # Log inference availability
    if config.openai_api_key:
        logger.info("Async inference enabled (OPENAI_API_KEY present)")
    else:
        logger.info("Async inference disabled (OPENAI_API_KEY not set)")

    return config


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    Startup:
        - Build config from environment
        - Initialize Neo4jContext and QueryService
        - Store on app.state

    Shutdown:
        - Close QueryService and Neo4jContext

    Raises:
        RuntimeError: If critical services (Neo4j, Redis) are unavailable
    """
    # Startup
    logger.info("Starting Adjacent API server...")

    try:
        config = build_config_from_env()
        query_service = QueryService(config)

        # Verify connections by testing a simple operation
        # This will fail fast if Neo4j or Redis are unreachable
        logger.info("Verifying service connections...")

        # Store on app state
        app.state.query_service = query_service
        app.state.config = config

        logger.info("Adjacent API server started successfully")
        logger.info("Neo4j: %s", config.neo4j_uri)
        logger.info("Redis: %s", config.redis_url)
        logger.info("Embedding provider: %s", config.embedding_provider)

    except Exception as e:
        logger.exception("Failed to start API server: %s", e)
        raise RuntimeError(f"API startup failed: {e}") from e

    yield

    # Shutdown
    logger.info("Shutting down Adjacent API server...")

    try:
        query_service.close()
        logger.info("Adjacent API server shutdown complete")
    except Exception as e:
        logger.exception("Error during shutdown: %s", e)


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        FastAPI application instance
    """
    app = FastAPI(
        title="Adjacent API",
        description="Reference server for Adjacent QueryService",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Include routes
    app.include_router(router)

    return app


# Create the default app instance
app = create_app()
