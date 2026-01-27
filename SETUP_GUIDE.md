# Adjacent Setup Guide

This guide covers the new Docker Compose workflow for Adjacent.

## Overview

The project now supports two workflows:

1. **Docker Compose (Recommended)** - Single command, everything in containers
2. **Native Python** - Multiple terminals, for advanced debugging

## Quick Start (Docker Compose)

### First Time Setup

```bash
# 1. Run setup script
./scripts/setup.sh

# 2. Add your OpenAI API key
echo "OPENAI_API_KEY=sk-your-key-here" >> .env

# 3. Start everything
make dev
```

### What `make dev` Does

1. Stops any existing services
2. Starts infrastructure (Neo4j, Redis, Grafana, Loki, Promtail)
3. Waits for services to be healthy
4. Ingests demo data into Neo4j
5. Embeds products using HuggingFace
6. Starts API server and worker

### Daily Development

```bash
# Start everything
make dev

# View logs (all services)
make dev-logs

# Check service health
make dev-status

# Stop everything (keeps data)
make dev-down

# Clean everything (removes volumes)
make dev-clean
```

## Architecture

### Services

- **neo4j** - Graph database
- **redis** - Job queue
- **api** - FastAPI server (port 8000)
- **worker** - RQ worker for async inference
- **loki** - Log aggregation
- **promtail** - Log shipping
- **grafana** - Monitoring dashboard (port 3000)

### Volumes

All data is stored in named volumes:
- `adjacent-neo4j-data` - Graph database
- `adjacent-redis-data` - Queue state
- `adjacent-loki-data` - Logs
- `adjacent-grafana-data` - Dashboards

To clean volumes: `make dev-clean` or `docker compose down -v`

### Hot Reload

The API and worker containers mount `src/` and `schemas/` as volumes. Changes are automatically picked up:

- **API**: Uvicorn auto-reloads on file changes
- **Worker**: May need restart for some changes (`docker compose restart worker`)

## Advanced: Native Python Workflow

For debugging or when you need more control:

```bash
# Terminal 1: Infrastructure + monitoring
make reset-full

# Terminal 2: API server
make api-start

# Terminal 3: Worker
make worker
```

This gives you:
- Direct access to Python debugger
- Easier log filtering
- More control over individual services

## Environment Variables

The [.env](.env) file controls configuration:

```bash
# Required for LLM inference
OPENAI_API_KEY=sk-your-key-here

# Optional (defaults shown)
NEO4J_URI=bolt://localhost:7688
NEO4J_USER=neo4j
NEO4J_PASSWORD=adjacent123
REDIS_URL=redis://localhost:6379/0
```

In Docker Compose, these are passed to containers via [docker-compose.yml](docker-compose.yml).

## Data Pipeline

### Ingestion

```bash
make ingest
```

Loads demo data from [data/demo/kaggle_ecommerce.json](data/demo/kaggle_ecommerce.json) into Neo4j.

### Embedding

```bash
# HuggingFace (default, runs locally)
make embed

# OpenAI (requires OPENAI_API_KEY)
make embed-openai
```

Both work with Docker Compose or native setup.

## Troubleshooting

### Services won't start

```bash
# Check Docker is running
docker info

# Clean and restart
make dev-clean
make dev
```

### Port conflicts

If ports 7475, 7688, 6379, 8000, 3000, or 3100 are in use:

```bash
# Find conflicting process
lsof -i :8000

# Stop old containers
docker ps -a | grep adjacent
docker rm -f <container-id>
```

### Volume leaks

The makefile now uses `docker compose down -v` to prevent volume leaks. If you see orphaned volumes:

```bash
# List all volumes
docker volume ls | grep adjacent

# Remove specific volume
docker volume rm adjacent-neo4j-data

# Or clean everything
make dev-clean
```

### Container logs

```bash
# All services
make dev-logs

# Specific service
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f neo4j
```

### Neo4j connection issues

```bash
# Check Neo4j health
docker exec adjacent-neo4j cypher-shell -u neo4j -p adjacent123 "RETURN 1"

# Check logs
docker logs adjacent-neo4j
```

## Performance Considerations

### macOS Docker Desktop

- Volumes use `:consistent` flag for better performance
- File sync may have slight delay
- Use `docker compose logs -f` to monitor changes

### Building Images

```bash
# Build from scratch
make dev-build

# No cache (after dependency changes)
docker compose build --no-cache
```

## Migration from Old Setup

If you were using the old workflow:

**Before:**
```bash
make reset-full    # Terminal 1
make api-start     # Terminal 2
make worker        # Terminal 3
```

**After:**
```bash
make dev           # One command
```

The old commands still work if you prefer native Python development.

## Next Steps

1. Read [docs/system_dynamics.md](docs/system_dynamics.md) - Understanding cold start behavior
2. Try the API: [http://localhost:8000/docs](http://localhost:8000/docs)
3. Check monitoring: [http://localhost:3000](http://localhost:3000)
4. Query recommendations: `curl http://localhost:8000/v1/system/status | jq`
