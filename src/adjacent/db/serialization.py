"""Convert Neo4j result data to JSON-serializable form.

Neo4j returns temporal types (DateTime, Date, Time, Duration) that are not
JSON-serializable. This module provides a sanitizer so any code that returns
Neo4j data to clients (MCP, API) can use it and avoid serialization errors.
"""

from __future__ import annotations

from typing import Any

try:
    import neo4j.time as neo4j_time
except ImportError:
    neo4j_time = None  # type: ignore[assignment]


def sanitize_for_json(obj: Any) -> Any:
    """Convert Neo4j temporal types and nested structures to JSON-serializable form.

    Recursively walks dicts and lists; converts neo4j.time.DateTime, Date, Time
    to ISO strings; converts Duration to string. All other values are returned
    unchanged. Use this on any dict/list returned from Neo4j before sending
    to MCP, API, or json.dumps().

    Args:
        obj: Value that may contain Neo4j types (e.g. from session.run() result).

    Returns:
        Same structure with Neo4j temporal types replaced by strings.
    """
    if neo4j_time is not None:
        if isinstance(obj, (neo4j_time.DateTime, neo4j_time.Date, neo4j_time.Time)):
            return obj.iso_format()
        if isinstance(obj, neo4j_time.Duration):
            return str(obj)
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    return obj
