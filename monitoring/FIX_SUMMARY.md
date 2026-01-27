# Monitoring Stack Fix Summary

## Problem Assessment

You identified two critical issues with the monitoring stack:

1. **LogQL JSON Parsing Failure**: Log lines had prefixes (`2026-01-27 16:18:18,247 [INFO] ...`) before the JSON, requiring regex extraction in LogQL queries.

2. **Promtail/macOS Docker Issue**: Promtail's file tailing (inotify) doesn't work reliably on macOS Docker Desktop, causing logs to not be ingested.

## Solution Implemented

### Direct HTTP Push to Loki

Instead of relying on Promtail file tailing, logs now push **directly to Loki via HTTP**. This:

- ✅ Eliminates Promtail dependency for metrics
- ✅ Works reliably on all platforms (macOS, Linux, Windows)
- ✅ No file watching issues
- ✅ Cleaner logs (no prefix parsing needed)
- ✅ More efficient (direct push vs file tailing)

### Changes Made

1. **Created `src/commons/loki_handler.py`**
   - Custom Python logging handler that pushes logs directly to Loki
   - Batches logs for efficiency
   - Thread-safe with automatic flushing
   - Graceful error handling (falls back silently if Loki unavailable)
   - Configurable via environment variables:
     - `LOKI_URL`: Loki push endpoint
     - `LOKI_JOB`: Job label (`api` or `worker`)
     - `LOKI_ENABLED`: Enable/disable (default: `true`)

2. **Updated `src/adjacent/api/app.py`**
   - Configures "adjacent" logger with Loki handler for metrics
   - Clean JSON format (no prefixes) for metrics
   - Keeps file logging as backup

3. **Updated `src/adjacent/async_inference/tasks.py`**
   - Same configuration for worker processes
   - Job label set to "worker"

4. **Updated Grafana Dashboard**
   - Removed regex extraction from all queries
   - Queries now use clean JSON directly: `| json | unwrap field_name`
   - Simpler, more efficient LogQL queries

5. **Updated `monitoring/README.md`**
   - New architecture diagram showing direct HTTP push
   - Updated troubleshooting section
   - Removed Promtail-specific issues

6. **Updated `pyproject.toml`**
   - Added `requests>=2.31.0` dependency for HTTP client

## Architecture Comparison

### Before (File Tailing)
```
API/Worker → Log Files → Promtail → Loki → Grafana
              (macOS Docker issues)
```

### After (Direct Push)
```
API/Worker → Loki (HTTP) → Grafana
     ↓ (backup)
  Log Files
```

## Benefits

1. **Reliability**: No more Promtail position file issues
2. **Platform Independence**: Works on macOS, Linux, Windows
3. **Performance**: Direct push is more efficient than file tailing
4. **Simplicity**: Cleaner LogQL queries (no regex needed)
5. **Maintainability**: One less component to manage

## Migration Notes

- **Promtail is now optional**: Still in docker-compose.yml but not required for metrics
- **Backward compatible**: File logging still works as backup
- **Environment variables**: Can disable Loki handler with `LOKI_ENABLED=false` if needed
- **No breaking changes**: Existing Grafana dashboards work (queries simplified)

## Testing

To verify the fix works:

1. Start monitoring stack: `make monitoring-up`
2. Generate some metrics: `curl "http://localhost:8000/query/1?top_k=5"`
3. Check Loki directly: `curl -s 'http://localhost:3100/loki/api/v1/query?query={job="api"}' | jq`
4. View dashboard: http://localhost:3000/d/adjacent-metrics

## Rollback Plan

If needed, you can disable the Loki handler by setting:
```bash
export LOKI_ENABLED=false
```

This will fall back to file-only logging. Promtail can still be used if needed, though it's no longer required.
