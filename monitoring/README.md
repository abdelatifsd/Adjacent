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

3. **Promtail** (port 9080)
   - Tails `logs/api.log`, `logs/worker.log`, and `logs/simulation.log` and sends them to Loki.
   - Primary metrics still come from direct HTTP push (LokiHandler); Promtail provides a backup path and is required for **simulation** when the script runs on the host (direct push to Loki in Docker may not reach).
   - Simulation job uses a pipeline that extracts the JSON `timestamp` field so event time is correct (not ingestion time).
   - Config: [promtail-config.yml](promtail-config.yml)

4. **Loki** (port 3100)
   - Stores and indexes logs
   - Provides LogQL query API
   - Persists data to Docker volume `loki-data`
   - Config: [loki-config.yml](loki-config.yml)

5. **Grafana** (port 3000)
   - Visualizes metrics from Loki
   - Pre-configured dashboard: "Adjacent Metrics" with a **Job** variable (`$job`) to switch between `api` (production) and `simulation`
   - Worker panels (LLM Calls, Token Usage, etc.) always show `job="worker"` (all worker activity); query/graph/vector panels use `$job`
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

### Vector → Graph Savings
- **From Graph (count)** / **From Vector (count)**: Raw counts (no percentage) so you can compare graph vs vector split

### Overview Row
- **Total Queries**: Count of API queries in selected time range
- **Avg Query Time**: Mean query response time (green < 100ms < yellow < 500ms < red)
- **LLM Calls**: Count of LLM inference calls
- **Avg LLM Time**: Mean LLM inference time (green < 5s < yellow < 15s < red)

### Query Latency / LLM Latency
- **Query Latency**: Median API query duration over time (`quantile_over_time`)
- **LLM Latency**: Median LLM call duration over time

### Graph Evolution Row
- **Result Sources**: Stacked bar chart showing results from graph vs. vector search (graph should increase over time as LLM builds edges)
- **Edges Created**: Bar chart of new edges created by LLM inference

### Token Economics, Sub-Span Latency, Edge Lifecycle
- Token usage over time; per-operation latency; edge state transitions

## Common Operations

### Docker Compose (Integrated Setup)

The monitoring stack is now integrated with the main application via `make dev`:

```bash
# Start everything (app + monitoring)
make dev

# View all service logs
make dev-logs

# Check service health
make dev-status

# Stop everything (keeps volumes)
make dev-down

# Clean everything (removes volumes)
make dev-clean
```

### Standalone Monitoring Stack (Legacy)

For running monitoring independently:

```bash
# Start only monitoring stack
make monitoring-up
# or
docker compose up -d loki promtail grafana

# Stop monitoring stack (preserves data)
make monitoring-down

# View real-time logs
docker compose logs -f loki promtail grafana
```

## Troubleshooting

### Issue: Grafana shows "No data"

**Symptoms:**
- Dashboard panels show "No data" even though API/worker are running

**Diagnosis:**
```bash
# Check if services are running
make dev-status

# Check if logs are being written
ls -lh logs/
cat logs/api.log | grep '"span"' | head -5

# Check if Loki is receiving logs
curl -s 'http://localhost:3100/loki/api/v1/query?query={job="api"}' | jq '.data.result | length'

# Check Loki handler status (look for errors in application logs)
docker compose logs api | grep -i loki
docker compose logs worker | grep -i loki
```

**Common Causes:**

1. **Logs haven't been generated yet**
   - Solution: Make some API requests to generate logs
   ```bash
   curl "http://localhost:8000/v1/query/1?top_k=5"
   ```

2. **Services not running**
   - Check with: `docker compose ps`
   - Solution: Start everything with `make dev`

3. **Loki not reachable from containers**
   - In Docker Compose, services use `http://loki:3100` (not localhost)
   - Check docker-compose.yml for correct LOKI_URL
   - Solution: Ensure all services are on the same network: `docker network ls | grep adjacent`

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

# Simulation logs (from simulate/v1_random_sample)
{job="simulation"}

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

