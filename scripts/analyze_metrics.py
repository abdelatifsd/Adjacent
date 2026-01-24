#!/usr/bin/env python3
"""
Analyze metrics JSONL output.

Computes p50, p95, mean, and other statistics for each instrumented span.

Usage:
    # Analyze metrics from file
    python scripts/analyze_metrics.py metrics.jsonl

    # Analyze from stdin
    python scripts/baseline_metrics.py | python scripts/analyze_metrics.py

    # Pretty print with jq (requires jq installed)
    cat metrics.jsonl | jq -s 'group_by(.span) | map({span: .[0].span, count: length})'
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def load_metrics(file_path: str | None) -> List[Dict[str, Any]]:
    """Load metrics from JSONL file or stdin."""
    events = []

    if file_path:
        with open(file_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"Warning: Skipping invalid JSON line: {e}", file=sys.stderr)
    else:
        for line in sys.stdin:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"Warning: Skipping invalid JSON line: {e}", file=sys.stderr)

    return events


def analyze_spans(events: List[Dict[str, Any]]) -> None:
    """Analyze span timing statistics."""
    # Group by span name
    span_data = defaultdict(list)

    for event in events:
        if event.get("event_type") == "span":
            span_name = event.get("span")
            duration_ms = event.get("duration_ms")
            if span_name and duration_ms is not None:
                span_data[span_name].append({
                    "duration_ms": duration_ms,
                    "status": event.get("status", "ok"),
                    "operation": event.get("operation"),
                })

    if not span_data:
        print("No span events found in metrics")
        return

    print("\n" + "="*80)
    print("SPAN TIMING ANALYSIS")
    print("="*80 + "\n")

    # Compute statistics for each span
    for span_name, data in sorted(span_data.items()):
        durations = [d["duration_ms"] for d in data]
        ok_count = sum(1 for d in data if d["status"] == "ok")
        error_count = len(data) - ok_count

        durations_sorted = sorted(durations)
        count = len(durations)

        p50 = durations_sorted[int(count * 0.5)] if count > 0 else 0
        p95 = durations_sorted[int(count * 0.95)] if count > 0 else 0
        p99 = durations_sorted[int(count * 0.99)] if count > 0 else 0
        mean = sum(durations) / count if count > 0 else 0
        min_dur = min(durations) if durations else 0
        max_dur = max(durations) if durations else 0

        print(f"Span: {span_name}")
        print(f"  Count:      {count:6d} ({ok_count} ok, {error_count} errors)")
        print(f"  Mean:       {mean:8.2f} ms")
        print(f"  p50:        {p50:8.2f} ms")
        print(f"  p95:        {p95:8.2f} ms")
        print(f"  p99:        {p99:8.2f} ms")
        print(f"  Min:        {min_dur:8.2f} ms")
        print(f"  Max:        {max_dur:8.2f} ms")

        # Show operation breakdown if available
        ops = defaultdict(int)
        for d in data:
            if d["operation"]:
                ops[d["operation"]] += 1
        if ops:
            print(f"  Operations: {dict(ops)}")

        print()


def analyze_counters(events: List[Dict[str, Any]]) -> None:
    """Analyze counter statistics."""
    counter_data = defaultdict(list)

    for event in events:
        if event.get("event_type") == "counter":
            counter_name = event.get("counter_name")
            value = event.get("value")
            if counter_name and value is not None:
                counter_data[counter_name].append(value)

    if not counter_data:
        print("No counter events found in metrics\n")
        return

    print("="*80)
    print("COUNTER ANALYSIS")
    print("="*80 + "\n")

    for counter_name, values in sorted(counter_data.items()):
        count = len(values)
        total = sum(values)
        mean = total / count if count > 0 else 0
        min_val = min(values) if values else 0
        max_val = max(values) if values else 0

        print(f"Counter: {counter_name}")
        print(f"  Count:      {count:6d}")
        print(f"  Total:      {total:8.2f}")
        print(f"  Mean:       {mean:8.2f}")
        print(f"  Min:        {min_val:8.2f}")
        print(f"  Max:        {max_val:8.2f}")
        print()


def analyze_span_counts(events: List[Dict[str, Any]]) -> None:
    """Analyze counts embedded in span events."""
    span_counts = defaultdict(lambda: defaultdict(list))

    for event in events:
        if event.get("event_type") == "span" and event.get("counts"):
            span_name = event.get("span")
            for count_name, value in event["counts"].items():
                span_counts[span_name][count_name].append(value)

    if not span_counts:
        print("No span count data found in metrics\n")
        return

    print("="*80)
    print("SPAN COUNTS ANALYSIS")
    print("="*80 + "\n")

    for span_name, counts in sorted(span_counts.items()):
        print(f"Span: {span_name}")
        for count_name, values in sorted(counts.items()):
            count = len(values)
            total = sum(values)
            mean = total / count if count > 0 else 0
            print(f"  {count_name:20s}: total={total:6.0f}, mean={mean:6.2f}, count={count}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Adjacent metrics JSONL output"
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Metrics JSONL file (default: stdin)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    events = load_metrics(args.file)

    if not events:
        print("No metrics events found", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(events)} events", file=sys.stderr)

    if args.json:
        # TODO: Implement JSON output format
        print("JSON output not yet implemented", file=sys.stderr)
        sys.exit(1)
    else:
        analyze_spans(events)
        analyze_counters(events)
        analyze_span_counts(events)


if __name__ == "__main__":
    main()
