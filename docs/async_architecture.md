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
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns:
        {
            "anchor_id": "...",
            "edges_created": 5,                    # Total new edges created
            "anchor_edges_created": 3,             # Edges connecting anchor to candidates
            "candidate_edges_created": 2,          # Edges between candidates
            "edges_reinforced": 2,                 # Existing edges newly seen by this anchor
            "edges_noop_existing": 1,              # Edges already seen by this anchor
            "error": None  # or error message if failed
        }
    """
```

**Return Fields Explained:**

| Field | Description |
|-------|-------------|
| `edges_created` | Total number of new edges created (sum of anchor + candidate edges) |
| `anchor_edges_created` | New edges connecting anchor to candidates |
| `candidate_edges_created` | New edges between candidates (candidate-candidate relationships) |
| `edges_reinforced` | Existing edges where this anchor was added to `anchors_seen` |
| `edges_noop_existing` | Existing edges where this anchor was already in `anchors_seen` |
| `error` | Error message if inference failed, null otherwise |

**Edge Lifecycle:**
1. **Created** - Edge doesn't exist, LLM suggests it
2. **Reinforced** - Edge exists, but this anchor hasn't seen it before (strengthens confidence)
3. **No-op** - Edge exists and this anchor already observed it (no change)

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

    # Embedding
    embedding_provider: str = "huggingface"
    embedding_model: Optional[str] = None

    # LLM
    openai_api_key: Optional[str] = None
    llm_model: str = "gpt-4o-mini"

    # Query settings
    top_k_candidates: int = 10
    max_recommendations: int = 10

    # Worker settings
    job_timeout: int = 300  # 5 minutes max per job

    # Endpoint reinforcement settings
    allow_endpoint_reinforcement: bool = True
    endpoint_reinforcement_threshold: int = 5
    endpoint_reinforcement_max_confidence: float = 0.70
```

**Key Configuration Options:**

| Setting | Default | Description |
|---------|---------|-------------|
| `embedding_provider` | `"huggingface"` | Embedding provider (huggingface or openai) |
| `top_k_candidates` | `10` | Number of candidates to consider for inference |
| `allow_endpoint_reinforcement` | `True` | Enable reinforcement for low-confidence edges |
| `endpoint_reinforcement_threshold` | `5` | Only reinforce if `anchors_seen` count < this |
| `endpoint_reinforcement_max_confidence` | `0.70` | Don't reinforce if confidence >= this |

**Endpoint Reinforcement:**
When enabled, the system will re-infer edges for endpoints (candidates) that have low observation counts or confidence. This helps strengthen weak edges and improve recommendation quality over time.

## Running

### Docker Compose (Recommended)

```bash
# Start everything (Neo4j, Redis, API, Worker, Monitoring)
make dev
```

This single command:
- Starts all infrastructure (Neo4j, Redis)
- Ingests demo data and embeds products
- Starts API server and RQ worker
- Starts monitoring stack (Grafana, Loki)

### Native Python (Advanced)

For debugging or development with more control:

```bash
# Terminal 1: Infrastructure
make reset-full

# Terminal 2: API server
make api-start

# Terminal 3: Worker
make worker
```

The worker listens on the `adjacent_inference` queue and processes inference tasks.

### Query with Async Inference

```bash
# Via API (after running 'make dev')
curl http://localhost:8000/v1/query/your_product_id | jq

# Or programmatically
from adjacent.async_inference import QueryService, AsyncConfig

config = AsyncConfig(openai_api_key=os.environ["OPENAI_API_KEY"])
with QueryService(config) as svc:
    result = svc.query("product_id")
```

### Monitor Services

```bash
# View all service logs
make dev-logs

# Check service health
make dev-status

# View Grafana dashboard
open http://localhost:3000  # admin/admin
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
# Returns:
# {
#     "job_id": "abc-123",
#     "status": "finished",
#     "result": {
#         "anchor_id": "product_123",
#         "edges_created": 5,
#         "anchor_edges_created": 3,
#         "candidate_edges_created": 2,
#         "edges_reinforced": 2,
#         "edges_noop_existing": 1
#     },
#     "error": None
# }
```

Or via the API:

```bash
curl http://localhost:8000/v1/jobs/abc-123 | jq
```

## Future Enhancements

1. **Job deduplication** — Skip if same anchor+candidates recently enqueued
2. **Priority queues** — User-triggered vs background warmup
3. **Batch inference** — Combine multiple anchors into one LLM call
4. **Result caching** — Cache frequent queries in Redis
