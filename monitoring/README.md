# Adjacent Monitoring Stack

This directory contains the configuration for Adjacent's observability stack: **Loki** (log aggregation), **Promtail** (log shipping), and **Grafana** (visualization).

## Architecture

```
┌─────────────┐                                                      ┌─────────┐
│   API/      │                                                      │         │
│   Worker    │───────────────────HTTP Push────────────────────────▶│  Loki   │
│  (Python)   │                                                      │         │
└─────────────┘                                                      └─────────┘
      │                                                                   │
      │ (backup)                                                          │
      ▼                                                                   │
┌──────────────┐                                                          │
│  Log Files   │                                                          │
│(logs/*.log)  │                                                          │
└──────────────┘                                                          │
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
   - **Direct HTTP push to Loki** via [LokiHandler](../src/commons/loki_handler.py)
   - Also logs to `logs/api.log` and `logs/worker.log` as backup

2. **Loki HTTP Handler** ([src/commons/loki_handler.py](../src/commons/loki_handler.py))
   - Custom Python logging handler
   - Pushes logs directly to Loki via HTTP API
   - Batches logs for efficiency
   - Eliminates Promtail dependency and macOS Docker file watching issues
   - Configurable via environment variables:
     - `LOKI_URL`: Loki push endpoint (default: `http://localhost:3100/loki/api/v1/push`)
     - `LOKI_JOB`: Job label (default: `api` for API, `worker` for workers)
     - `LOKI_ENABLED`: Enable/disable Loki handler (default: `true`)

3. **Promtail** (port 9080) - *Optional/Deprecated*
   - Previously used for file tailing
   - No longer required for metrics (logs push directly to Loki)
   - May still be used for other log sources if needed
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

### 1. Structured Metrics (JSON) - Direct to Loki

Metrics are pushed directly to Loki as **clean JSON** (no prefixes):

```json
{"event_type":"span","span":"query_total","duration_ms":558.11,"status":"ok","operation":"query","trace_id":"...","counts":{"from_graph":0,"from_vector":10},"timestamp":"2026-01-27T13:50:45.481122+00:00","schema_version":"1.0"}
```

**Note:** Log files (`logs/api.log`, `logs/worker.log`) still contain prefixed format for debugging:
```
2026-01-27 15:50:45,481 [INFO] adjacent.async_inference.query_service: {"event_type":"span",...}
```

But Loki receives clean JSON directly, eliminating the need for regex parsing in LogQL queries.

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

Regular Python log messages without structured data. These are also pushed to Loki but don't have structured JSON fields.

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

**Diagnosis:**
```bash
# Check if logs are being written
ls -lh logs/
cat logs/api.log | grep '"span"' | head -5

# Check if Loki is receiving logs
curl -s 'http://localhost:3100/loki/api/v1/query?query={job="api"}' | jq '.data.result | length'

# Check Loki handler status (look for errors in application logs)
docker logs adjacent-api 2>&1 | grep -i loki
```

**Common Causes:**

1. **Logs haven't been generated yet**
   - Solution: Make some API requests to generate logs
   ```bash
   curl "http://localhost:8000/query/1?top_k=5"
   ```

2. **Loki handler not enabled**
   - Check `LOKI_ENABLED` environment variable (default: `true`)
   - Solution: Ensure `LOKI_ENABLED=true` is set

3. **Loki not reachable**
   - Check if Loki is running: `docker ps | grep loki`
   - Check if Loki URL is correct: `echo $LOKI_URL` (default: `http://localhost:3100/loki/api/v1/push`)
   - Solution: Ensure monitoring stack is running: `make monitoring-up`

4. **Time range issue**
   - Grafana's time picker may not include your log timestamps
   - Solution: Expand time range to "Last 6 hours" or "Last 24 hours"

5. **Loki ingester not ready**
   - After starting, Loki's ingester needs ~15 seconds to warm up
   - Solution: Check `curl http://localhost:3100/ready`

### Issue: macOS Docker file watching (resolved)

**Previous Issue:**
- Promtail couldn't reliably tail files on macOS Docker Desktop due to inotify limitations
- Required manual Promtail container recreation

**Solution:**
- Logs now push directly to Loki via HTTP, eliminating file watching dependency
- No more Promtail position file issues
- Works reliably on all platforms (macOS, Linux, Windows)

### Issue: RQ worker logs have ANSI color codes

**Symptoms:**
- Worker logs show `[32m`, `[39;49;00m`, etc.

