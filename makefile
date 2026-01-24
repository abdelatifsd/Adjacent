.PHONY: tree validate setup test clean

# Use 'uv run' to ensure the environment is synced and used correctly
PYTHON := uv run python
CHECK_JSONSCHEMA := uv run check-jsonschema

setup:
	uv sync

tree:
	tree -L 5 -I '.git|.venv|__pycache__'

# In your NEW project Makefile
neo4j-start:
	@echo "Starting Neo4j for adjacent"
	@docker run \
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
		OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES PYTHONPATH=src uv run rq worker adjacent_inference \
			--url redis://localhost:6379/0 \
			--with-scheduler; \
	else \
		echo "Warning: .env file not found"; \
		OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES PYTHONPATH=src uv run rq worker adjacent_inference \
			--url redis://localhost:6379/0 \
			--with-scheduler; \
	fi

# Monitor the queue (requires rq-dashboard: pip install rq-dashboard)
worker-dashboard:
	@echo "Starting RQ dashboard at http://localhost:9181"
	uv run rq-dashboard --redis-url redis://localhost:6379/0

# ----------------------------
# Complete Pipeline
# ----------------------------
pipeline:
	@echo "Running complete pipeline: preprocess → ingest → embed"
	@$(MAKE) ingest
	@$(MAKE) embed

pipeline-async:
	@echo "Running async pipeline: neo4j + redis + ingest + embed"
	@$(MAKE) neo4j-start
	@$(MAKE) redis-start
	@sleep 5
	@$(MAKE) ingest
	@$(MAKE) embed
	@echo "Start worker with: make worker"

# ----------------------------
# Quick Start (for testing)
# ----------------------------
dev:
	@echo "Starting development environment..."
	@echo "This will start Neo4j and Redis if not already running"
	@$(MAKE) neo4j-start || docker start adjacent-neo4j || true
	@$(MAKE) redis-start || true
	@sleep 3
	@echo ""
	@echo "Services ready! Now starting API server..."
	@echo "Run 'make worker' in another terminal to enable async inference"
	@echo ""
	@$(MAKE) api-start