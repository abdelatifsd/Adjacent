"""MCP server for Adjacent knowledge graph.

Uses FastMCP from the official MCP Python SDK. Designed to run under
Claude Desktop via STDIO: Claude Desktop spawns this process and
communicates over stdin/stdout.

Logging is sent to stderr only so it does not interfere with the
MCP protocol on stdout.
"""

from __future__ import annotations

import atexit
import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from adjacent.async_inference.query_service import QueryService
from adjacent.db import Neo4jContext
from adjacent.mcp.config import get_config

# ---------------------------------------------------------------------------
# Logging: stderr only (stdout is used for MCP protocol)
# ---------------------------------------------------------------------------
_log_handler = logging.StreamHandler(sys.stderr)
_log_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
_mcp_logger = logging.getLogger("adjacent.mcp")
_mcp_logger.setLevel(logging.INFO)
_mcp_logger.addHandler(_log_handler)
_mcp_logger.propagate = False

# ---------------------------------------------------------------------------
# MCP app and shared context (set at startup in __main__)
# ---------------------------------------------------------------------------
mcp = FastMCP("adjacent-kg", json_response=True)

# Populated in __main__ before mcp.run(); used by tools so they can
# access QueryService and Neo4jContext without global imports at import time.
_mcp_context: dict[str, Any] | None = None


@mcp.tool()
def get_product(product_id: str) -> dict[str, Any]:
    """Get product details by ID from the knowledge graph.

    Args:
        product_id: The product identifier.

    Returns:
        Product data (id, title, description, category, brand, tags, price, etc.).
    """
    if _mcp_context is None:
        raise RuntimeError("MCP server not initialized")
    neo4j_ctx: Neo4jContext = _mcp_context["neo4j_ctx"]
    product = neo4j_ctx.fetch_product(product_id)
    if not product:
        raise ValueError(f"Product not found: {product_id}")
    return product


@mcp.tool()
def get_product_recommendations(product_id: str, top_k: int = 10) -> dict[str, Any]:
    """Get recommendations for a product (graph + vector search).

    Args:
        product_id: Anchor product ID.
        top_k: Number of recommendations to return (1–100). Default 10.

    Returns:
        Recommendations with product_id, edge_type, confidence, source (graph/vector).
    """
    if _mcp_context is None:
        raise RuntimeError("MCP server not initialized")
    query_service: QueryService = _mcp_context["query_service"]
    top_k = max(1, min(100, top_k))
    result = query_service.query(
        product_id=product_id,
        top_k=top_k,
        skip_inference=True,
        trace_id="mcp",
    )
    return result.to_dict()


@mcp.prompt()
def find_recommendations(product_id: str) -> str:
    """Generate a prompt to find and explain recommendations for a product.

    Args:
        product_id: The product to get recommendations for.

    Returns:
        Instruction text for the LLM to use get_product and get_product_recommendations.
    """
    return (
        f"Use the get_product and get_product_recommendations tools to find "
        f"recommendations for product {product_id}. Then summarize the results "
        f"and explain why each item is recommended."
    )


def _create_context() -> dict[str, Any]:
    """Build QueryService and Neo4jContext from config. Used in __main__."""
    config = get_config()
    query_service = QueryService(config)
    neo4j_ctx = Neo4jContext(
        uri=config.neo4j_uri,
        user=config.neo4j_user,
        password=config.neo4j_password,
    )

    def _cleanup() -> None:
        query_service.close()
        neo4j_ctx.close()
        _mcp_logger.info("MCP server resources closed")

    atexit.register(_cleanup)
    return {"query_service": query_service, "neo4j_ctx": neo4j_ctx}


def run_server() -> None:
    """Run the MCP server with STDIO transport (for Claude Desktop)."""
    global _mcp_context
    _mcp_context = _create_context()
    _mcp_logger.info(
        "Starting Adjacent MCP server (stdio); Neo4j=%s",
        _mcp_context["query_service"].config.neo4j_uri,
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()


# ---------------------------------------------------------------------------
# Claude Desktop configuration (macOS)
# ---------------------------------------------------------------------------
# Add to ~/Library/Application Support/Claude/claude_desktop_config.json:
#
#   "mcpServers": {
#     "adjacent-kg": {
#       "command": "uv",
#       "args": ["run", "python", "-m", "adjacent.mcp.server"],
#       "cwd": "/path/to/adjacent",
#       "env": {
#         "NEO4J_URI": "bolt://localhost:7688",
#         "NEO4J_USER": "neo4j",
#         "NEO4J_PASSWORD": "adjacent123"
#       }
#     }
#   }
#
# Or with system Python: "command": "python", "args": ["-m", "adjacent.mcp.server"]
# Restart Claude Desktop after changing the config.
