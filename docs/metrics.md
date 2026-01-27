# Performance Metrics Instrumentation

This document describes the lightweight performance metrics system used across Adjacent.

## Overview

The metrics system produces JSONL (JSON Lines) structured events for:
- **Latency breakdown**: Timing of individual operations (spans)
- **Counters**: Point-in-time metrics (counts, flags, configuration values)

**Key features:**
- Stdlib-only (no external dependencies)
- Works in CLI scripts and RQ workers
- Clean JSONL output for easy analysis
- Safe serialization (prevents huge payloads)
- Trace ID correlation across operations

## Quick Start

### 0. Output Location

Metrics are written as JSONL files. The `logs/` directory is git-ignored and is the recommended location for storing metrics output:

```bash
# Metrics are automatically written by QueryService and worker tasks
# Configure output location via logging_config:
from commons.logging_config import configure_metrics_logger

logger = configure_metrics_logger("adjacent", output_file="logs/metrics.jsonl")
```

**Note**: The `logs/` directory, `*.jsonl` files, and `*.log` files are all excluded from git via `.gitignore`.

### 1. Import the metrics module

```python
from commons.metrics import span, emit_counter, generate_trace_id
import logging

logger = logging.getLogger(__name__)
trace_id = generate_trace_id()
```

### 2. Wrap operations with spans

```python
# Measure operation duration
with span("fetch_products", operation="query", trace_id=trace_id, logger=logger) as ctx:
    products = fetch_many(product_ids)
    ctx.set_count("products_fetched", len(products))
```

### 3. Emit standalone counters

```python
emit_counter("top_k", 10, operation="query", trace_id=trace_id, logger=logger)
```

### 4. Configure clean JSONL logging

```python
from commons.logging_config import configure_metrics_logger

# Write to git-ignored logs/ directory
logger = configure_metrics_logger("adjacent", output_file="logs/metrics.jsonl")
```

## Event Schema

All events include:
- `schema_version`: Schema version (currently "1.0")
- `timestamp`: ISO 8601 timestamp (UTC)
- `event_type`: "span" or "counter"
- `operation`: High-level operation name (e.g., "query", "infer_edges")
- `trace_id`: UUID for correlating related operations (optional)

### Span Events

Measure duration of an operation:

```json
{
  "schema_version": "1.0",
  "timestamp": "2026-01-24T12:34:56.789Z",
  "event_type": "span",
  "operation": "query",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "span": "fetch_anchor",
  "duration_ms": 12.45,
  "status": "ok",
  "attrs": {
    "product_id": "prod_abc123"
  }
}
```

Span events can also include:
- `counts`: Dictionary of counter values accumulated during the span
- `error_type`: Exception class name (if status is "error")
- `error_message`: Exception message (if status is "error")

### Counter Events

Record a point-in-time metric:

```json
{
  "schema_version": "1.0",
  "timestamp": "2026-01-24T12:34:56.900Z",
  "event_type": "counter",
  "operation": "query",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "counter_name": "top_k",
  "value": 10
}
```

## Instrumented Paths

### Query Path (QueryService.query)

**Spans:**
- `query_total`: Total query duration
- `fetch_anchor`: Fetch anchor product from Neo4j
- `increment_query_count`: Update anchor's total query count
- `graph_neighbors`: Get existing graph edges
- `vector_search`: Vector similarity search (if needed)
- `enqueue_inference`: Enqueue async inference job

**Counts:**
- `from_graph`: Number of recommendations from graph
- `from_vector`: Number of recommendations from vector search
- `candidates_enqueued`: Number of candidates enqueued for inference

**Counters:**
- `top_k`: Requested number of recommendations
- `skip_inference`: 1 if inference skipped, 0 otherwise

### Worker Path (tasks.infer_edges)

**Spans:**
- `infer_edges_total`: Total inference task duration
- `fetch_products`: Fetch anchor and candidate products
- `llm_call`: LLM edge inference
- `materialize_and_upsert`: Edge materialization and storage
- `mark_anchor_inferred`: Update anchor inference timestamp

**Counts:**
- `candidates_count`: Number of candidate products
- `patches_count`: Number of edge patches from LLM
- `edges_created`: Number of new edges created
- `edges_reinforced`: Number of existing edges reinforced (anchor newly observed)
- `anchor_edges_created`: Number of new edges directly involving the anchor
- `candidate_edges_created`: Number of new edges between candidates
- `edges_noop_existing`: Number of edges that already existed with this anchor
- `input_tokens`: LLM input tokens consumed
- `output_tokens`: LLM output tokens generated
- `total_tokens`: Total LLM tokens (input + output)
- `cached_tokens`: Number of cached tokens (prompt caching)
- `reasoning_tokens`: Number of reasoning tokens (for reasoning models)

