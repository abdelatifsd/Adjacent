# src/adjacent/api/__init__.py
"""FastAPI reference server for Adjacent QueryService."""

from adjacent.api.app import app, create_app
from adjacent.api.routes import router

__all__ = ["app", "create_app", "router"]
