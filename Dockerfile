# Dockerfile for Adjacent API and Worker services
# Using bookworm (Debian 12) for better security updates
FROM python:3.11-slim-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy dependency files and README (needed for package build)
COPY pyproject.toml uv.lock README.md ./

# Install Python dependencies
RUN uv sync --frozen

# Copy application code (will be overridden by volume mounts in development)
COPY src/ ./src/
COPY schemas/ ./schemas/

# Create necessary directories
RUN mkdir -p /app/logs

# Note: In development, src/ and schemas/ are volume-mounted for hot reload
# Prompts are inside src/adjacent/prompts/ so they're included with src/

# Set Python path
ENV PYTHONPATH=/app/src

# Default command (will be overridden by docker-compose)
CMD ["echo", "Specify a command in docker-compose.yml"]