**Before you commit any dashboard change:** run through the [Checklist: Do This Every Time](#checklist-do-this-every-time-mandatory) below (graphTooltip, tooltip single, showPoints always, showLegend false, tooltip field overrides). These settings prevent recurring bugs.

When creating or modifying Grafana dashboards for Loki metrics, follow these best practices to ensure clean, usable visualizations.

### Checklist: Do This Every Time (mandatory)

**Before saving or committing any dashboard change, verify:**

| Setting | Required value | Why |
|--------|-----------------|-----|
| **Dashboard: graphTooltip** | `0` | Per-panel tooltips only; avoids confusing shared crosshair behavior. |
| **Time series: tooltip mode** | `"single"` (never `"multi"`) | Hover shows only the series under the cursor. `"multi"` lists every series and repeats names (e.g. "fetch anchor graph lookup graph lookup...") and overwhelms users. |
| **Time series: showPoints** | `"always"` (never `"never"` or `"auto"`) | Data points (dots) are always visible on the line. With `"never"` or `"auto"`, points often only appear on hover, so users cannot see if there is data without hovering. |
| **Time series: showLegend** | `false` | Prevents the legend from growing with every new series as data comes in. |
| **Field overrides for tooltips** | Hide JSON metadata (event_type, span, trace_id, etc.); show only Value | Tooltips show the metric value (e.g. "558 ms"), not the full log line. |

**In JSON:**

- At dashboard level: `"graphTooltip": 0`
- For every time series panel:
  - `"options": { "tooltip": { "mode": "single", ... } }` — never `"mode": "multi"`
  - In panel `fieldConfig.defaults.custom`: `"showPoints": "always"` — never `"never"` or `"auto"`
  - `"options": { "legend": { "showLegend": false, ... } }`

These settings fix recurring bugs: repeated/overwhelming tooltip series and invisible data points until hover. Apply them to every new or edited time series panel.

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

**Always use single-series tooltips:**
- Set **`"tooltip": { "mode": "single", ... }`** on every time series panel. Never use `"mode": "multi"`.
- With `"multi"`, hovering lists every series at that time (e.g. fetch_anchor, graph_lookup, vector_search…) and can repeat the same names, which is confusing and recurring bug.
- With `"single"`, the tooltip shows only the one series under the cursor.

**Dashboard-level:**
- Set **`"graphTooltip": 0`** in the dashboard JSON so tooltips are per-panel and not shared in a way that encourages multi-series listing.

**Hide JSON fields from tooltips:**
- Add field overrides to hide all JSON metadata fields (event_type, span, trace_id, counts, attrs, timestamp, schema_version, status).
- Show only the metric value of interest (e.g. Value / displayName).

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

**Always show data points:**
- Set **`"showPoints": "always"`** in `fieldConfig.defaults.custom` for every time series panel. Never use `"never"` or `"auto"`.
- With `"never"` or `"auto"`, markers on the line often appear only on hover, so users cannot tell if there is data without moving the mouse. This is a recurring bug.
- With `"always"`, dots are always drawn on the line so data is visible at a glance.

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
2. **Don't use division of two range/instant queries** - causes "unimplemented" (e.g. `A / B` where both are Loki queries). Show counts separately instead of percentages.
3. **Don't show full JSON in tooltips** - use field overrides to hide metadata
4. **Don't let legends grow** - set `showLegend: false` or `displayMode: "hidden"` so legends don't accumulate as data comes in
5. **Don't use tooltip mode `"multi"`** - use `"single"` so the tooltip shows only the series under the cursor; multi repeats names and overwhelms
6. **Don't use `showPoints: "never"` or `"auto"`** - use `"always"` so data points are visible without hovering
7. **Don't use regex extraction** - logs are already clean JSON, use `| json` directly
8. **Don't use unsupported colors** - use hex (e.g. `#d8d9da`) instead of names like `light-gray`
9. **Don't hardcode intervals** - use `[$__interval]` for time series, `[$__range]` for stat panels

### 7. Dashboard Panel Conventions (adjacent-metrics)

- **Vector -> Graph Savings**: "From Graph (count)" and "From Vector (count)" show raw counts (no percentage) to avoid Loki division errors. Compare the two for graph vs vector split.
- **Legends**: All time series panels have legends hidden (`showLegend: false`) so the legend list does not grow with data.
- **Tooltips**: Field overrides hide JSON metadata (event_type, span, trace_id, etc.) so tooltips show only the metric value.
- **Colors**: Use hex for grays (e.g. `#d8d9da`) to avoid "unsupported light gray color" errors.

### 8. Testing Your Dashboard

After creating or modifying panels:

1. **Verify queries work:** Check for syntax errors in Grafana's query inspector
2. **Check tooltips:** Hover over data points - should show only metric values, not full JSON
3. **Verify legends:** Should be hidden or compact, not overwhelming
4. **Test with real data:** Generate some metrics and verify visualizations render correctly
5. **Check time ranges:** Ensure queries work across different time ranges (1h, 6h, 24h)

### Reference: Current Dashboard Structure

The `adjacent-metrics.json` dashboard demonstrates these best practices. **Current rows** (no "Performance Over Time" row; latency is in Overview + dedicated panels):

- **Vector → Graph Savings**: From Graph (count) and From Vector (count) stat panels
- **Overview**: Total Queries, Avg Query Time, LLM Calls, Avg LLM Time
- **Query Latency / LLM Latency**: Time series for query and LLM duration (median via `quantile_over_time`)
- **Graph Evolution**: Result Sources (stacked), Edges Created
- **Token Economics**: Token Usage Over Time, etc.
- **Sub-Span Latency**: Per-operation latency time series
- **Edge Lifecycle**: State transitions over time

**Conventions applied:**
- Clean LogQL using `quantile_over_time` (not `avg_over_time` with unwrap)
- Hidden legends; single-series tooltips; `showPoints: "always"`
- Field overrides to hide JSON metadata from tooltips
- Units: ms for latency, short for counts; hex for colors (e.g. `#d8d9da`)
