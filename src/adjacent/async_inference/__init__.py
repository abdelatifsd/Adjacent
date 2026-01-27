"""Async inference infrastructure using Redis Queue (RQ)."""

from adjacent.async_inference.config import AsyncConfig
from adjacent.async_inference.query_service import QueryService, QueryResult

# Note: tasks.infer_edges is NOT imported here to avoid triggering
# worker logging config when API imports this package.
# Workers import tasks directly via RQ task path.

__all__ = [
    "AsyncConfig",
    "QueryService",
    "QueryResult",
]
