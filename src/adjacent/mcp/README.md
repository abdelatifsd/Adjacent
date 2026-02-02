# MCP Server: Decisions, Implementation, and Workflow with Claude

This document describes the Adjacent MCP (Model Context Protocol) server: why it was built this way, how it is implemented, and how to use it with Claude Desktop.

---

## Overview

The **MCP server** exposes the Adjacent knowledge graph to LLM applications (e.g. Claude Desktop) via the Model Context Protocol. This repository implements **only the server**; the client (Claude Desktop, Cursor, or another app) runs the server as a subprocess and talks to it over STDIO.

- **What we implement:** Tools, one prompt, config, and the server process.
- **What we do not implement:** Any MCP client; the client is the application that launches and uses the server.

---

## Decisions

### Server-only, no client

We implement only the MCP server (tools, prompt, config). The client is external: Claude Desktop (or Cursor, etc.) runs `python -m adjacent.mcp.server` and communicates with it. This keeps the repo focused and lets any MCP-capable client use the same server.

### FastMCP (official MCP Python SDK)

We use the **official MCP Python SDK** (`mcp` on PyPI) and its **FastMCP** helper (`mcp.server.fastmcp`). FastMCP gives a simple API for registering tools and prompts and for running the server with a chosen transport. We do not use the separate third-party `fastmcp` package.

### STDIO transport for Claude Desktop

The server runs with **STDIO** transport (`mcp.run(transport="stdio")`). Claude Desktop spawns the server process and talks over stdin/stdout. No HTTP server or port is required for local Claude Desktop use.

### Same config as the API

