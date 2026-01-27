# Adjacent Monitoring Stack

This directory contains the configuration for Adjacent's observability stack: **Loki** (log aggregation), **Promtail** (log shipping), and **Grafana** (visualization).

## Architecture

```
┌─────────────┐       ┌──────────────┐       ┌──────────┐       ┌─────────┐
│   API/      │       │              │       │          │       │         │
│   Worker    │──────▶│  Log Files   │──────▶│ Promtail │──────▶│  Loki   │
│  (Python)   │       │  (logs/*.log)│       │          │       │         │
└─────────────┘       └──────────────┘       └──────────┘       └─────────┘
                                                                       │
                                                                       │
                                                                       ▼
                                                                  ┌─────────┐
                                                                  │ Grafana │
                                                                  │  :3000  │
                                                                  └─────────┘
```

### Components

1. **Application Logging** ([src/commons/metrics.py](../src/commons/metrics.py))
   - Emits structured JSON logs with metrics
   - Uses Python's `logging` module
   - Logs to `logs/api.log` and `logs/worker.log`

2. **Promtail** (port 9080)
   - Tails log files in real-time
   - Parses JSON payloads and extracts labels
   - Ships logs to Loki
   - Config: [promtail-config.yml](promtail-config.yml)

3. **Loki** (port 3100)
   - Stores and indexes logs
   - Provides LogQL query API
   - Persists data to Docker volume `loki-data`
   - Config: [loki-config.yml](loki-config.yml)

4. **Grafana** (port 3000)
   - Visualizes metrics from Loki
   - Pre-configured dashboard: "Adjacent Metrics"
   - Credentials: `admin` / `admin`
   - Dashboard: [grafana/provisioning/dashboards/adjacent-metrics.json](grafana/provisioning/dashboards/adjacent-metrics.json)

## Log Format

The application emits two types of logs:

### 1. Structured Metrics (JSON)
```
2026-01-27 15:50:45,481 [INFO] adjacent.async_inference.query_service: {"event_type":"span","span":"query_total","duration_ms":558.11,"status":"ok","operation":"query","trace_id":"...","counts":{"from_graph":0,"from_vector":10},"timestamp":"2026-01-27T13:50:45.481122+00:00","schema_version":"1.0"}
```

**Key fields:**
- `event_type`: `span` (timed operation) or `counter` (standalone metric)
- `span`: Name of the operation (e.g., `query_total`, `llm_call`, `fetch_anchor`)
- `duration_ms`: How long the operation took
- `operation`: Higher-level operation name (e.g., `query`, `infer_edges`)
- `trace_id`: UUID for tracing related operations
- `counts`: Nested object with counters (e.g., `from_graph`, `from_vector`, `edges_created`)
- `attrs`: Nested object with attributes (e.g., `product_id`, `model`)
- `timestamp`: ISO 8601 timestamp in UTC

### 2. Plaintext Logs
```
2026-01-27 15:50:44,922 [INFO] adjacent.async_inference.query_service: Query for product: 1 (trace_id=a949fea1-7fe1-434c-a32e-294e6c21d975)
```

Regular Python log messages without structured data. Promtail ingests these but doesn't extract labels from them.

## Dashboard Panels

