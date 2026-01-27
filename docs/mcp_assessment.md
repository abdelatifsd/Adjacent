# MCP Layer Assessment for Adjacent Knowledge Graph

## Executive Summary

This document assesses the effort required to add a Model Context Protocol (MCP) layer to the Adjacent project, enabling third-party LLMs to interact with the Neo4j knowledge graph.

**Estimated Effort: 2-3 weeks** (assuming 1 developer, full-time)

## Understanding MCP

Model Context Protocol (MCP) is an open protocol that standardizes how AI applications connect to external data sources and tools. MCP servers expose three main capabilities:

1. **Tools**: Functions the LLM can actively call (e.g., query products, find relationships)
2. **Resources**: Passive read-only data sources (e.g., graph schema, product catalogs)
3. **Prompts**: Pre-built instruction templates for common tasks

## Current Project Architecture

### Knowledge Graph Structure

**Nodes:**
- **Product** nodes with properties:
  - Core: `id`, `title`, `description`, `category`, `brand`, `tags`, `price`, `currency`
  - Runtime: `embedding`, `embedding_dim`, `embedding_model`, `embedding_updated_at`
  - Query tracking: `total_query_count`, `last_inference_at`, `inference_count`

**Relationships:**
- **RECOMMENDATION** relationships (single Neo4j relationship type) with properties:
  - `edge_id`, `edge_type` (SIMILAR_TO, COMPLEMENTS, SUBSTITUTE_FOR, OFTEN_USED_WITH)
  - `confidence_0_to_1`, `anchors_seen[]`, `status` (PROPOSED, ACTIVE, DISPUTED)
  - `created_at`, `last_reinforced_at`, `notes`, `edge_props_json`
  - Provenance: `created_kind`, `created_under_anchor_id`, `created_in_job_id`

### Existing API Layer

The project already has:
- FastAPI-based REST API (`src/adjacent/api/`)
- Query service with graph + vector search (`QueryService`)
- Neo4j stores (`Neo4jEdgeStore`, `Neo4jVectorStore`)
- Well-defined schemas for Products and Edges

## MCP Implementation Requirements

### 1. MCP Server Implementation

**Location:** New module `src/adjacent/mcp/` or integrate into existing API

**Core Components Needed:**

#### A. MCP Tools (Primary Interface)

**Essential Tools:**
1. **`get_product`** - Fetch a product by ID
   - Input: `product_id: string`
   - Output: Product details with all properties
   - Uses: `Neo4jContext.fetch_product()`

2. **`get_product_recommendations`** - Get recommendations for a product
   - Input: `product_id: string`, `top_k: int` (optional, default 10)
   - Output: List of recommendations with source (graph/vector), confidence, edge_type
   - Uses: `QueryService.query()` (or direct store access)

3. **`find_similar_products`** - Vector similarity search
   - Input: `query_text: string` or `product_id: string`, `top_k: int`
   - Output: Similar products with similarity scores
   - Uses: `Neo4jVectorStore.similarity_search()`

4. **`get_product_neighbors`** - Get graph neighbors
   - Input: `product_id: string`, `edge_type: string` (optional), `min_confidence: float` (optional), `limit: int`
   - Output: Connected products with relationship details
   - Uses: `Neo4jEdgeStore.get_neighbors()`

5. **`get_edge_details`** - Get relationship details between two products
   - Input: `from_id: string`, `to_id: string` (or `edge_id: string`)
   - Output: Edge properties including confidence, status, notes
   - Uses: `Neo4jEdgeStore.get_edge()`

6. **`search_products`** - Search products by attributes
   - Input: `category: string` (optional), `brand: string` (optional), `tags: string[]` (optional), `limit: int`
   - Output: Matching products
   - Uses: Custom Cypher queries

**Advanced Tools (Optional):**
7. **`get_graph_statistics`** - Graph metrics
   - Input: None or `product_id: string` (for local stats)
   - Output: Node count, edge count, coverage stats
   - Uses: System status endpoint logic