MCP uses the **same environment variables** as the FastAPI app (`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `REDIS_URL`, etc.). `mcp/config.py` builds `AsyncConfig` from env; no separate MCP config file. This avoids drift and keeps one source of truth for Neo4j and Redis.

### Logging to stderr only

With STDIO, **stdout is reserved for the MCP protocol**. All logging is sent to **stderr** (StreamHandler on `sys.stderr`) so that debug and error messages do not break the protocol. The `adjacent.mcp` logger is configured in `server.py` and does not propagate to the root logger.

### Minimal first version: two tools, one prompt

We started with the minimum useful surface:

- **Tools:** `get_product`, `get_product_recommendations` (read-only; no resources yet).
- **Prompt:** `find_recommendations` (instruction template for the LLM).

Recommendations are fetched with **inference skipped** (`skip_inference=True`) so that MCP queries stay fast and do not enqueue LLM jobs. The REST API remains the place for full inference behavior.

### Shared context via module-level variable

Tools need access to `QueryService` and `Neo4jContext`. These are created in `run_server()` and stored in a **module-level `_mcp_context`** dict, which tools read when invoked. Registration happens at import time (decorators); the context is set once before `mcp.run()`. This avoids threading or dependency-injection complexity for a single-process, single-user (Claude Desktop) setup.

### Cleanup on exit

`atexit` is used to close `QueryService` and `Neo4jContext` when the process exits, so connections are released cleanly when Claude Desktop stops the server.

---

## Implementation

### Layout

```
src/adjacent/mcp/
├── __init__.py                          # Package docstring; run via python -m adjacent.mcp.server
├── config.py                            # get_config() -> AsyncConfig from env
├── server.py                            # FastMCP app, tools, prompt, run_server(), __main__
├── README.md                            # This file
└── claude_desktop_config.example.json   # Copy to Claude config; replace path
```

### Dependencies

- **`mcp`** (PyPI): added in `pyproject.toml` as `mcp>=1.0.0`. All other needs (Neo4j, Redis, Pydantic, etc.) are already project dependencies.

### Config (`mcp/config.py`)

- **`get_config() -> AsyncConfig`**  
  Builds config from env (same defaults and vars as the API). Used by `server.py` to create `QueryService` and `Neo4jContext`.

### Server (`mcp/server.py`)

- **FastMCP instance:** `mcp = FastMCP("adjacent-kg", json_response=True)`.
- **Context:** `_mcp_context` holds `query_service` and `neo4j_ctx`; set in `run_server()` before `mcp.run(transport="stdio")`.
- **Tools:**
  - **`get_product(product_id: str)`** — Fetches a product by ID via `Neo4jContext.fetch_product()`. Raises `ValueError` if not found.
  - **`get_product_recommendations(product_id: str, top_k: int = 10)`** — Returns recommendations via `QueryService.query(..., skip_inference=True)`. `top_k` is clamped to 1–100.
  - **`get_products_by_edge_count(limit: int = 20)`** — Returns products ordered by number of RECOMMENDATION edges (most connected first). Returns list of `{product_id, edge_count}`; `limit` clamped to 1–500.
- **Prompt:**
  - **`find_recommendations(product_id: str)`** — Returns a short instruction string telling the LLM to call the two tools and then summarize and explain the recommendations.
- **Entrypoint:** `if __name__ == "__main__": run_server()` so the server is run as `python -m adjacent.mcp.server`.

### Neo4j result serialization (data layer, not MCP-specific)

Product and edge data returned by `fetch_product`, `fetch_products`, `get_edge`, and `get_neighbors` is made JSON-serializable by the shared **`adjacent.db.serialization`** module (Neo4j temporal types → ISO strings). That logic lives in `adjacent.db` and `adjacent.stores.neo4j_edge_store`; the MCP server uses it indirectly via `Neo4jContext` and `QueryService`. The same sanitization applies to the REST API and any other consumer of those methods.

### Running the server

From the **repository root**, with Neo4j and Redis available and env set (or defaults):

```bash
uv run python -m adjacent.mcp.server
```

Or with system Python (and dependencies installed):

```bash
python -m adjacent.mcp.server
```

The process reads/writes MCP on stdin/stdout and logs to stderr. It runs until the client (e.g. Claude Desktop) stops it.

---

## Workflow with Claude Desktop

### 1. Prerequisites

- Adjacent repo cloned and dependencies installed (`uv sync` or equivalent).
- Neo4j and Redis running (e.g. via project `docker-compose` or local install).
- Environment variables set for Neo4j and Redis (or use the defaults in `config.py`).

### 2. Config file location

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

Create the file if it does not exist. It must be valid JSON.

### 3. Use the provided config artifact (recommended)

This directory includes a **working** Claude Desktop config that has been debugged and tested:

- **File:** `claude_desktop_config.example.json`

**Steps:**

1. Copy the contents of `claude_desktop_config.example.json` into your Claude Desktop config file (see locations above). If you already have other MCP servers, merge the `mcpServers` entry for `adjacent-kg` into your existing config.
2. Replace **every** `/path/to/adjacent` with your **absolute path** to the Adjacent repo (e.g. `/Users/you/Desktop/adjacent`). There are two places: `command` (venv Python) and `cwd`.
3. Optionally adjust `env` (NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD) if your Neo4j uses different credentials.
4. Save the file, then **fully quit and reopen Claude Desktop**.

This config uses the project’s **venv Python** (`/path/to/adjacent/.venv/bin/python`) and **only** the args `["-m", "adjacent.mcp.server"]`, which avoids PATH and pyenv issues when Claude spawns the subprocess.

### 4. Restart Claude Desktop

Fully quit Claude Desktop and reopen it so it reloads the config and starts the MCP server when needed.

### 5. Using the server in a conversation

- Claude will list available tools (e.g. **adjacent-kg** with `get_product`, `get_product_recommendations`, `get_products_by_edge_count`) when relevant.
- You can ask things like:
  - “Get product X and its recommendations from the Adjacent graph.”
  - “Use the adjacent-kg tools to find recommendations for product ID abc123 and explain them.”
  - “Which products have the most recommendation edges?”
- You can use the **find_recommendations** prompt (if your client exposes prompts) by providing a product ID; Claude will receive the instruction template and can then call the tools and summarize.

If the server fails to start, check Claude Desktop’s logs (or run `uv run python -m adjacent.mcp.server` in a terminal and watch stderr) and verify Neo4j/Redis and `cwd`/`env`.

### 6. Quick checklist

| Step | Action |
|------|--------|
| 1 | Neo4j and Redis running; env set or defaults OK |
| 2 | Copy `claude_desktop_config.example.json` into Claude config (or merge `adjacent-kg` into existing config) |
| 3 | Replace `/path/to/adjacent` with your repo path in `command` and `cwd` |
| 4 | Save config; fully restart Claude Desktop |
| 5 | Start a new conversation and ask for product/recommendations or top products by edge count |

---

## See also

- **`docs/mcp_assessment.md`** — Broader MCP plan, optional tools/resources, and phased rollout.
- **`server.py`** — Inline comment block at the bottom with a compact Claude Desktop config snippet.
