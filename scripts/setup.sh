#!/bin/bash

set -e  # Exit on any error

echo "🚀 Adjacent Setup"
echo "============================================"
echo ""

# Check Docker
echo "📦 Checking prerequisites..."
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is required but not installed"
    echo "   Install from: https://docs.docker.com/get-docker/"
    exit 1
fi
echo "  ✓ Docker found"

# Check Docker is running
if ! docker info &> /dev/null; then
    echo "❌ Docker is installed but not running"
    echo "   Please start Docker and try again"
    exit 1
fi
echo "  ✓ Docker is running"

# Check uv
if ! command -v uv &> /dev/null; then
    echo "❌ uv is required but not installed"
    echo "   Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
echo "  ✓ uv found"

# Check Python version (compatible with macOS BSD tools)
PYTHON_VERSION=$(uv run python --version 2>&1 | sed -E 's/Python ([0-9]+\.[0-9]+).*/\1/')
REQUIRED_MAJOR=3
REQUIRED_MINOR=11

# Extract major and minor versions
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt "$REQUIRED_MAJOR" ] || \
   ([ "$PYTHON_MAJOR" -eq "$REQUIRED_MAJOR" ] && [ "$PYTHON_MINOR" -lt "$REQUIRED_MINOR" ]); then
    echo "❌ Python 3.11+ is required (found: $PYTHON_VERSION)"
    exit 1
fi
echo "  ✓ Python $PYTHON_VERSION found"

echo ""
echo "📥 Installing Python dependencies..."
uv sync

echo ""
echo "📝 Setting up environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  ✓ Created .env file from .env.example"
    echo ""
    echo "⚠️  IMPORTANT: Add your OpenAI API key to .env"
    echo "   Edit .env and set: OPENAI_API_KEY=sk-your-key-here"
    echo ""
else
    echo "  ✓ .env file already exists"
fi

# Create logs directory
mkdir -p logs
echo "  ✓ Created logs directory"

echo ""
echo "============================================"
echo "✅ Setup Complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Add your OpenAI API key to .env (if not done)"
echo "  2. Run: make dev"
echo ""
echo "This will:"
echo "  • Start all infrastructure (Neo4j, Redis, monitoring)"
echo "  • Ingest demo data"
echo "  • Embed products"
echo "  • Start API and worker"
echo ""
echo "Access points after 'make dev':"
echo "  • API docs:  http://localhost:8000/docs"
echo "  • Grafana:   http://localhost:3000 (admin/admin)"
echo "  • Neo4j:     http://localhost:7475 (neo4j/adjacent123)"
echo ""
