"""
Lightweight performance instrumentation for Adjacent.

Produces JSONL structured events for latency breakdown and counters.
Works in CLI scripts and RQ workers without external dependencies.

Usage:
    >>> import logging
    >>> from commons.metrics import span, emit_counter, generate_trace_id
    >>>
    >>> logger = logging.getLogger(__name__)
    >>> trace_id = generate_trace_id()
    >>>
    >>> # Measure a timed operation
    >>> with span("fetch_anchor", operation="query", trace_id=trace_id, logger=logger) as ctx:
    ...     # Do some work
    ...     result = fetch_product(product_id)
    ...     ctx.set_count("products_fetched", 1)
    >>>
    >>> # Emit standalone counter events
    >>> emit_counter("cache_hits", 5, operation="query", trace_id=trace_id, logger=logger)

Examples from QueryService.query:
    >>> # In src/adjacent/async_inference/query_service.py
    >>> from commons.metrics import span, emit_counter, generate_trace_id
    >>>
    >>> def query(self, product_id: str, top_k: int = 10, skip_inference: bool = False):
    ...     trace_id = generate_trace_id()
    ...     logger = logging.getLogger(__name__)
    ...
    ...     with span("query_total", operation="query", trace_id=trace_id, logger=logger,
    ...               product_id=product_id):
    ...         # Fetch anchor
    ...         with span("fetch_anchor", operation="query", trace_id=trace_id, logger=logger):
    ...             anchor = self._fetch_product(product_id)
    ...
    ...         # Get graph neighbors
    ...         with span("graph_neighbors", operation="query", trace_id=trace_id, logger=logger) as ctx:
    ...             neighbors = edge_store.get_neighbors(product_id, limit=top_k)
    ...             ctx.set_count("from_graph", len(neighbors))
    ...
    ...         # Vector search if needed
    ...         if need_more > 0:
    ...             with span("vector_search", operation="query", trace_id=trace_id, logger=logger) as ctx:
    ...                 results = self._vector_store.similarity_search(...)
    ...                 ctx.set_count("from_vector", len(results))
    ...
    ...         # Enqueue inference
    ...         with span("enqueue_inference", operation="query", trace_id=trace_id, logger=logger) as ctx:
    ...             job = self._queue.enqueue(...)
    ...             ctx.set_count("candidates_enqueued", len(new_candidates))
    ...
    ...         emit_counter("top_k", top_k, operation="query", trace_id=trace_id, logger=logger)
    ...         emit_counter("skip_inference", 1 if skip_inference else 0,
    ...                      operation="query", trace_id=trace_id, logger=logger)

Examples from tasks.infer_edges:
    >>> # In src/adjacent/async_inference/tasks.py
    >>> from commons.metrics import span, emit_counter, generate_trace_id
    >>>
    >>> def infer_edges(anchor_id: str, candidate_ids: List[str], config_dict: dict):
    ...     trace_id = generate_trace_id()
    ...     logger = logging.getLogger(__name__)
    ...
    ...     with span("infer_edges_total", operation="infer_edges", trace_id=trace_id,
    ...               logger=logger, anchor_id=anchor_id) as total_ctx:
    ...         # Fetch products
    ...         with span("fetch_products", operation="infer_edges", trace_id=trace_id,
    ...                   logger=logger) as ctx:
    ...             anchor = neo4j_ctx.fetch_product(anchor_id)
    ...             candidates = neo4j_ctx.fetch_products(candidate_ids)
    ...             ctx.set_count("candidates_count", len(candidates))
    ...
    ...         # LLM inference
    ...         with span("llm_call", operation="infer_edges", trace_id=trace_id,
    ...                   logger=logger) as ctx:
    ...             patches = edge_inference.construct_patch(anchor_view, candidate_views)
    ...             ctx.set_count("patches_count", len(patches))
    ...
    ...         # Materialize and upsert
    ...         with span("materialize_and_upsert", operation="infer_edges", trace_id=trace_id,
    ...                   logger=logger) as ctx:
    ...             for patch in patches:
    ...                 edge = materializer.materialize(patch, anchor_id, existing)
    ...                 edge_store.upsert_edge(edge)
    ...             ctx.set_count("edges_created", edges_created)
    ...             ctx.set_count("edges_reinforced", edges_reinforced)
    ...
    ...         # Mark anchor inferred
    ...         with span("mark_anchor_inferred", operation="infer_edges",
    ...                   trace_id=trace_id, logger=logger):
    ...             _mark_anchor_inferred(neo4j_ctx, anchor_id)
    ...
    ...         total_ctx.set_count("edges_created", edges_created)
    ...         total_ctx.set_count("edges_reinforced", edges_reinforced)

IMPORTANT: Attrs safety
    When passing **attrs, only include:
    - Product IDs, edge IDs (strings)
    - Small integers (counts, limits)
    - Booleans (feature flags)
    - Short strings (< 100 chars)

    NEVER include:
    - Full product descriptions or text blobs
    - Embeddings or vector data
    - Large lists of candidates
    - Credentials or tokens
    - PII (user emails, names, etc.)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional, Union

__all__ = [
    "span",
    "emit_event",
    "emit_counter",
    "generate_trace_id",
    "generate_request_id",
]

# Schema version for metrics events
METRICS_SCHEMA_VERSION = "1.0"


def generate_trace_id() -> str:
    """Generate a unique trace ID for tracking related operations.

    Returns:
        A UUID4 string suitable for use as a trace_id.
    """
    return str(uuid.uuid4())


def generate_request_id() -> str:
    """Generate a unique request ID.

    Alias for generate_trace_id() for semantic clarity.

    Returns:
        A UUID4 string suitable for use as a request_id.
    """
    return generate_trace_id()


def _safe_serialize(value: Any, max_str_len: int = 500) -> Any:
    """
    Safely serialize a value for JSON output.

    - Truncates long strings to prevent huge payloads
    - Converts non-serializable types to strings
    - Handles None, numbers, bools, lists, and dicts

    Args:
        value: The value to serialize
        max_str_len: Maximum length for string values

    Returns:
        A JSON-serializable value
    """
    if value is None:
        return None

    if isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        if len(value) > max_str_len:
            return value[:max_str_len] + "..."
        return value

    if isinstance(value, (list, tuple)):
        # Truncate long lists - keep as consistent type
        max_items = 50
        if len(value) > max_items:
            serialized = [_safe_serialize(v, max_str_len) for v in value[:max_items]]
            # Note: appending would change type; instead rely on logging that it was truncated
            return serialized
        return [_safe_serialize(v, max_str_len) for v in value]

    if isinstance(value, dict):
        # Only serialize top-level keys, avoid deep nesting
        return {str(k): _safe_serialize(v, max_str_len) for k, v in value.items()}

    # Fallback: convert to string
    str_value = str(value)
    if len(str_value) > max_str_len:
        return str_value[:max_str_len] + "..."
    return str_value


def emit_event(event: Dict[str, Any], logger: logging.Logger) -> None:
    """
    Emit a single metrics event as JSONL.

    The event is logged as a single-line JSON object at INFO level.
    All values are safely serialized to prevent huge payloads.

    This function does NOT mutate the input event dictionary.

    Args:
        event: The event dictionary to emit (will not be modified)
        logger: Logger instance to use for output
    """
    # Copy event to avoid mutating caller's dict
    safe_event = {**event}

    # Ensure timestamp is present
    if "timestamp" not in safe_event:
        safe_event["timestamp"] = datetime.now(timezone.utc).isoformat()

    # Ensure schema version is present
    if "schema_version" not in safe_event:
        safe_event["schema_version"] = METRICS_SCHEMA_VERSION

    # Safe serialize all values
    serialized = {k: _safe_serialize(v) for k, v in safe_event.items()}

    # Emit as single-line JSON
    logger.info(json.dumps(serialized, separators=(",", ":")))


def emit_counter(
    name: str,
    value: Union[int, float],
    *,
    operation: Optional[str] = None,
    trace_id: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    **attrs: Any,
) -> None:
    """
    Emit a standalone counter event.

    Useful for metrics that don't fit into a span (e.g., configuration values,
    feature flags, or aggregate counts).

    Args:
        name: Counter name
        value: Counter value (int or float)
        operation: Operation name (e.g., "query", "infer_edges")
        trace_id: Optional trace ID for correlation
        logger: Logger instance (uses root logger if not provided)
        **attrs: Additional attributes (IDs, small ints, bools only - see module docstring)
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    event = {
        "event_type": "counter",
        "counter_name": name,
        "value": value,
    }

    if operation:
        event["operation"] = operation

    if trace_id:
        event["trace_id"] = trace_id

    if attrs:
        event["attrs"] = attrs

    emit_event(event, logger)


