#!/bin/bash
# Monitoring stack health check script

set -e

echo "============================================"
echo "Adjacent Monitoring Stack Health Check"
echo "============================================"
echo ""

# Check if containers are running
echo "[1/6] Checking Docker containers..."
LOKI_RUNNING=$(docker ps --filter "name=adjacent-loki" --format "{{.Status}}" | grep -c "Up" || echo "0")
GRAFANA_RUNNING=$(docker ps --filter "name=adjacent-grafana" --format "{{.Status}}" | grep -c "Up" || echo "0")
PROMTAIL_RUNNING=$(docker ps --filter "name=adjacent-promtail" --format "{{.Status}}" | grep -c "Up" || echo "0")

if [ "$LOKI_RUNNING" = "1" ]; then
  echo "  ✓ Loki is running"
else
  echo "  ✗ Loki is NOT running"
  exit 1
fi

if [ "$GRAFANA_RUNNING" = "1" ]; then
  echo "  ✓ Grafana is running"
else
  echo "  ✗ Grafana is NOT running"
  exit 1
fi

if [ "$PROMTAIL_RUNNING" = "1" ]; then
  echo "  ✓ Promtail is running"
else
  echo "  ✗ Promtail is NOT running"
  exit 1
fi

# Check Loki readiness
echo ""
echo "[2/6] Checking Loki readiness..."
LOKI_READY=$(curl -s http://localhost:3100/ready 2>/dev/null || echo "error")
if echo "$LOKI_READY" | grep -q "ready"; then
  echo "  ✓ Loki is ready"
elif echo "$LOKI_READY" | grep -q "Ingester not ready"; then
  echo "  ⚠ Loki ingester is warming up (this is normal for ~15s after start)"
else
  echo "  ✗ Loki is NOT ready: $LOKI_READY"
fi

# Check Grafana
echo ""
echo "[3/6] Checking Grafana..."
GRAFANA_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/health 2>/dev/null || echo "000")
if [ "$GRAFANA_STATUS" = "200" ]; then
  echo "  ✓ Grafana is accessible at http://localhost:3000"
else
  echo "  ✗ Grafana is NOT accessible (HTTP $GRAFANA_STATUS)"
fi

# Check log files
echo ""
echo "[4/6] Checking log files..."
if [ -f "logs/api.log" ]; then
  API_LOG_SIZE=$(wc -c < logs/api.log)
  API_LOG_LINES=$(wc -l < logs/api.log)
  echo "  ✓ api.log exists ($API_LOG_SIZE bytes, $API_LOG_LINES lines)"
else
  echo "  ⚠ api.log does not exist yet"
fi

if [ -f "logs/worker.log" ]; then
  WORKER_LOG_SIZE=$(wc -c < logs/worker.log)
  WORKER_LOG_LINES=$(wc -l < logs/worker.log)
  echo "  ✓ worker.log exists ($WORKER_LOG_SIZE bytes, $WORKER_LOG_LINES lines)"
else
  echo "  ⚠ worker.log does not exist yet"
fi

# Check Promtail positions
echo ""
echo "[5/6] Checking Promtail ingestion..."
docker exec adjacent-promtail cat /tmp/positions.yaml 2>/dev/null | while IFS=: read -r file position; do
  file=$(echo "$file" | xargs)
  position=$(echo "$position" | tr -d '"' | xargs)
  if [ -n "$file" ] && [ "$file" != "positions" ]; then
    echo "  → $file: $position bytes ingested"
  fi
done

# Check if data is in Loki
echo ""
echo "[6/6] Checking data in Loki..."
API_COUNT=$(curl -s 'http://localhost:3100/loki/api/v1/query?query=count_over_time({job="api"}[24h])' 2>/dev/null | grep -o '"result":\[.*\]' | grep -o '\[.*\]' | grep -c '\[' || echo "0")
WORKER_COUNT=$(curl -s 'http://localhost:3100/loki/api/v1/query?query=count_over_time({job="worker"}[24h])' 2>/dev/null | grep -o '"result":\[.*\]' | grep -o '\[.*\]' | grep -c '\[' || echo "0")

if [ "$API_COUNT" != "0" ]; then
  echo "  ✓ API logs are in Loki"
else
  echo "  ⚠ No API logs found in Loki (may need to generate some)"
fi

if [ "$WORKER_COUNT" != "0" ]; then
  echo "  ✓ Worker logs are in Loki"
else
  echo "  ⚠ No worker logs found in Loki (may need to generate some)"
fi

echo ""
echo "============================================"
echo "Health check complete!"
echo ""
echo "Next steps:"
echo "  - View logs: http://localhost:3000/explore"
echo "  - View dashboard: http://localhost:3000/d/adjacent-metrics"
echo "  - Credentials: admin / admin"
echo "============================================"
