# Adjacent API Reference

> Minimal FastAPI server exposing QueryService querying and job status endpoints.

The Adjacent API provides a thin HTTP layer over the core QueryService, enabling:
- Low-latency recommendation queries
- Performance monitoring
- Async job status tracking

---

## Quick Start

### Prerequisites

1. **Neo4j** running with indexed products
2. **Redis** running for job queue
3. **RQ worker** running to process inference jobs (if using async inference)

### Installation

Install FastAPI and Uvicorn (already added to `pyproject.toml`):

```bash
uv sync
```

### Required Environment Variables

```bash
# Neo4j connection
export NEO4J_URI="bolt://localhost:7688"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="adjacent123"

# Redis connection
export REDIS_URL="redis://localhost:6379/0"

# Embedding provider
export EMBEDDING_PROVIDER="huggingface"  # or "openai"
# export EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2"  # optional

# LLM (optional, required for async inference)
export OPENAI_API_KEY="sk-..."  # optional
export LLM_MODEL="gpt-4o-mini"  # optional, defaults to gpt-4o-mini
```

**Note:** If `OPENAI_API_KEY` is not set, async inference will be skipped automatically (inference_status will be "skipped").

---

## Running the API Server

### Development Mode

```bash
uvicorn adjacent.api.app:app --reload --host 0.0.0.0 --port 8000
```

**Options:**
- `--reload`: Auto-reload on code changes
- `--host 0.0.0.0`: Listen on all network interfaces
- `--port 8000`: Port to bind to

### Production Mode

```bash
uvicorn adjacent.api.app:app --host 0.0.0.0 --port 8000 --workers 4
```

**Production considerations:**
- Use multiple workers for concurrency
- Each worker initializes its own Neo4j connection pool
- Redis connection is shared via connection string

---

## Running the Worker

The worker processes async inference jobs enqueued by query endpoints.

```bash
rq worker adjacent_inference --url redis://localhost:6379/0
```

**Worker details:**
- Queue name: `adjacent_inference` (defined in `AsyncConfig.queue_name`)
- Task module: `adjacent.async_inference.tasks`
- Job function: `infer_edges(anchor_id, candidate_ids, config_dict)`

**Verify queue name:**

If the worker isn't processing jobs, verify the queue name matches your config:

```bash
# Check queue status
rq info --url redis://localhost:6379/0

# Should show "adjacent_inference" queue
```

**Environment for worker:**