**Solution:**
Worker is started with `NO_COLOR=1` environment variable (fixed in [makefile:154](../makefile#L154))

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

# Average query duration (clean JSON, no regex needed)
avg_over_time({job="api"} |~ `"span":"query_total"` | json | unwrap duration_ms [5m])

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
4. **LokiHandler pushes clean JSON directly to Loki via HTTP** (no file tailing needed)
5. Grafana queries Loki via LogQL and visualizes

**Best practices:**
- Use spans for timed operations (queries, LLM calls, DB operations)
- Use counters for standalone metrics (cache hits, feature flags)
- Keep `attrs` small (IDs, short strings, small ints only)
- Never include PII, embeddings, or large text in attrs
- Always provide `trace_id` to correlate related operations

## Grafana Dashboard Best Practices

When creating or modifying Grafana dashboards for Loki metrics, follow these best practices to ensure clean, usable visualizations:

### 1. LogQL Query Best Practices

**Avoid `avg_over_time` with `unwrap`:**
- `avg_over_time(... | unwrap field)` has known bugs in Loki
- **Use instead:** `quantile_over_time(0.5, ... | unwrap field [interval])` for median, or `sum_over_time(... | unwrap field [interval]) / count_over_time(... [interval])` for mean

**Example:**
```logql
# ❌ Avoid (can cause "unimplemented" errors)
avg_over_time({job="api"} | json | unwrap duration_ms [1m])

# ✅ Use median instead
quantile_over_time(0.5, {job="api"} | json | unwrap duration_ms [1m])

# ✅ Or use sum/count division for mean
sum_over_time({job="api"} | json | unwrap duration_ms [1m]) / count_over_time({job="api"} |~ `"span":"query_total"` [1m])
```

**Clean JSON parsing:**
- Since logs are pushed as clean JSON (no prefixes), use `| json` directly
- No need for regex extraction: `| regexp ... | line_format ...`

### 2. Legend Configuration

**For single-series panels:**
- Hide legends to reduce clutter: `"showLegend": false`
- Remove calculations: `"calcs": []`

**For multi-series panels:**
- Use compact table mode: `"displayMode": "table", "width": 150`
- Remove unnecessary calculations

**Example:**
```json
"legend": {
  "calcs": [],
  "displayMode": "hidden",
  "showLegend": false
}
```

### 3. Tooltip Configuration

**Hide JSON fields from tooltips:**
- Add field overrides to hide all JSON metadata fields
- Show only the metric value of interest

**Example field overrides:**
```json
"overrides": [
  {
    "matcher": { "id": "byName", "options": "event_type" },
    "properties": [{ "id": "custom.hideFrom", "value": { "tooltip": true, "viz": true, "legend": true } }]
  },
  {
    "matcher": { "id": "byName", "options": "span" },
    "properties": [{ "id": "custom.hideFrom", "value": { "tooltip": true, "viz": true, "legend": true } }]
  },
  // ... hide other JSON fields: operation, trace_id, counts, attrs, timestamp, schema_version, status
  {
    "matcher": { "id": "byName", "options": "Value" },
    "properties": [
      { "id": "displayName", "value": "Latency" },
      { "id": "custom.hideFrom", "value": { "tooltip": false, "viz": false, "legend": true } }
    ]
  }
]
```

This ensures tooltips show only the metric value (e.g., "Latency: 558.11 ms") instead of the full JSON structure.

### 4. Time Series Panel Configuration

**For latency/duration metrics:**
- Use `quantile_over_time(0.5, ...)` for median (more stable than mean)
- Set appropriate units: `"unit": "ms"` or `"unit": "s"`
- Configure thresholds for visual alerts:
  ```json
  "thresholds": {
    "mode": "absolute",
    "steps": [
      { "color": "green", "value": null },
      { "color": "yellow", "value": 100 },
      { "color": "red", "value": 500 }
    ]
  }
  ```

**For count metrics:**
- Use `sum_over_time(... | unwrap count_field [$__interval])`
- Set unit: `"unit": "short"` for automatic formatting

### 5. Panel Organization

- Group related panels in rows with descriptive titles
- Use consistent color schemes (e.g., green for graph metrics, purple for LLM metrics)
- Set appropriate refresh intervals: `"refresh": "10s"` for real-time monitoring

### 6. Common Pitfalls to Avoid

1. **Don't use `avg_over_time` with `unwrap`** - causes "unimplemented" errors
2. **Don't show full JSON in tooltips** - use field overrides to hide metadata
3. **Don't stack legends** - hide them for single-series, use compact mode for multi-series
4. **Don't use regex extraction** - logs are already clean JSON, use `| json` directly
5. **Don't hardcode intervals** - use `[$__interval]` for time series, `[$__range]` for stat panels

### 7. Testing Your Dashboard

After creating or modifying panels:

1. **Verify queries work:** Check for syntax errors in Grafana's query inspector
2. **Check tooltips:** Hover over data points - should show only metric values, not full JSON
3. **Verify legends:** Should be hidden or compact, not overwhelming
4. **Test with real data:** Generate some metrics and verify visualizations render correctly
5. **Check time ranges:** Ensure queries work across different time ranges (1h, 6h, 24h)

### Reference: Current Dashboard Structure

The `adjacent-metrics.json` dashboard demonstrates these best practices:
- Clean LogQL queries using `quantile_over_time` instead of `avg_over_time`
- Hidden legends on single-series panels
- Compact table legends on multi-series panels
- Field overrides to hide JSON metadata from tooltips
- Proper unit configuration (ms for latency, short for counts)
- Organized into logical rows (Overview, Performance Over Time, Graph Evolution)
