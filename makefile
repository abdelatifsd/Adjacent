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