The Grafana dashboard ([adjacent-metrics](http://localhost:3000/d/adjacent-metrics)) shows:

### Overview Row
- **Total Queries**: Count of API queries in selected time range
- **Avg Query Time**: Mean query response time (green < 100ms < yellow < 500ms < red)
- **LLM Calls**: Count of LLM inference calls
- **Avg LLM Time**: Mean LLM inference time (green < 5s < yellow < 15s < red)

### Performance Over Time Row
- **Query Latency**: 5-minute rolling average of API query duration
- **LLM Latency**: 5-minute rolling average of LLM call duration

### Graph Evolution Row
- **Result Sources**: Stacked bar chart showing results from graph vs. vector search (graph should increase over time as LLM builds edges)
- **Edges Created**: Bar chart of new edges created by LLM inference

## Common Operations

### Start monitoring stack
```bash
make monitoring-up
```

### Stop monitoring stack (preserves data)
```bash
make monitoring-down
```

### View real-time logs
```bash
make monitoring-logs
```

### Check health status
```bash
make monitoring-status
```

### Full reset (clears all data including metrics history)
```bash
make reset-full
```

## Troubleshooting

### Issue: Grafana shows "No data"

**Symptoms:**
- Dashboard panels show "No data" even though API/worker are running
- Some panels work (e.g., LLM metrics) but others don't (e.g., query metrics)

**Diagnosis:**
```bash
# Check if logs are being written
ls -lh logs/
cat logs/api.log | grep '"span"' | head -5

# Check if Promtail is ingesting
make monitoring-status

# Check Promtail position
docker exec adjacent-promtail cat /tmp/positions.yaml
```

**Common Causes:**

1. **Logs haven't been generated yet**
   - Solution: Make some API requests to generate logs
   ```bash
   curl "http://localhost:8000/query/1?top_k=5"
   ```

2. **Promtail hasn't caught up**
   - Promtail ingests logs in real-time but may lag slightly
   - Solution: Wait 10-15 seconds and refresh Grafana

3. **Time range issue**
   - Grafana's time picker may not include your log timestamps
   - Solution: Expand time range to "Last 6 hours" or "Last 24 hours"

4. **Loki ingester not ready**
   - After starting, Loki's ingester needs ~15 seconds to warm up
   - Solution: Check `curl http://localhost:3100/ready`

### Issue: RQ worker logs have ANSI color codes

**Symptoms:**
- Worker logs show `[32m`, `[39;49;00m`, etc.
- Promtail can't parse these lines

**Solution:**
Worker is started with `NO_COLOR=1` environment variable (fixed in [makefile:154](../makefile#L154))

### Issue: Promtail stops ingesting after encountering non-JSON log

**Symptoms:**
- Promtail position stuck at a certain byte count
- Not all logs make it to Loki

**Solution:**
The Promtail config has been updated to gracefully handle both JSON and plaintext logs (fixed in [promtail-config.yml:26-44](promtail-config.yml#L26-L44))

### Issue: After full reset, old metrics still appear

**Symptoms:**
- After `make reset-full`, Grafana shows historical data
- Metrics from previous runs persist

**Solution:**
Ensure `reset-full` uses `docker compose down -v` to remove volumes (fixed in [makefile:39](../makefile#L39))

## LogQL Query Examples

Access Loki directly or via Grafana Explore (http://localhost:3000/explore):

```logql
# All API logs
{job="api"}

# Only structured metric logs (with span)
{job="api", span!=""}

# Query operations
{job="api", span="query_total"}

# LLM inference calls
{job="worker", span="llm_call"}

# Errors only
{job="api", status="error"}

# Extract and filter by JSON fields
{job="api"} | json | operation="query"

# Count queries over time
count_over_time({job="api", span="query_total"}[5m])

# Average query duration
avg_over_time({job="api", span="query_total"} | json | unwrap duration_ms [5m])

# Results from graph vs vector
{job="api", span="query_total"} | json | unwrap counts_from_graph
{job="api", span="query_total"} | json | unwrap counts_from_vector

# Edges created per LLM call
{job="worker", span="infer_edges_total"} | json | unwrap counts_edges_created
```

## Files

```
monitoring/
├── README.md                          # This file
├── loki-config.yml                    # Loki configuration
├── promtail-config.yml                # Promtail log shipping config
└── grafana/
    └── provisioning/
        ├── dashboards/
        │   ├── dashboards.yml          # Dashboard provider config
        │   └── adjacent-metrics.json   # Pre-built dashboard
        └── datasources/
            └── loki.yml                # Loki datasource config
```

## Metrics Schema

See [src/commons/metrics.py](../src/commons/metrics.py) for the complete metrics instrumentation API.

**Span lifecycle:**
1. Application code wraps operations in `span()` context manager
2. Span measures duration and collects counts/attributes
3. On exit, emits JSON log line with all metrics
4. Promtail tails log, extracts labels, ships to Loki
5. Grafana queries Loki via LogQL and visualizes

**Best practices:**
- Use spans for timed operations (queries, LLM calls, DB operations)
- Use counters for standalone metrics (cache hits, feature flags)
- Keep `attrs` small (IDs, short strings, small ints only)
- Never include PII, embeddings, or large text in attrs
- Always provide `trace_id` to correlate related operations