8. **`explore_product_context`** - Multi-hop exploration
   - Input: `product_id: string`, `hops: int` (default 2), `edge_types: string[]` (optional)
   - Output: Subgraph around product
   - Uses: Custom Cypher traversal queries

#### B. MCP Resources (Read-only Data)

1. **`graph://schema`** - Knowledge graph schema
   - Product node schema
   - Edge relationship schema
   - Edge types enumeration
   - Uses: JSON schemas from `schemas/` directory

2. **`graph://product/{product_id}`** - Individual product resource
   - Uses: `Neo4jContext.fetch_product()`

3. **`graph://stats`** - Graph statistics resource
   - Uses: System status endpoint

#### C. MCP Prompts (Pre-built Templates)

1. **`analyze_product_relationships`** - Template for analyzing product connections
2. **`find_recommendations`** - Template for recommendation queries
3. **`explore_product_category`** - Template for category exploration

### 2. Integration Points

**Option A: Standalone MCP Server (Recommended)**
- Separate process running MCP server
- Connects to same Neo4j instance
- Can run alongside existing FastAPI server
- Uses Python MCP SDK (`mcp` package)

**Option B: Integrated into FastAPI**
- Add MCP endpoint to existing FastAPI app
- HTTP-based MCP transport
- Shares same application lifecycle

**Option C: HTTP-to-STDIO Bridge**
- FastAPI endpoint that spawns MCP server process
- More complex but allows both REST and MCP access

### 3. Dependencies

**New Dependencies:**
```toml
"mcp>=1.2.0"  # Python MCP SDK
```

**Existing Dependencies (Already Present):**
- `fastapi>=0.115.0` ✓
- `neo4j>=5.27.0` ✓
- `pydantic>=2.12.5` ✓

### 4. Code Structure

```
src/adjacent/
├── mcp/
│   ├── __init__.py
│   ├── server.py          # Main MCP server setup
│   ├── tools.py           # Tool implementations
│   ├── resources.py       # Resource handlers
│   ├── prompts.py         # Prompt templates
│   └── config.py          # MCP-specific configuration
├── api/                   # Existing FastAPI (unchanged)
└── ...
```

## Implementation Breakdown

### Phase 1: Core MCP Server Setup (3-4 days)
- [ ] Install MCP SDK
- [ ] Create basic server structure
- [ ] Set up STDIO transport
- [ ] Implement server initialization
- [ ] Add basic error handling and logging

### Phase 2: Essential Tools (5-6 days)
- [ ] `get_product` tool
- [ ] `get_product_recommendations` tool
- [ ] `find_similar_products` tool
- [ ] `get_product_neighbors` tool
- [ ] `get_edge_details` tool
- [ ] Input validation and error handling
- [ ] Unit tests for each tool

### Phase 3: Resources and Prompts (2-3 days)
- [ ] Graph schema resource
- [ ] Product resource handler
- [ ] Statistics resource
- [ ] Prompt templates
- [ ] Resource URI routing

### Phase 4: Advanced Features (3-4 days)
- [ ] `search_products` tool (Cypher query builder)
- [ ] `get_graph_statistics` tool
- [ ] `explore_product_context` tool (multi-hop queries)
- [ ] Query optimization
- [ ] Caching layer (optional)

### Phase 5: Testing and Documentation (2-3 days)
- [ ] Integration tests with MCP Inspector
- [ ] Test with Claude Desktop
- [ ] Documentation for tool usage
- [ ] Example queries and use cases
- [ ] Performance testing

### Phase 6: Deployment and Configuration (1-2 days)
- [ ] Configuration management
- [ ] Environment variable setup
- [ ] Docker integration (if needed)
- [ ] Deployment documentation

## Technical Considerations

### 1. Connection Management
- **Reuse existing Neo4j driver** from `Neo4jContext`
- **Share QueryService** or create lightweight wrapper
- **Connection pooling** already handled by Neo4j driver

