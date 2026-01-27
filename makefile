.PHONY: tree validate setup test clean monitoring-up monitoring-down monitoring-logs reset reset-full

# Use 'uv run' to ensure the environment is synced and used correctly
PYTHON := uv run python
CHECK_JSONSCHEMA := uv run check-jsonschema

setup:
	uv sync

reset:
	@echo "Resetting all data and containers..."
	@docker stop adjacent-neo4j adjacent-redis 2>/dev/null || true
	@docker rm adjacent-neo4j adjacent-redis 2>/dev/null || true
	@rm -rf neo4j-data
	@echo "Clearing logs..."
	@rm -f logs/api.log logs/worker.log
	@echo "Starting fresh Neo4j and Redis..."
	@$(MAKE) neo4j-start
	@echo "Waiting for Neo4j to be ready..."
	@sleep 10
	@$(MAKE) redis-start
	@sleep 2
	@echo "Ingesting and embedding data..."
	@$(MAKE) ingest
	@$(MAKE) embed
	@echo ""
	@echo "Reset complete! Run 'make api-start' and 'make worker' to start services."

reset-full:
	@echo "============================================"
	@echo "FULL RESET: Stopping all services"
	@echo "============================================"
	@echo ""
	@echo "Stopping API server (uvicorn)..."
	@pkill -f "uvicorn adjacent.api.app" 2>/dev/null || echo "  (no API process running)"
	@echo "Stopping RQ worker..."
	@pkill -f "rq worker adjacent_inference" 2>/dev/null || echo "  (no worker process running)"
	@echo "Stopping monitoring stack..."
	@docker compose down -v 2>/dev/null || echo "  (monitoring stack not running)"
	@echo "Waiting for processes to terminate..."
	@sleep 2
	@echo ""
	@echo "============================================"
	@echo "Resetting data and infrastructure"
	@echo "============================================"
	@$(MAKE) reset
	@echo ""
	@echo "============================================"
	@echo "Starting monitoring stack"
	@echo "============================================"
	@$(MAKE) monitoring-up
	@echo ""
	@echo "============================================"
	@echo "FULL RESET COMPLETE"
	@echo "============================================"
	@echo ""
	@echo "Infrastructure ready:"
	@echo "  ✓ Neo4j:    bolt://localhost:7688"
	@echo "  ✓ Redis:    redis://localhost:6379"
	@echo "  ✓ Grafana:  http://localhost:3000 (admin/admin)"
	@echo ""
	@echo "Next steps (run in separate terminals):"
	@echo "  1. make api-start    # Terminal 1: Start API server"
	@echo "  2. make worker       # Terminal 2: Start RQ worker"
	@echo ""

tree:
	tree -L 5 -I '.git|.venv|__pycache__'

# In your NEW project Makefile
neo4j-start:
	@echo "Starting Neo4j for adjacent"
	@docker run -d \
	--name adjacent-neo4j \
	-p 7475:7474 -p 7688:7687 \
	-e NEO4J_AUTH=neo4j/adjacent123 \
	-v $$(PWD)/neo4j-data:/data \
	neo4j:5

format:
	@echo "Running Ruff Linter (Sorting imports)..."
	@uv run ruff check --fix .
	@echo "Running Ruff Formatter (Black style)..."
	@uv run ruff format .


# ----------------------------
# Ingestion
# ----------------------------
ingest:
	@echo "Ingesting demo data into Neo4j..."
	$(PYTHON) -m src.adjacent.graph.ingest \
		--input data/demo/kaggle_ecommerce.json \
		--schema schemas/product.json \
		--neo4j-uri bolt://localhost:7688 \
		--neo4j-user neo4j \
		--neo4j-password adjacent123 

# ----------------------------
# Embedding Pipeline
# ----------------------------

embed:
	@echo "Embedding ALL products in Neo4j..."
	PYTHONPATH=src $(PYTHON) -m adjacent.graph.embed \
		--provider huggingface \
		--neo4j-uri bolt://localhost:7688 \
		--neo4j-user neo4j \
		--neo4j-password adjacent123

embed-openai:
	@echo "Embedding with OpenAI..."
	@test -n "$(OPENAI_API_KEY)" || (echo "Error: OPENAI_API_KEY not set" && exit 1)
	PYTHONPATH=src $(PYTHON) -m adjacent.graph.embed \
		--provider openai \
		--api-key $(OPENAI_API_KEY) \
		--neo4j-uri bolt://localhost:7688 \
		--neo4j-user neo4j \
		--neo4j-password adjacent123

# ----------------------------
# FastAPI Server
# ----------------------------
api-start:
	@echo "Starting Adjacent API server at http://localhost:8000"
	@echo "Interactive docs available at:"
	@echo "  - Swagger UI: http://localhost:8000/docs"
	@echo "  - ReDoc:      http://localhost:8000/redoc"
	@if [ -f .env ]; then \
		echo "Loading environment from .env file..."; \
		export $$(cat .env | grep -v '^#' | xargs) && \
		PYTHONPATH=src uv run uvicorn adjacent.api.app:app --reload --host 0.0.0.0 --port 8000; \
	else \
		echo "Warning: .env file not found, using defaults"; \
		PYTHONPATH=src uv run uvicorn adjacent.api.app:app --reload --host 0.0.0.0 --port 8000; \
	fi

# ----------------------------
# Async Infrastructure (Redis + RQ)
# ----------------------------
redis-start:
	@echo "Starting Redis for async inference queue"
	@docker run --name adjacent-redis -p 6379:6379 -d redis:7-alpine || docker start adjacent-redis

redis-stop:
	@docker stop adjacent-redis || true

# Start the RQ worker (processes inference tasks)
worker:
	@echo "Starting RQ worker for inference tasks..."
	@if [ -f .env ]; then \
		echo "Loading environment from .env file..."; \
		export $$(cat .env | grep -v '^#' | xargs) && \
		NO_COLOR=1 OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES PYTHONPATH=src uv run rq worker adjacent_inference \
			--url redis://localhost:6379/0 \
			--with-scheduler; \
	else \
		echo "Warning: .env file not found"; \
		NO_COLOR=1 OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES PYTHONPATH=src uv run rq worker adjacent_inference \
			--url redis://localhost:6379/0 \
			--with-scheduler; \
	fi

# Monitor the queue (requires rq-dashboard: pip install rq-dashboard)
worker-dashboard:
	@echo "Starting RQ dashboard at http://localhost:9181"
	uv run rq-dashboard --redis-url redis://localhost:6379/0


# ----------------------------
# Monitoring (Grafana + Loki)
# ----------------------------
monitoring-up:
	@echo "Starting monitoring stack (Grafana + Loki + Promtail)..."
	@mkdir -p logs
	@docker compose up -d
	@echo ""
	@echo "Monitoring services started:"
	@echo "  - Grafana:  http://localhost:3000 (admin/admin)"
	@echo "  - Loki:     http://localhost:3100"
	@echo ""
	@echo "Try these LogQL queries in Grafana Explore:"
	@echo '  {job="api"} | json'
	@echo '  {job="worker"} | json | span="llm_call"'
	@echo '  {job=~"api|worker"} | json | trace_id="<your-trace-id>"'

monitoring-down:
	@echo "Stopping monitoring stack..."
	@docker compose down

monitoring-logs:
	@docker compose logs -f

monitoring-status:
	@bash scripts/check_monitoring.sh