#!/usr/bin/env python3
"""
Baseline metrics collection script.

Runs a sample query workload to generate JSONL metrics for performance analysis.
Use this to establish baseline latency (p50/p95) for each instrumented span.

Usage:
    # Run with default settings (10 queries, output to stdout)
    python scripts/baseline_metrics.py

    # Run with custom settings (recommended: use logs/ directory)
    python scripts/baseline_metrics.py --queries 50 --output logs/metrics.jsonl

    # Analyze the output
    python scripts/analyze_metrics.py logs/metrics.jsonl

    # Or use jq directly
    cat logs/metrics.jsonl | jq -s '.[] | select(.event_type=="span") | {span, duration_ms}'

    # Get p50/p95 for each span
    cat metrics.jsonl | jq -s '
      group_by(.span) |
      map({
        span: .[0].span,
        count: length,
        p50: (sort_by(.duration_ms) | .[length/2 | floor].duration_ms),
        p95: (sort_by(.duration_ms) | .[length*0.95 | floor].duration_ms),
        mean: (map(.duration_ms) | add / length)
      })
    '
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from adjacent.async_inference.config import AsyncConfig
from adjacent.async_inference.query_service import QueryService
from commons.logging_config import configure_metrics_logger


def run_baseline_queries(
    num_queries: int = 10,
    top_k: int = 10,
    skip_inference: bool = True,
) -> None:
    """
    Run baseline query workload.

    Args:
        num_queries: Number of queries to run
        top_k: Number of recommendations per query
        skip_inference: Skip async inference (faster baseline)
    """
    # Load config from environment
    config = AsyncConfig()

    # Initialize query service
    with QueryService(config) as service:
        # Get a sample of product IDs from the database
        # For baseline, we'll need some existing product IDs
        # You may need to adjust this based on your actual data
        print(
            f"# Running {num_queries} queries (top_k={top_k}, skip_inference={skip_inference})",
            file=sys.stderr,
        )
        print(
            f"# Starting baseline collection at {time.strftime('%Y-%m-%d %H:%M:%S')}",
            file=sys.stderr,
        )

        # Example: Query the same product multiple times to measure consistency
        # In production, you'd query different products
        sample_product_ids = [
            # Add your actual product IDs here
            # For demo purposes, using placeholder
            "sample_product_1",
        ]

        if not sample_product_ids:
            print("# ERROR: No sample product IDs configured", file=sys.stderr)
            print(
                "# Edit this script and add real product IDs from your database",
                file=sys.stderr,
            )
            return

        for i in range(num_queries):
            # Cycle through sample products
            product_id = sample_product_ids[i % len(sample_product_ids)]

            try:
                result = service.query(
                    product_id=product_id,
                    top_k=top_k,
                    skip_inference=skip_inference,
                )
                print(
                    f"# Query {i + 1}/{num_queries} completed: "
                    f"{len(result.recommendations)} recommendations",
                    file=sys.stderr,
                )

            except Exception as e:
                print(f"# Query {i + 1}/{num_queries} failed: {e}", file=sys.stderr)
                continue

            # Small delay between queries
            time.sleep(0.1)

        print(
            f"# Baseline collection completed at {time.strftime('%Y-%m-%d %H:%M:%S')}",
            file=sys.stderr,
        )


def main():
    parser = argparse.ArgumentParser(
        description="Run baseline metrics collection for Adjacent query service"
    )
    parser.add_argument(
        "--queries",
        type=int,
        default=10,
        help="Number of queries to run (default: 10)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of recommendations per query (default: 10)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for metrics JSONL (default: stdout)",
    )
    parser.add_argument(
        "--with-inference",
        action="store_true",
        help="Include async inference (slower but more realistic)",
    )

    args = parser.parse_args()

    # Configure clean JSONL logging
    configure_metrics_logger(
        logger_name="adjacent",
        level=logging.INFO,
        output_file=args.output,
    )

    # Also configure the specific module loggers
    configure_metrics_logger(
        "adjacent.async_inference.query_service", logging.INFO, args.output
    )
    configure_metrics_logger("commons.metrics", logging.INFO, args.output)

    print("# Adjacent Baseline Metrics Collection", file=sys.stderr)
    print("# ====================================", file=sys.stderr)
    print(f"# Queries: {args.queries}", file=sys.stderr)
    print(f"# Top-K: {args.top_k}", file=sys.stderr)
    print(f"# Output: {'stdout' if not args.output else args.output}", file=sys.stderr)
    print(f"# Skip inference: {not args.with_inference}", file=sys.stderr)
    print("#", file=sys.stderr)

    try:
        run_baseline_queries(
            num_queries=args.queries,
            top_k=args.top_k,
            skip_inference=not args.with_inference,
        )
    except KeyboardInterrupt:
        print("\n# Interrupted by user", file=sys.stderr)
    except Exception as e:
        print(f"# ERROR: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