### 2. Authentication & Authorization
- MCP servers typically don't handle auth (client-side)
- Consider adding API keys if exposing publicly
- Rate limiting may be needed

### 3. Error Handling
- Map Neo4j errors to MCP error codes
- Provide helpful error messages
- Log errors appropriately (stderr for STDIO)

### 4. Performance
- MCP tools should be fast (< 1s for simple queries)
- Consider caching for schema resources
- Async operations where possible

### 5. Logging
- **Critical**: For STDIO transport, never use `print()` (writes to stdout)
- Use `logging` module writing to stderr
- Follow existing logging patterns in project

## Example MCP Tool Implementation

```python
from mcp.server.fastmcp import FastMCP
from adjacent.db import Neo4jContext
from adjacent.stores import Neo4jEdgeStore, Neo4jVectorStore

mcp = FastMCP("adjacent-kg")

# Initialize shared connections
neo4j_ctx = Neo4jContext(...)
edge_store = Neo4jEdgeStore(...)
vector_store = Neo4jVectorStore(...)

@mcp.tool()
async def get_product(product_id: str) -> dict:
    """Get product details by ID.
    
    Args:
        product_id: The product identifier
        
    Returns:
        Product data with all properties
    """
    product = neo4j_ctx.fetch_product(product_id)
    if not product:
        raise ValueError(f"Product not found: {product_id}")
    return product

@mcp.tool()
async def get_product_recommendations(
    product_id: str,
    top_k: int = 10
) -> dict:
    """Get recommendations for a product.
    
    Args:
        product_id: Anchor product ID
        top_k: Number of recommendations (1-100)
        
    Returns:
        Recommendations with source, confidence, and edge_type
    """
    # Use existing QueryService or direct store access
    ...
```

## Deployment Options

### Option 1: Standalone Process
```bash
# Run MCP server separately
python -m adjacent.mcp.server
```

### Option 2: Docker Container
```dockerfile
# Add to existing Dockerfile or create separate one
CMD ["python", "-m", "adjacent.mcp.server"]
```

### Option 3: Systemd Service (Linux)
```ini
[Unit]
Description=Adjacent MCP Server
After=network.target

[Service]
ExecStart=/usr/bin/python -m adjacent.mcp.server
Restart=always
```

## Client Configuration Example

For Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "adjacent-kg": {
      "command": "python",
      "args": [
        "-m",
        "adjacent.mcp.server"
      ],
      "env": {
        "NEO4J_URI": "bolt://localhost:7688",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "adjacent123"
      }
    }
  }
}
```

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| MCP protocol changes | Medium | Pin SDK version, monitor updates |
| Performance issues | Medium | Implement caching, optimize queries |
| Security concerns | High | Add authentication, rate limiting |
| Complexity in tool design | Low | Start simple, iterate based on feedback |

## Success Criteria

1. ✅ MCP server connects successfully to Neo4j
2. ✅ All essential tools work correctly
3. ✅ Resources are accessible
4. ✅ Prompts provide value
5. ✅ Integration works with Claude Desktop
6. ✅ Performance is acceptable (< 1s for simple queries)
7. ✅ Documentation is complete

## Future Enhancements

- **Write Operations**: Allow LLMs to propose new edges (with approval workflow)
- **Batch Operations**: Tools for bulk queries
- **Graph Analytics**: Advanced graph algorithms exposed as tools
- **Streaming**: Real-time updates via MCP resources
- **Multi-tenancy**: Support multiple knowledge graphs

## Conclusion

Adding an MCP layer to the Adjacent project is **feasible and well-aligned** with the existing architecture. The project's clean separation of concerns, well-defined schemas, and existing Neo4j stores make it an ideal candidate for MCP integration.

**Recommended Approach:**
1. Start with standalone MCP server (Option A)
2. Implement essential tools first (Phase 1-2)
3. Test with Claude Desktop early
4. Iterate based on usage patterns

**Total Estimated Time: 2-3 weeks** for a complete, production-ready implementation.