**Attributes (llm_call span):**
- `model`: LLM model used for inference
- `response_id`: OpenAI response ID
- `system_prompt_hash`: Hash of system prompt content
- `user_prompt_hash`: Hash of user prompt content
- `status`: LLM response status
- `service_tier`: OpenAI service tier (if available)

## Analyzing Metrics

### View metrics events

```bash
# View all events from logs directory
cat logs/metrics.jsonl | jq

# Extract specific span timings with jq
cat logs/metrics.jsonl | jq -s '.[] | select(.span=="vector_search") | .duration_ms'

# Filter by operation
cat logs/metrics.jsonl | jq -s '.[] | select(.operation=="query")'

# Compute percentiles by span
cat logs/metrics.jsonl | jq -s '
  group_by(.span) |
  map({
    span: .[0].span,
    count: length,
    p50: (sort_by(.duration_ms) | .[length/2 | floor].duration_ms),
    p95: (sort_by(.duration_ms) | .[length*0.95 | floor].duration_ms)
  })
'

# Summarize LLM token usage
cat logs/metrics.jsonl | jq -s '
  [.[] | select(.span=="llm_call")] |
  {
    total_calls: length,
    total_input_tokens: map(.counts.input_tokens // 0) | add,
    total_output_tokens: map(.counts.output_tokens // 0) | add,
    total_cached_tokens: map(.counts.cached_tokens // 0) | add
  }
'
```

## Example Metrics Output

### Sample span event (query path)
```json
{
  "schema_version": "1.0",
  "timestamp": "2026-01-24T12:34:56.789Z",
  "event_type": "span",
  "operation": "query",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "span": "vector_search",
  "duration_ms": 42.15,
  "status": "ok",
  "counts": {
    "from_vector": 5
  }
}
```

### Sample span event (worker path)
```json
{
  "schema_version": "1.0",
  "timestamp": "2026-01-24T12:35:10.123Z",
  "event_type": "span",
  "operation": "infer_edges",
  "trace_id": "660e8400-e29b-41d4-a716-446655440111",
  "span": "llm_call",
  "duration_ms": 1250.75,
  "status": "ok",
  "attrs": {
    "model": "gpt-4o-mini",
    "response_id": "chatcmpl-abc123",
    "status": "completed"
  },
  "counts": {
    "patches_count": 15,
    "input_tokens": 2500,
    "output_tokens": 450,
    "total_tokens": 2950,
    "cached_tokens": 1200
  }
}
```

## Safety Guidelines

### What to include in `attrs`

**DO include:**
- Product IDs, edge IDs (strings)
- Small integers (counts, limits)
- Booleans (feature flags)
- Short strings (< 100 chars)

**DO NOT include:**
- Full product descriptions or text blobs
- Embeddings or vector data
- Large lists of candidates
- Credentials or tokens
- PII (user emails, names, etc.)

The system automatically truncates long strings and lists, but it's better to avoid passing them in the first place.

## Advanced Usage

### Nested spans

```python
trace_id = generate_trace_id()

with span("query_total", operation="query", trace_id=trace_id, logger=logger):
    with span("fetch_anchor", operation="query", trace_id=trace_id, logger=logger):
        anchor = fetch_product(product_id)

    with span("graph_neighbors", operation="query", trace_id=trace_id, logger=logger) as ctx:
        neighbors = get_neighbors(product_id)
        ctx.set_count("neighbor_count", len(neighbors))
```

### Error handling

```python
with span("risky_operation", operation="query", trace_id=trace_id, logger=logger) as ctx:
    try:
        result = do_something_risky()
    except ValueError as e:
        # Error is automatically captured by the span context manager
        raise
    # Span will emit with status="error", error_type="ValueError", error_message="..."
```

### Multiple loggers

```python
# Configure separate outputs for different modules
from commons.logging_config import configure_combined_logger

# Metrics to metrics.jsonl, debug logs to debug.log
logger = configure_combined_logger(
    "adjacent",
    level=logging.DEBUG,
    metrics_file="metrics.jsonl",
    debug_file="debug.log"
)
```

## Integration with Existing Code

The metrics system is already integrated into:
- `src/adjacent/async_inference/query_service.py::QueryService.query`
- `src/adjacent/async_inference/tasks.py::infer_edges`

To add metrics to new code:

1. Import the metrics module
2. Generate a trace ID at the entry point
3. Wrap key operations with `span()` context managers
4. Set counts via `ctx.set_count()`
5. Emit standalone counters with `emit_counter()`

## Schema Reference

See [`schemas/metrics_event.json`](../schemas/metrics_event.json) for the complete JSON Schema definition.
