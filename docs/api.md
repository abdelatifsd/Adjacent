# Adjacent API Reference

> Minimal FastAPI server exposing QueryService querying, job status, and system health endpoints.

The Adjacent API provides a thin HTTP layer over the core QueryService, enabling:
- Low-latency recommendation queries
- Performance monitoring
- Async job status tracking
- System health and status monitoring

---

## Quick Start

### Docker Compose (Recommended)

```bash
# First time setup
./scripts/setup.sh

# Add your OpenAI API key to .env
echo "OPENAI_API_KEY=sk-your-key-here" >> .env

# Start everything
make dev
```

This starts:
- Neo4j with indexed products
- Redis for job queue
- API server on port 8000
- RQ worker for async inference
- Monitoring stack (Grafana, Loki)

**Access points:**
- API: http://localhost:8000/docs
- Grafana: http://localhost:3000 (admin/admin)
- Neo4j: http://localhost:7475 (neo4j/adjacent123)

### Environment Configuration

The [.env](.env) file controls all configuration:

```bash
# Required for async inference
OPENAI_API_KEY=sk-your-key-here

# Optional (defaults provided)
NEO4J_URI=bolt://localhost:7688
NEO4J_USER=neo4j
NEO4J_PASSWORD=adjacent123
REDIS_URL=redis://localhost:6379/0
EMBEDDING_PROVIDER=huggingface
LLM_MODEL=gpt-4o-mini
```

**Note:** If `OPENAI_API_KEY` is not set, async inference will be skipped automatically (inference_status will be "skipped").

---

## Running Components Separately (Advanced)

### Native Python API Server

For debugging or advanced development:

```bash
# Start infrastructure first
make reset-full

# Then start API
make api-start
# or
uv run uvicorn adjacent.api.app:app --reload --host 0.0.0.0 --port 8000
```

### Native Python Worker

```bash
# In separate terminal
make worker
# or
uv run rq worker adjacent_inference --url redis://localhost:6379/0 --with-scheduler
```

### Docker Compose (Individual Services)

```bash
# Start only infrastructure
docker compose up -d neo4j redis

# Start only API
docker compose up -d api

# Start only worker
docker compose up -d worker

# View logs
docker compose logs -f api worker
```

---

## API Endpoints Overview

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Simple health check |
| `/v1/query/{product_id}` | GET | Get recommendations for a product |
| `/v1/perf/query/{product_id}` | GET | Get recommendations with timing metrics |
| `/v1/jobs/{job_id}` | GET | Check async inference job status |
| `/v1/system/status` | GET | Get comprehensive system health and metrics |

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
    "anchor_id": "product_123",
    "edges_created": 5,
    "anchor_edges_created": 3,
    "candidate_edges_created": 2,
    "edges_reinforced": 2,
    "edges_noop_existing": 1
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

**Result Fields:**

The `result` field contains the return value from the `infer_edges` task:

| Field | Type | Description |
|-------|------|-------------|
| `anchor_id` | string | The anchor product ID that was processed |
| `edges_created` | integer | Total number of new edges created |
| `anchor_edges_created` | integer | New edges connecting anchor to candidates |
| `candidate_edges_created` | integer | New edges between candidates (candidate-candidate) |
| `edges_reinforced` | integer | Existing edges where anchor was newly added to `anchors_seen` |
| `edges_noop_existing` | integer | Existing edges where anchor was already in `anchors_seen` |

**Understanding Edge Counts:**

- **Created** edges are brand new relationships discovered by the LLM
  - `anchor_edges_created`: Direct anchor → candidate connections
  - `candidate_edges_created`: Candidate → candidate connections (secondary relationships)
- **Reinforced** edges existed but gain this anchor as a new observer (increases confidence)
- **No-op** edges existed and this anchor already observed them (no change)

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

**Error Responses:**

```json
// Job not found
{
  "job_id": "invalid-id",
  "status": "not_found",
  "error": "No such job: invalid-id"
}

// Redis unavailable (503)
{
  "error": "Job queue temporarily unavailable",
  "error_type": "redis_unavailable"
}

// Internal error (500)
{
  "error": "Failed to retrieve job status",
  "error_type": "internal_error"
}
```

---

### GET /v1/system/status

Get comprehensive system health and status information.

**Description:**

Fast, read-only endpoint that provides a snapshot of system health, including:
- Neo4j connectivity and database stats
- Vector index status
- Redis/RQ connectivity and queue status
- Graph coverage metrics
- System dynamics information

This endpoint degrades gracefully - Redis unavailability doesn't fail the request, but Neo4j is required (returns 503 if unreachable).

**Parameters:**

None.

**Response:**

