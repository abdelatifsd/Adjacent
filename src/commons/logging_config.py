"""
Logging configuration for clean JSONL metrics output.

Provides helpers to configure loggers that emit clean JSON lines
without extra prefixes like "INFO:" or timestamps.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional


def configure_metrics_logger(
    logger_name: str = "adjacent",
    level: int = logging.INFO,
    output_file: Optional[str] = None,
) -> logging.Logger:
    """
    Configure a logger for clean JSONL metrics output.

    Sets up a logger with a formatter that outputs only the message (no prefixes).
    This ensures metrics events are emitted as clean JSONL lines.

    Args:
        logger_name: Name of the logger to configure (default: "adjacent")
        level: Logging level (default: INFO)
        output_file: Optional file path for output (default: stdout)

    Returns:
        Configured logger instance

    Example:
        >>> from commons.logging_config import configure_metrics_logger
        >>> logger = configure_metrics_logger("adjacent")
        >>> # Now all metrics.emit_event() calls will output clean JSON
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Create handler
    if output_file:
        handler = logging.FileHandler(output_file)
    else:
        handler = logging.StreamHandler(sys.stdout)

    handler.setLevel(level)

    # Clean formatter - just the message, no timestamps or level names
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger.addHandler(handler)

    # Prevent propagation to root logger (avoids duplicate output)
    logger.propagate = False

    return logger


def configure_combined_logger(
    logger_name: str = "adjacent",
    level: int = logging.INFO,
    metrics_file: Optional[str] = None,
    debug_file: Optional[str] = None,
) -> logging.Logger:
    """
    Configure a logger with separate outputs for metrics and debug logs.

    Metrics (INFO level) go to metrics_file (or stdout) with clean JSONL format.
    Debug logs (DEBUG level) go to debug_file with full context.

    This allows you to:
    - Pipe clean JSONL metrics to analysis tools
    - Keep traditional debug logs separate for troubleshooting

    Args:
        logger_name: Name of the logger to configure (default: "adjacent")
        level: Logging level (default: INFO)
        metrics_file: Optional file path for metrics JSONL (default: stdout)
        debug_file: Optional file path for debug logs (default: stderr if level is DEBUG)

    Returns:
        Configured logger instance

    Example:
        >>> from commons.logging_config import configure_combined_logger
        >>> logger = configure_combined_logger(
        ...     "adjacent",
        ...     level=logging.DEBUG,
        ...     metrics_file="metrics.jsonl",
        ...     debug_file="debug.log"
        ... )
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)

    # Remove existing handlers
    logger.handlers.clear()

    # Metrics handler - clean JSONL
    if metrics_file:
        metrics_handler = logging.FileHandler(metrics_file)
    else:
        metrics_handler = logging.StreamHandler(sys.stdout)

    metrics_handler.setLevel(logging.INFO)
    metrics_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(metrics_handler)

    # Debug handler - full context (only if DEBUG level)
    if level == logging.DEBUG and debug_file:
        debug_handler = logging.FileHandler(debug_file)
        debug_handler.setLevel(logging.DEBUG)
        debug_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(debug_handler)

    logger.propagate = False

    return logger
