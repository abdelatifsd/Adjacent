# System Dynamics & Cold Start Behavior

> Understanding how Adjacent evolves from cold start to full operation

This guide explains how the Adjacent recommendation system behaves over time, what to expect during initial setup, and how to validate system health.

---

## Overview

Adjacent is a **hybrid recommendation engine** that combines:
- **Vector similarity** (immediate, cold-start ready)
- **Graph edges** (inferred asynchronously over time via LLM)

The system is designed to provide useful recommendations immediately while improving quality as it runs.

---

## Cold Start Behavior

### What Happens on First Run

When you first deploy Adjacent with a fresh product catalog:

1. **Products are indexed** with embeddings (vector representations)
2. **Vector index is created** in Neo4j for similarity search
3. **No graph edges exist yet** (inferred_edge_count = 0)
4. **Recommendations come 100% from vector similarity**

This is **expected and by design**. Vector recommendations provide reasonable results based on semantic similarity of product descriptions.

### Example Cold Start Response

```json
{
  "anchor_id": "product_123",
  "recommendations": [
    {
      "product_id": "product_456",
      "edge_type": null,
      "confidence": null,
      "source": "vector",
      "score": 0.87
    }
  ],
  "from_graph": 0,
  "from_vector": 10,
  "inference_status": "enqueued",
  "job_id": "abc-123"
}
```

**Key observations:**
- All recommendations have `"source": "vector"`
- `from_graph: 0` (no edges exist yet)
- `inference_status: "enqueued"` (LLM job is running in background)

---

## System Evolution Over Time

### Phase 1: Cold Start (First Few Queries)

**Graph Coverage:** 0%
**Recommendation Sources:** 100% vector

- System returns vector-based recommendations instantly
- Each query enqueues an async inference job
- Worker processes jobs and creates initial edges

### Phase 2: Early Growth (First Hour)

**Graph Coverage:** 5-20%
**Recommendation Sources:** Mixed (mostly vector, some graph)

- Frequently queried products start accumulating edges
- Recommendations begin mixing graph and vector results
- Graph edges have explicit semantic types (SIMILAR_TO, COMPLEMENTS, etc.)

### Phase 3: Steady State (After Several Hours/Days)

**Graph Coverage:** 40-80%
**Recommendation Sources:** Balanced mix

- Most popular products have rich edge connections
- Graph recommendations dominate for well-connected products
- Vector fallback ensures long-tail products still get recommendations
- Edge confidence scores improve through reinforcement

---

## Why This Design?

### Immediate Value
- No training period required
- Works with any product catalog
- Reasonable recommendations from day one

### Quality Improvement Over Time
- LLM inference captures nuanced relationships (complements vs substitutes)
- Explicit edge types enable filtered queries
- Confidence scores guide ranking
- Reinforcement learning strengthens validated edges

### Operational Simplicity
- Async inference doesn't block query latency
- Worker can be scaled independently
- System degrades gracefully if Redis/worker unavailable

---

## Monitoring System Health

### Using the Status Endpoint

Adjacent provides a dedicated endpoint to verify setup and monitor dynamics:

```bash
curl http://localhost:8000/v1/system/status
```

### Example Response

```json
{
  "status": "ok",
  "neo4j": {
    "connected": true,
    "product_count": 1000,
    "inferred_edge_count": 250,
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
    "pending_jobs": 5
  },
  "dynamics": {
    "graph_coverage_pct": 15.5,
    "notes": [
      "Cold start is expected: vector recommendations dominate until async inference creates edges.",
      "Inferred edges and graph coverage increase over time as the worker runs."
    ]
  }
}
```

### What to Check

#### 1. Neo4j Health
- `neo4j.connected: true` - Database is reachable
- `neo4j.product_count > 0` - Products are loaded
- `neo4j.vector_index.present: true` - Vector search is available
- `neo4j.vector_index.state: "ONLINE"` - Index is ready

#### 2. Inference Health
- `inference.redis_connected: true` - Queue backend is available
- `inference.queue_enabled: true` - Job enqueueing is working
- `inference.pending_jobs` - Current queue depth (should decrease over time)

#### 3. System Dynamics
- `dynamics.graph_coverage_pct` - Percentage of products with edges
  - **0%**: Cold start (expected on first run)
  - **5-20%**: Early growth phase
  - **40-80%**: Steady state
- Watch this metric increase over time as the worker processes jobs

---

## First Run Checklist

Use this checklist to validate a fresh Adjacent deployment:

### Step 1: Verify Infrastructure

```bash
# Check Neo4j is running
docker ps | grep neo4j

# Check Redis is running
docker ps | grep redis

# Test Neo4j connectivity
cypher-shell -u neo4j -p adjacent123 "RETURN 1"

# Test Redis connectivity
redis-cli ping
```

### Step 2: Load Products

```bash
# Ingest product catalog (see api.md for examples)
python scripts/ingest_products.py
```

### Step 3: Create Vector Index

