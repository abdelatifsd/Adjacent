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
# Complete Pipeline
# ----------------------------
pipeline:
	@echo "Running complete pipeline: preprocess → ingest → embed"
	@$(MAKE) ingest
	@$(MAKE) embed