"""
Loki HTTP handler for direct log pushing.

This handler pushes logs directly to Loki via HTTP, eliminating the need for
Promtail file tailing. This solves the macOS Docker Desktop issue where
inotify events don't reliably propagate through bind mounts.

Usage:
    >>> import logging
    >>> from commons.loki_handler import LokiHandler
    >>>
    >>> handler = LokiHandler(
    ...     url="http://localhost:3100/loki/api/v1/push",
    ...     job="api",
    ...     batch_size=10,
    ...     flush_interval=5.0
    ... )
    >>> logger = logging.getLogger("adjacent")
    >>> logger.addHandler(handler)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore


class LokiHandler(logging.Handler):
    """
    Logging handler that pushes logs directly to Loki via HTTP.

    Features:
    - Batches logs for efficiency
    - Handles errors gracefully (logs to stderr if Loki unavailable)
    - Supports labels for job identification
    - Thread-safe
    - Automatic batching and flushing

    Args:
        url: Loki push endpoint URL (default: from LOKI_URL env var or http://localhost:3100/loki/api/v1/push)
        job: Job label (e.g., "api", "worker") - required
        batch_size: Number of logs to batch before sending (default: 10)
        flush_interval: Seconds between automatic flushes (default: 5.0)
        timeout: HTTP request timeout in seconds (default: 5.0)
        enabled: Whether handler is enabled (default: True, or from LOKI_ENABLED env var)
    """

    def __init__(
        self,
        url: Optional[str] = None,
        job: Optional[str] = None,
        batch_size: int = 10,
        flush_interval: float = 5.0,
        timeout: float = 5.0,
        enabled: Optional[bool] = None,
    ):
        super().__init__()

        if requests is None:
            raise ImportError(
                "requests library is required for LokiHandler. "
                "Install with: pip install requests"
            )

        # Get URL from env or use default
        self.url = url or os.getenv(
            "LOKI_URL", "http://localhost:3100/loki/api/v1/push"
        )

        # Get job from env or parameter
        self.job = job or os.getenv("LOKI_JOB", "adjacent")

        # Check if enabled (default: True)
        if enabled is None:
            enabled_str = os.getenv("LOKI_ENABLED", "true").lower()
            self.enabled = enabled_str in ("true", "1", "yes", "on")
        else:
            self.enabled = enabled

        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.timeout = timeout

        # Thread-safe batch queue
        self._batch: deque[Dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._last_flush = time.time()

        # Start background flush thread
        self._flush_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        if self.enabled:
            self._start_flush_thread()

    def _start_flush_thread(self) -> None:
        """Start background thread for periodic flushing."""
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="LokiHandler-flush"
        )
        self._flush_thread.start()

    def _flush_loop(self) -> None:
        """Background thread that periodically flushes batches."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self.flush_interval)
            if not self._stop_event.is_set():
                self.flush()

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record to Loki.

        This method is called by Python's logging system for each log record.
        """
        if not self.enabled:
            return

        try:
            # Format the message
            message = self.format(record)

            # Create Loki log entry
            # Use nanoseconds since epoch for timestamp
            timestamp_ns = int(record.created * 1_000_000_000)

            log_entry = {
                "stream": {
                    "job": self.job,
                    "level": record.levelname.lower(),
                    "logger": record.name,
                },
                "values": [[str(timestamp_ns), message]],
            }

            # Add to batch
            with self._lock:
                self._batch.append(log_entry)

                # Flush if batch is full
                if len(self._batch) >= self.batch_size:
                    self._flush_unsafe()

        except Exception:
            # Don't let logging errors break the application
            # Log to stderr as fallback
            self.handleError(record)

    def flush(self) -> None:
        """Flush any pending logs to Loki."""
        if not self.enabled:
            return

        with self._lock:
            self._flush_unsafe()

    def _flush_unsafe(self) -> None:
        """Flush batch to Loki (must be called with lock held)."""
        if not self._batch:
            return

        # Prepare batch payload
        streams = list(self._batch)
        self._batch.clear()

        payload = {"streams": streams}

        try:
            # Send to Loki
            response = requests.post(
                self.url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()

        except requests.exceptions.RequestException as e:
            # Log error but don't break application
            # In production, you might want to retry or queue for later
            import sys

            print(
                f"LokiHandler: Failed to push logs to {self.url}: {e}",
                file=sys.stderr,
            )

    def close(self) -> None:
        """Close the handler and flush any pending logs."""
        # Stop flush thread
        if self._flush_thread:
            self._stop_event.set()
            self._flush_thread.join(timeout=2.0)

        # Final flush
        self.flush()

        super().close()


def configure_loki_logging(
    logger_name: str = "adjacent",
    job: Optional[str] = None,
    level: int = logging.INFO,
    url: Optional[str] = None,
    enabled: Optional[bool] = None,
    format_string: Optional[str] = None,
) -> logging.Logger:
    """
    Configure a logger with Loki handler for direct log pushing.

    This is a convenience function that sets up a logger with:
    - Loki handler for direct HTTP pushing
    - Clean JSON format for metrics (no prefixes)

    Args:
        logger_name: Name of the logger (default: "adjacent")
        job: Job label for Loki (default: from LOKI_JOB env var or "adjacent")
        level: Logging level (default: INFO)
        url: Loki push endpoint URL (default: from LOKI_URL env var)
        enabled: Whether Loki handler is enabled (default: from LOKI_ENABLED env var)
        format_string: Log format string (default: "%(message)s" for clean JSON)

    Returns:
        Configured logger instance

    Example:
        >>> from commons.loki_handler import configure_loki_logging
        >>> logger = configure_loki_logging("adjacent", job="api")
        >>> # Now all metrics.emit_event() calls will push directly to Loki
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Create Loki handler
    if enabled is None:
        enabled_str = os.getenv("LOKI_ENABLED", "true").lower()
        enabled = enabled_str in ("true", "1", "yes", "on")

    if enabled:
        try:
            loki_handler = LokiHandler(url=url, job=job, enabled=enabled)
            loki_handler.setLevel(level)

            # Use clean format for metrics (just the message, no prefixes)
            if format_string is None:
                format_string = "%(message)s"
            formatter = logging.Formatter(format_string)
            loki_handler.setFormatter(formatter)

            logger.addHandler(loki_handler)
        except Exception as e:
            # If Loki handler fails to initialize, log warning but continue
            import sys

            print(
                f"Warning: Failed to initialize Loki handler: {e}",
                file=sys.stderr,
            )

    # Prevent propagation to root logger
    logger.propagate = False

    return logger