```bash
# Run indexing script or use Neo4j browser
CREATE VECTOR INDEX product_embedding IF NOT EXISTS
FOR (p:Product) ON p.embedding
OPTIONS {
  indexConfig: {
    `vector.dimensions`: 384,
    `vector.similarity_function`: 'cosine'
  }
}
```

### Step 4: Start Services

```bash
# Terminal 1: Start API server
uvicorn adjacent.api.app:app --reload --host 0.0.0.0 --port 8000

# Terminal 2: Start RQ worker
python -m adjacent.async_inference.worker
```

### Step 5: Check System Status

```bash
# Should return status: "ok"
curl http://localhost:8000/v1/system/status | jq
```

**Expected cold start response:**
- `neo4j.connected: true`
- `neo4j.product_count > 0`
- `neo4j.inferred_edge_count: 0` (no edges yet - this is OK!)
- `neo4j.vector_index.present: true`
- `inference.redis_connected: true`
- `dynamics.graph_coverage_pct: null` (becomes a number after first edges)

### Step 6: Make First Query

```bash
# Query a product
curl "http://localhost:8000/v1/query/product_123?top_k=10" | jq
```

**Expected response:**
- `from_vector: 10` (all recommendations from vector)
- `from_graph: 0` (no edges exist yet)
- `inference_status: "enqueued"` (job is queued)
- `job_id: "abc-123"` (track the job)

### Step 7: Monitor Job Processing

```bash
# Check job status
curl "http://localhost:8000/v1/jobs/abc-123" | jq

# Watch queue depth decrease
watch -n 2 'curl -s http://localhost:8000/v1/system/status | jq .inference.pending_jobs'
```

### Step 8: Verify Edge Creation

After a few minutes:

```bash
# Check status again
curl http://localhost:8000/v1/system/status | jq

# Should now show:
# - inferred_edge_count > 0
# - graph_coverage_pct > 0
# - pending_jobs decreasing
```

### Step 9: Verify Graph Recommendations

```bash
# Query the same product again
curl "http://localhost:8000/v1/query/product_123?top_k=10" | jq

# Should now show:
# - from_graph > 0 (some recommendations from edges)
# - recommendations with edge_type (SIMILAR_TO, COMPLEMENTS, etc.)
# - recommendations with confidence scores
```

---

## Troubleshooting Cold Start Issues

### Issue: `inferred_edge_count` Stays at 0

**Possible causes:**
1. Worker not running - Check `docker ps` or process list
2. Redis not connected - Check `inference.redis_connected`
3. OPENAI_API_KEY not set - Check environment variables
4. Worker errors - Check worker logs

**Debug steps:**
```bash
# Check worker logs
python -m adjacent.async_inference.worker

# Verify OpenAI key is set
echo $OPENAI_API_KEY

# Check Redis queue manually
redis-cli
> LLEN adjacent_inference
```

### Issue: `vector_index.present: false`

**Solution:**
```cypher
// Create vector index manually
CREATE VECTOR INDEX product_embedding IF NOT EXISTS
FOR (p:Product) ON p.embedding
OPTIONS {
  indexConfig: {
    `vector.dimensions`: 384,
    `vector.similarity_function`: 'cosine'
  }
}

// Wait for index to come online
SHOW INDEXES
```

### Issue: `graph_coverage_pct` Not Increasing

**Possible causes:**
1. Worker processing slowly (check `pending_jobs`)
2. LLM rate limits (check worker logs for errors)
3. Insufficient query volume (system learns from queries)

**Solutions:**
- Scale workers horizontally (run multiple worker processes)
- Reduce `job_timeout` if jobs are hanging
- Query more products to trigger more inference jobs

---

## Performance Expectations

### Query Latency

- **Vector-only queries:** 10-50ms (cold start)
- **Mixed graph+vector:** 15-80ms (steady state)
- **Graph-heavy:** 20-100ms (well-connected products)

Latency increases slightly as graph grows, but stays under 100ms for typical catalogs.

### Inference Throughput

- **Single worker:** ~5-15 jobs/minute (depends on LLM latency)
- **Multiple workers:** Linear scaling (3 workers ≈ 45 jobs/minute)

### Coverage Timeline

For a 1,000 product catalog with 1 worker:

- **1 hour:** ~5-10% coverage
- **6 hours:** ~20-30% coverage
- **24 hours:** ~40-60% coverage
- **3 days:** ~70-90% coverage

High-traffic products get edges first (query-driven learning).

---

## Related Documentation

- **[API Reference](api.md)** - Complete endpoint documentation and examples
- **[Async Architecture](async_architecture.md)** - Deep dive into inference system design
- **[Metrics Guide](metrics.md)** - Performance instrumentation details

---

## Summary

**Key Takeaways:**

1. **Cold start is normal** - Vector recommendations work immediately
2. **System improves over time** - Watch `graph_coverage_pct` increase
3. **Use status endpoint** - Monitor health with `/v1/system/status`
4. **Worker must run** - Async inference requires RQ worker process
5. **Be patient** - Full coverage takes hours/days depending on catalog size

The status endpoint provides a real-time snapshot of your system's evolution from cold start to steady state. Check it regularly during initial deployment to ensure healthy growth.