class SpanContext:
    """
    Context for a timed span.

    Allows setting counts and attributes during the span execution.
    """

    def __init__(
        self,
        name: str,
        operation: Optional[str],
        trace_id: Optional[str],
        logger: logging.Logger,
        attrs: Dict[str, Any],
    ):
        self.name = name
        self.operation = operation
        self.trace_id = trace_id
        self.logger = logger
        self.attrs = attrs
        self.counts: Dict[str, Union[int, float]] = {}
        self.status = "ok"
        self.error_type: Optional[str] = None
        self.error_message: Optional[str] = None
        self.start_time = time.perf_counter()

    def set_count(self, key: str, value: Union[int, float]) -> None:
        """Set a counter value for this span."""
        self.counts[key] = value

    def set_attr(self, key: str, value: Any) -> None:
        """
        Set an attribute for this span.

        WARNING: Only pass IDs, small ints, bools, or short strings.
        Do not pass full text, embeddings, or PII. See module docstring.
        """
        self.attrs[key] = value

    def set_error(self, error: Exception) -> None:
        """
        Mark this span as failed with error information.

        Args:
            error: The exception that occurred
        """
        self.status = "error"
        self.error_type = type(error).__name__
        self.error_message = str(error) or repr(error)

    def _emit(self) -> None:
        """Emit the span event."""
        duration_ms = (time.perf_counter() - self.start_time) * 1000

        event = {
            "event_type": "span",
            "span": self.name,
            "duration_ms": round(duration_ms, 2),
            "status": self.status,
        }

        if self.operation:
            event["operation"] = self.operation

        if self.trace_id:
            event["trace_id"] = self.trace_id

        if self.error_type:
            event["error_type"] = self.error_type
        if self.error_message:
            event["error_message"] = self.error_message

        if self.attrs:
            event["attrs"] = self.attrs

        if self.counts:
            event["counts"] = self.counts

        emit_event(event, self.logger)


@contextmanager
def span(
    name: str,
    *,
    operation: Optional[str] = None,
    trace_id: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    **attrs: Any,
) -> Iterator[SpanContext]:
    """
    Context manager for measuring operation duration.

    Automatically emits a span event with duration when the context exits.
    Use the yielded SpanContext to set counts and attributes during execution.

    Args:
        name: Span name (e.g., "fetch_anchor", "llm_call")
        operation: Operation name (e.g., "query", "infer_edges", "ingest")
        trace_id: Optional trace ID for correlation across operations
        logger: Logger instance (uses root logger if not provided)
        **attrs: Additional attributes (IDs, small ints, bools only - see module docstring)

    Yields:
        SpanContext instance for setting counts and attributes

    Example:
        >>> with span("fetch_products", operation="query", trace_id=trace_id,
        ...           logger=logger, product_id="abc123") as ctx:
        ...     products = fetch_many(ids)
        ...     ctx.set_count("products_fetched", len(products))
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    ctx = SpanContext(name, operation, trace_id, logger, attrs)

    try:
        yield ctx
    except Exception as e:
        ctx.set_error(e)
        raise
    finally:
        ctx._emit()