```json
{
  "status": "ok",
  "neo4j": {
    "connected": true,
    "product_count": 1000,
    "inferred_edge_count": 2450,
    "vector_index": {
      "present": true,
      "state": "ONLINE",
      "name": "product_embedding"
    }
  },
  "inference": {
    "redis_connected": true,
    "queue_enabled": true,
    "queue_name": "adjacent_inference",
    "pending_jobs": 3
  },
  "dynamics": {
    "graph_coverage_pct": 45.2,
    "notes": [
      "Cold start is expected: vector recommendations dominate until async inference creates edges.",
      "Inferred edges and graph coverage increase over time as the worker runs."
    ]
  }
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Overall system status ("ok") |
| `neo4j.connected` | boolean | Neo4j connectivity status |
| `neo4j.product_count` | integer | Total number of products in database |
| `neo4j.inferred_edge_count` | integer | Total number of RECOMMENDATION edges |
| `neo4j.vector_index.present` | boolean | Whether vector index exists |
| `neo4j.vector_index.state` | string or null | Index state ("ONLINE", "FAILED", etc.) |
| `neo4j.vector_index.name` | string or null | Index name |
| `inference.redis_connected` | boolean | Redis connectivity status |
| `inference.queue_enabled` | boolean | Whether job queue is operational |
| `inference.queue_name` | string | RQ queue name |
| `inference.pending_jobs` | integer or null | Number of jobs waiting in queue |
| `dynamics.graph_coverage_pct` | float or null | Percentage of products with inferred edges |
| `dynamics.notes` | array | Informational notes about system behavior |

**Use Cases:**

1. **Health Checks:** Used by Docker healthcheck to verify API is ready
2. **Monitoring:** Check system health without making actual queries
3. **Debugging:** Understand why system may be in cold start or degraded state
4. **Dashboard:** Display system metrics in monitoring tools

**Examples:**

```bash
# Basic status check
curl http://localhost:8000/v1/system/status | jq

# Check if ready (exit 0 if ok)
curl -f http://localhost:8000/v1/system/status > /dev/null 2>&1 && echo "System ready"

# Get specific metrics
curl -s http://localhost:8000/v1/system/status | jq '.neo4j.inferred_edge_count'
curl -s http://localhost:8000/v1/system/status | jq '.dynamics.graph_coverage_pct'
```

**Error Response:**

```json
// Neo4j unavailable (503)
{
  "error": "Neo4j unavailable",
  "error_type": "neo4j_unavailable"
}
```

**Interpreting Results:**

| Condition | Meaning | Action |
|-----------|---------|--------|
| `graph_coverage_pct < 10%` | Cold start, few products have edges | Normal - wait for worker to process queries |
| `graph_coverage_pct > 80%` | Mature graph, most products connected | Good - system is well-trained |
| `inferred_edge_count = 0` | No edges created yet | Make queries to trigger inference |
| `pending_jobs > 100` | Worker may be overloaded or slow | Check worker logs, consider scaling |
| `redis_connected = false` | Queue unavailable | Queries work but no async inference |
| `vector_index.present = false` | No vector index | Embedding not set up, run `make embed` |

**Performance:**

- Very fast (< 50ms typical)
- Read-only queries
- Safe to call frequently for monitoring

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

### Using Docker Compose (Recommended)

- [ ] Clone repository and run `./scripts/setup.sh`
- [ ] Add `OPENAI_API_KEY` to `.env` file
- [ ] Run `make dev` to start all services
- [ ] Verify all services are healthy: `make dev-status`
- [ ] Test a sample query: `curl http://localhost:8000/v1/query/1`
- [ ] Check Grafana dashboard: http://localhost:3000
- [ ] Monitor logs: `make dev-logs`

### Using Native Setup (Advanced)

- [ ] Set all required environment variables in `.env`
- [ ] Start Neo4j with indexed products and vector index
- [ ] Start Redis
- [ ] Run data pipeline: `make ingest && make embed`
- [ ] Start RQ worker(s) for async inference
- [ ] Start API server with multiple workers (`--workers 4`)
- [ ] Verify `/health` endpoint responds
- [ ] Test a sample query with `/v1/query/{product_id}`
- [ ] Check worker logs to ensure jobs are processed
- [ ] Monitor metrics via Grafana or JSONL events

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
1. Neo4j is running and accessible
2. Redis is running and accessible
3. Environment variables are set correctly in `.env`
4. Check API logs for specific connection errors

**Fix:**
```bash
# Check service status
make dev-status

# View API logs
docker compose logs api

# Verify services are running
docker compose ps

# Restart API
docker compose restart api
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
1. Worker is running: `docker compose ps worker`
2. Worker logs for errors: `docker compose logs worker`
3. Worker is connected to Redis properly
4. Job queue status in worker logs

**Fix:**
```bash
# Restart worker
docker compose restart worker

# View worker logs in real-time
docker compose logs -f worker

# Check Redis connectivity
docker compose exec worker redis-cli -h redis ping
```

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
