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
		--neo4j-password adjacent123 \
		--limit 10

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
# Recommender
# ----------------------------
# Example: make recommend PRODUCT_ID=some_product_id
recommend:
	@test -n "$(PRODUCT_ID)" || (echo "Error: PRODUCT_ID not set. Usage: make recommend PRODUCT_ID=your_id" && exit 1)
	PYTHONPATH=src $(PYTHON) -m adjacent.recommender $(PRODUCT_ID) \
		--neo4j-uri bolt://localhost:7688 \
		--neo4j-user neo4j \
		--neo4j-password adjacent123 \
		--embedding-provider huggingface

# With LLM inference (requires OPENAI_API_KEY)
recommend-llm:
	@test -n "$(PRODUCT_ID)" || (echo "Error: PRODUCT_ID not set. Usage: make recommend-llm PRODUCT_ID=your_id" && exit 1)
	@test -n "$(OPENAI_API_KEY)" || (echo "Error: OPENAI_API_KEY not set" && exit 1)
	PYTHONPATH=src $(PYTHON) -m adjacent.recommender $(PRODUCT_ID) \
		--neo4j-uri bolt://localhost:7688 \
		--neo4j-user neo4j \
		--neo4j-password adjacent123 \
		--embedding-provider huggingface \
		--openai-api-key $(OPENAI_API_KEY)

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
	PYTHONPATH=src uv run rq worker adjacent_inference \
		--url redis://localhost:6379/0 \
		--with-scheduler

# Monitor the queue (requires rq-dashboard: pip install rq-dashboard)
worker-dashboard:
	@echo "Starting RQ dashboard at http://localhost:9181"
	uv run rq-dashboard --redis-url redis://localhost:6379/0

# Check queue status
queue-status:
	PYTHONPATH=src uv run rq info --url redis://localhost:6379/0

# ----------------------------
# Async Query (fast path + background inference)
# ----------------------------
# Example: make query-async PRODUCT_ID=some_product_id
query-async:
	@test -n "$(PRODUCT_ID)" || (echo "Error: PRODUCT_ID not set. Usage: make query-async PRODUCT_ID=your_id" && exit 1)
	PYTHONPATH=src $(PYTHON) -c "\
from adjacent.async_inference import QueryService, AsyncConfig; \
import os; \
config = AsyncConfig(openai_api_key=os.environ.get('OPENAI_API_KEY')); \
with QueryService(config) as svc: \
    result = svc.query('$(PRODUCT_ID)'); \
    print('Anchor:', result.anchor_id); \
    print('From graph:', result.from_graph); \
    print('From vector:', result.from_vector); \
    print('Inference:', result.inference_status, result.job_id or ''); \
    print('Recommendations:'); \
    for r in result.recommendations: \
        print(f'  - {r.product_id} ({r.source}, conf={r.confidence})')"

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