# Async Edge Inference Architecture

> Decoupling query latency from LLM inference using Redis Queue (RQ).

## Overview

Adjacent supports two recommendation modes:

1. **Synchronous** (`Recommender`) — Blocks on LLM inference, returns enriched results
2. **Asynchronous** (`QueryService`) — Returns immediately, enqueues inference for background processing

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           User Query                                     │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                     QueryService (Fast Path)                     │    │
│  │                                                                  │    │
│  │   1. Query existing graph edges                                  │    │
│  │   2. Supplement with vector similarity (if needed)               │    │
│  │   3. Enqueue inference task → Redis/RQ                           │    │
│  │   4. Return immediately (< 100ms)                                │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              │ (async via RQ)                            │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                     Redis Queue                                  │    │
│  │   Queue: adjacent_inference                                      │    │
│  │   Task: infer_edges(anchor_id, candidate_ids, config)            │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                     RQ Worker                                    │    │
│  │                                                                  │    │
│  │   1. Fetch anchor + candidates from Neo4j                        │    │
│  │   2. Project to LLMProductView                                   │    │
│  │   3. Call LLM for edge patches                                   │    │
│  │   4. Materialize and store edges                                 │    │
│  │   5. Update anchor's last_inference_at                           │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. QueryService (`src/adjacent/async_inference/query_service.py`)

Fast-path query handler that never blocks on LLM.

```python
from adjacent.async_inference import QueryService, AsyncConfig

config = AsyncConfig(
    redis_url="redis://localhost:6379/0",
    neo4j_uri="bolt://localhost:7688",
    openai_api_key="sk-...",  # For async inference
)

with QueryService(config) as svc:
    result = svc.query("product_123")
    
    print(result.from_graph)        # Existing edges
    print(result.from_vector)       # Vector similarity fallback
    print(result.inference_status)  # "enqueued", "complete", or "skipped"
    print(result.job_id)            # RQ job ID (if enqueued)
```

**QueryResult fields:**
- `recommendations` — List of recommendations (mixed graph + vector)
- `from_graph` — Count from existing edges
- `from_vector` — Count from vector similarity
- `inference_status` — "complete" | "enqueued" | "skipped"
- `job_id` — RQ job ID for status tracking

### 2. Inference Task (`src/adjacent/async_inference/tasks.py`)

The RQ task that processes inference in the background.

```python
def infer_edges(
    anchor_id: str,
    candidate_ids: List[str],
    config_dict: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Returns:
        {
            "anchor_id": "...",
            "edges_created": 5,
            "edges_reinforced": 2,
            "error": None  # or error message
        }
    """
```

### 3. Configuration (`src/adjacent/async_inference/config.py`)

Shared config for QueryService and workers.

```python
@dataclass
class AsyncConfig:
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "adjacent_inference"
    
    # Neo4j
    neo4j_uri: str = "bolt://localhost:7688"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "adjacent123"
    
    # LLM
    openai_api_key: Optional[str] = None
    llm_model: str = "gpt-4o-mini"
    
    # ...
```

## Running

### Start Infrastructure

```bash
# Start Redis (via Docker)
make redis-start

# Start Neo4j (if not running)
make neo4j-start
```

### Start Worker

```bash
# In a separate terminal
make worker
```

This starts an RQ worker listening on the `adjacent_inference` queue.

### Query with Async Inference

```bash
# Via Makefile
make query-async PRODUCT_ID=your_product_id

# Or programmatically
from adjacent.async_inference import QueryService, AsyncConfig

config = AsyncConfig(openai_api_key=os.environ["OPENAI_API_KEY"])
with QueryService(config) as svc:
    result = svc.query("product_id")
```

### Monitor Queue

```bash
# Queue status
make queue-status

# Dashboard (requires: pip install rq-dashboard)
make worker-dashboard
# Opens http://localhost:9181
```

## Comparison

| Aspect | Sync (`Recommender`) | Async (`QueryService`) |
|--------|---------------------|------------------------|
| First query latency | 300-2000ms | < 100ms |
| LLM failure impact | Blocks request | Graceful (returns vector results) |
| Result quality | Best available | May be "thin" initially |
| Consistency | Immediate | Eventually consistent |
| Use case | Batch processing, testing | Production queries |

## How It Works

### First Query for a Product

1. **QueryService** receives query for product X
2. Checks graph → no edges exist yet
3. Falls back to vector similarity → returns top-K similar products
4. Enqueues `infer_edges(X, [candidates])` to Redis
5. Returns immediately with vector-based recommendations
6. **Worker** picks up job, calls LLM, stores edges
7. Next query for X returns graph-based recommendations

### Subsequent Queries

1. **QueryService** receives query for product X
2. Checks graph → edges exist from previous inference
3. Returns graph-based recommendations (high confidence)
4. May enqueue inference for any new vector candidates not yet connected

## Tracking Inference

Products track their inference history:

```cypher
// Set by worker after successful inference
MATCH (p:Product {id: $anchor_id})
SET p.last_inference_at = datetime(),
    p.inference_count = coalesce(p.inference_count, 0) + 1
```

Check job status programmatically:

```python
status = svc.get_job_status(result.job_id)
# {"job_id": "...", "status": "finished", "result": {...}}
```

## Future Enhancements

1. **Job deduplication** — Skip if same anchor+candidates recently enqueued
2. **Priority queues** — User-triggered vs background warmup
3. **Batch inference** — Combine multiple anchors into one LLM call
4. **Result caching** — Cache frequent queries in Redis