The worker needs the same environment variables as the API server:
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`
- `OPENAI_API_KEY` (required for inference)
- `LLM_MODEL` (optional)

The config is also serialized in the job payload as a fallback.

---

## API Endpoints

### GET /health

Health check endpoint.

**Response:**

```json
{
  "status": "ok"
}
```

**Example:**

```bash
curl http://localhost:8000/health
```

---

### GET /v1/query/{product_id}

Get recommendations for a product.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `product_id` | path | Yes | - | Anchor product ID |
| `top_k` | query | No | 10 | Number of recommendations to return |
| `skip_inference` | query | No | false | Skip async inference (no job enqueued) |
| `X-Trace-Id` | header | No | auto | Request trace ID for correlation |

**Response:**

```json
{
  "anchor_id": "prod-123",
  "recommendations": [
    {
      "product_id": "prod-456",
      "edge_type": "COMPLEMENTS",
      "confidence": 0.70,
      "source": "graph",
      "score": null
    },
    {
      "product_id": "prod-789",
      "edge_type": null,
      "confidence": null,
      "source": "vector",
      "score": 0.87
    }
  ],
  "from_graph": 5,
  "from_vector": 5,
  "inference_status": "enqueued",
  "job_id": "abc-123-def-456",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `anchor_id` | string | Queried product ID |
| `recommendations` | array | List of recommended products |
| `recommendations[].product_id` | string | Recommended product ID |
| `recommendations[].edge_type` | string or null | Edge type (if from graph) |
| `recommendations[].confidence` | float or null | Confidence score (if from graph) |
| `recommendations[].source` | string | "graph" or "vector" |
| `recommendations[].score` | float or null | Similarity score (if from vector) |
| `from_graph` | int | Count of graph-sourced recommendations |
| `from_vector` | int | Count of vector-sourced recommendations |
| `inference_status` | string | "complete", "enqueued", or "skipped" |
| `job_id` | string or null | Job ID if inference was enqueued |
| `trace_id` | string | Request trace ID for correlation |

**Inference Status:**
- `"complete"`: No new candidates needed inference (graph had enough results)
- `"enqueued"`: Inference job was enqueued (job_id provided)
- `"skipped"`: Inference was skipped (skip_inference=true or no OPENAI_API_KEY)

**Examples:**

```bash
# Basic query
curl http://localhost:8000/v1/query/prod-123

# Query with custom top_k
curl "http://localhost:8000/v1/query/prod-123?top_k=20"

# Query without inference
curl "http://localhost:8000/v1/query/prod-123?skip_inference=true"

# Query with trace ID
curl -H "X-Trace-Id: my-trace-123" http://localhost:8000/v1/query/prod-123
```

**Error Responses:**

All error responses include `error_type` for programmatic handling:

```json
// Product not found (404)
{
  "error": "Product not found: prod-999",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "error_type": "product_not_found"
}

// Invalid input (400)
{
  "error": "Invalid product_id format",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "error_type": "invalid_input"
}

// Service unavailable (503)
{
  "error": "Service temporarily unavailable",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "error_type": "service_unavailable"
}

// Internal error (500)
{
  "error": "Internal server error",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "error_type": "internal_error"
}
```

**Error Types:**

| error_type | HTTP Status | Meaning | Retry? |
|------------|-------------|---------|--------|
| `product_not_found` | 404 | Product ID not in database | No |
| `invalid_input` | 400 | Invalid query parameters | No |
| `service_unavailable` | 503 | Neo4j or Redis unavailable | Yes, with backoff |
| `redis_unavailable` | 503 | Redis unavailable (job status only) | Yes, with backoff |
| `internal_error` | 500 | Unexpected server error | Maybe, check logs |

---

### GET /v1/perf/query/{product_id}

Get recommendations with performance timings.

**Same as `/v1/query/{product_id}` but includes:**
- `request_total_ms`: Total API request duration in milliseconds

**Parameters:**

Same as `/v1/query/{product_id}`.

**Response:**

Same as `/v1/query/{product_id}` plus:

```json
{
  "anchor_id": "prod-123",
  "recommendations": [...],
  "from_graph": 5,
  "from_vector": 5,
  "inference_status": "enqueued",
  "job_id": "abc-123-def-456",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "request_total_ms": 145.23
}
```

**Use Case:**

Use this endpoint to monitor query latency and performance. The trace_id can be correlated with backend span metrics (emitted as JSONL events) for detailed performance breakdown.

**Example:**

```bash
curl http://localhost:8000/v1/perf/query/prod-123
```

**Performance Expectations:**

| Scenario | Expected Latency |
|----------|------------------|
| Warm cache, graph results | 10-50ms |
| Vector search required | 50-150ms |
| Cold start (first query) | 100-300ms |

*These are rough estimates. Actual performance depends on Neo4j cluster, network, and data size.*

---

### GET /v1/jobs/{job_id}

Get status of an async inference job.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `job_id` | path | Yes | Job ID from query response |

**Response:**

```json
{
  "job_id": "abc-123-def-456",
  "status": "finished",
  "result": {
    "edges_created": 3,
    "edges_reinforced": 2,
    "total_edges": 5
  },
  "error": null
}
```

**Status Values:**

| Status | Description |
|--------|-------------|
| `queued` | Job is waiting to be processed |
| `started` | Job is currently running |
| `finished` | Job completed successfully |
| `failed` | Job failed with error |
| `not_found` | Job ID does not exist |

**Result Field:**

The `result` field contains the return value from the `infer_edges` task:
- `edges_created`: Number of new edges created
- `edges_reinforced`: Number of existing edges reinforced
- `total_edges`: Total edges processed

**Examples:**

```bash
# Check job status
curl http://localhost:8000/v1/jobs/abc-123-def-456

# Poll until complete (bash loop)
while true; do
  status=$(curl -s http://localhost:8000/v1/jobs/abc-123-def-456 | jq -r '.status')
  echo "Status: $status"
  [[ "$status" == "finished" ]] && break
  sleep 1
done
```

**Error Response:**

```json
{
  "job_id": "invalid-id",
  "status": "not_found",
  "error": "No such job: invalid-id"
}
```

---

## Expected Behavior

### Initial State (Sparse Graph)

When the system first starts with a fresh product catalog:

1. **First Query:**
   - `from_graph`: 0 (no edges exist yet)
   - `from_vector`: 10 (all results from vector similarity)
   - `inference_status`: "enqueued" (job created for candidates)
   - All recommendations have `source: "vector"`

2. **After Worker Runs:**
   - Edges are materialized in Neo4j
   - Status changes to "PROPOSED" (first anchor)
   - Confidence starts at ~0.55

3. **Subsequent Queries:**
   - `from_graph` increases as edges accumulate
   - `from_vector` decreases (less supplementation needed)
   - Edges gain confidence as they're reinforced by multiple anchors
   - Status progresses: PROPOSED → ACTIVE (at ~0.70 confidence)

### Steady State (Mature Graph)

After the system has processed many queries:

- Most queries return `from_graph: 10, from_vector: 0`
- `inference_status: "complete"` (graph already saturated)
- Recommendations have `source: "graph"` with edge types and confidence scores
- Worker jobs focus on new products or under-explored regions

---

## Trace Correlation

Every API request includes a `trace_id` in the response. This ID:

1. **Flows through the entire query path:**
   - API request → QueryService.query() → all internal spans
   - Appears in all JSONL metrics events
   - Enables end-to-end performance analysis

2. **Can be user-provided:**
   - Pass `X-Trace-Id` header to correlate external requests
   - Useful for debugging or tracking specific sessions

3. **Appears in logs:**
   ```json
   {
     "event_type": "span",
     "span": "query_total",
     "duration_ms": 123.45,
     "trace_id": "550e8400-e29b-41d4-a716-446655440000",
     "operation": "query",
     "attrs": { "product_id": "prod-123" }
   }
   ```

**Use trace_id to:**
- Correlate API requests with backend metrics
- Debug slow queries
- Track request flow across distributed components

---

## Deployment Checklist

Before deploying to production:

- [ ] Set all required environment variables
- [ ] Start Neo4j with indexed products and vector index
- [ ] Start Redis
- [ ] Start RQ worker(s) for async inference
- [ ] Start API server with multiple workers (`--workers 4`)
- [ ] Verify `/health` endpoint responds
- [ ] Test a sample query with `/v1/query/{product_id}`
- [ ] Check worker logs to ensure jobs are processed
- [ ] Monitor metrics via JSONL events (see `docs/metrics.md`)

---

## Architecture Notes

### Error Handling Philosophy

The API implements clean error responses suitable for OSS:

1. **No stack traces in responses**: Internal errors are logged server-side but never leaked to clients
2. **Structured error types**: All errors include `error_type` for programmatic handling
3. **Appropriate HTTP status codes**:
   - `400`: Client error (invalid input)
   - `404`: Resource not found
   - `503`: Service temporarily unavailable (retry with backoff)
   - `500`: Unexpected internal error (check logs)
4. **Graceful degradation**: Redis unavailable during query doesn't break the query—it just skips inference and returns graph+vector results

### Separation of Concerns

Routes are separated into [routes.py](../src/adjacent/api/routes.py) to:
- Keep app.py focused on application lifecycle
- Make route logic easier to test and extend
- Follow FastAPI best practices for larger applications

### No Ingest Endpoints

This API intentionally does NOT expose:
- Product upload/creation
- Bulk ingest
- Edge creation or editing

**Why?**

- Keeps the API thin and focused on querying
- Ingest workflows are typically batch-oriented (CLI/scripts)
- Graph construction is implicit (via lazy inference)

**For ingesting products, use:**

```bash
# CLI ingest (if available)
python -m adjacent.cli.ingest data/products.json

# Or directly via Neo4jContext in a script
```

### No Global Singletons

The API uses FastAPI's lifespan context manager to:
- Initialize `QueryService` once per worker process
- Store on `app.state` (request-scoped, not global)
- Clean up resources on shutdown

This ensures:
- Proper resource lifecycle management
- No leaked connections
- Worker-safe (each worker has its own state)

**Anti-pattern (not used):**

```python
# BAD: Global singleton outside lifespan
query_service = QueryService(config)  # DON'T DO THIS
```

**Correct pattern (used):**

```python
# GOOD: Initialize in lifespan, store on app.state
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.query_service = QueryService(config)
    yield
    app.state.query_service.close()
```

---

## Troubleshooting

### API Won't Start

**Symptoms:**
- Server crashes on startup with `RuntimeError: API startup failed`

**Check:**
1. Neo4j is running and accessible at `NEO4J_URI`
2. Redis is running and accessible at `REDIS_URL`
3. Environment variables are set correctly
4. Check API logs for specific connection errors

**Fix:**
```bash
# Verify Neo4j is running
docker ps | grep neo4j

# Verify Redis is running
redis-cli ping  # Should return PONG

# Check connection strings
echo $NEO4J_URI
echo $REDIS_URL
```

### API Returns 503 (Service Unavailable)

**Symptoms:**
- Queries return `{"error_type": "service_unavailable"}`

**Cause:**
- Neo4j or Redis became unavailable after startup

**Fix:**
- Restart the downed service
- API will reconnect automatically on next request
- Check service logs for root cause

### API Returns 500 on All Queries

**Check:**
1. API logs for detailed error messages
2. Product IDs exist in database
3. Neo4j vector index is created
4. Embedding model is accessible

### Inference Status Always "skipped"

**Cause:**
- `OPENAI_API_KEY` environment variable is not set

**Solution:**
- Set `OPENAI_API_KEY` to enable async inference
- Or use `skip_inference=true` to explicitly skip

### Worker Not Processing Jobs

**Check:**
1. Worker is running (`rq worker adjacent_inference`)
2. Worker is connected to the same Redis instance as API
3. Worker logs for errors
4. Redis queue status: `rq info --url redis://localhost:6379/0`

### Slow Query Performance

**Investigate:**
1. Use `/v1/perf/query/{product_id}` to measure `request_total_ms`
2. Correlate `trace_id` with JSONL span events to find bottleneck
3. Check Neo4j query performance (vector index, Cypher queries)
4. Check network latency between API and Neo4j/Redis

**Common bottlenecks:**
- Vector search on large catalogs (optimize index settings)
- Neo4j cold start (warm up with test queries)
- Network latency (colocate services)

---

## See Also

- [Async Architecture](async_architecture.md) - Details on query flow and worker processing
- [Metrics](metrics.md) - Performance instrumentation and monitoring
- [README](../README.md) - Core system design and graph model
