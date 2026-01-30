"""
v1_random_sample — Cold-start graph evolution experiment.

Randomly samples products from the catalog, then generates a weighted
query schedule to simulate production traffic patterns where some
products are "hot" (frequently queried) and others are "cold".

Query distribution follows a power-law (Zipf-like) to mirror realistic
e-commerce behavior. Queries are randomized and spaced to simulate
continuous production traffic.

Metrics are pushed directly to Loki (job="simulation") for Grafana visualization.

Usage:
    python simulate/v1_random_sample/run.py
    python simulate/v1_random_sample/run.py --products 15 --total-queries 100
    python simulate/v1_random_sample/run.py --api http://localhost:8000 --alpha 1.5
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Add src to path for commons imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from commons.loki_handler import LokiHandler
from commons.metrics import span, emit_counter, generate_trace_id

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "demo" / "kaggle_ecommerce.json"
DEFAULT_API = "http://localhost:8000"


def configure_simulation_logger(run_id: str, loki_enabled: bool = True) -> logging.Logger:
    """
    Configure logger for simulation metrics.

    Pushes metrics to Loki with job="simulation" and run_id label.
    Also writes to local file for archival.
    """
    logger = logging.getLogger("adjacent")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    # Loki handler for Grafana visualization
    if loki_enabled:
        try:
            loki_url = os.getenv("LOKI_URL", "http://localhost:3100/loki/api/v1/push")
            loki_handler = LokiHandler(url=loki_url, job="simulation", enabled=True)
            loki_handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(loki_handler)
        except Exception as e:
            print(f"Warning: Failed to configure Loki handler: {e}", file=sys.stderr)

    # Simulation log file — Promtail tails this into Loki with job="simulation"
    shared_log_dir = Path(__file__).resolve().parents[2] / "logs"
    shared_log_dir.mkdir(exist_ok=True)
    shared_log_path = shared_log_dir / "simulation.log"

    shared_handler = logging.FileHandler(shared_log_path, mode="a")
    shared_handler.setFormatter(logging.Formatter("%(message)s"))
    shared_handler.setLevel(logging.INFO)
    logger.addHandler(shared_handler)

    # Archival copy per run
    log_dir = Path(__file__).parent / "results"
    log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"run_{ts}_{run_id[:8]}.jsonl"

    file_handler = logging.FileHandler(log_path, mode="w")
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)

    return logger


def load_product_ids(path: Path) -> list[str]:
    with open(path) as f:
        products = json.load(f)
    return [p["id"] for p in products]


def query_product(client: httpx.Client, base: str, product_id: str, top_k: int = 10) -> dict:
    url = f"{base}/v1/perf/query/{product_id}"
    resp = client.get(url, params={"top_k": top_k}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def generate_weighted_schedule(
    product_ids: list[str], total_queries: int, alpha: float = 1.2
) -> list[str]:
    """
    Generate a weighted query schedule following a power-law distribution.

    Args:
        product_ids: List of product IDs to sample from
        total_queries: Total number of queries to generate
        alpha: Power-law exponent (higher = more skewed toward hot products)
               - 1.0: mild skew
               - 1.5: moderate skew (realistic for e-commerce)
               - 2.0: heavy skew

    Returns:
        List of product IDs representing the query schedule
    """
    # Shuffle first so hotness assignment is random, not based on input order
    shuffled = product_ids.copy()
    random.shuffle(shuffled)

    n = len(shuffled)
    # Generate Zipf-like weights: weight[i] ~ 1 / (i+1)^alpha
    weights = [1.0 / ((i + 1) ** alpha) for i in range(n)]
    total_weight = sum(weights)
    normalized_weights = [w / total_weight for w in weights]

    # Generate schedule using weighted random sampling with replacement
    schedule = random.choices(shuffled, weights=normalized_weights, k=total_queries)
    return schedule


def run_experiment(
    api_base: str,
    num_products: int,
    total_queries: int,
    min_delay: float,
    max_delay: float,
    top_k: int,
    alpha: float,
    loki_enabled: bool = True,
):
    # Generate unique run ID for this experiment
    run_id = generate_trace_id()

    # Configure logger for metrics
    logger = configure_simulation_logger(run_id, loki_enabled)

    all_ids = load_product_ids(DATA_PATH)
    sampled = random.sample(all_ids, min(num_products, len(all_ids)))

    # Generate weighted query schedule
    schedule = generate_weighted_schedule(sampled, total_queries, alpha)
    query_distribution = Counter(schedule)

    print(f"=== v1_random_sample experiment ===")
    print(f"Run ID: {run_id}")
    print(f"Sampled {len(sampled)} products")
    print(f"Total queries: {total_queries}")
    print(f"Delay range: {min_delay}-{max_delay}s")
    print(f"Alpha (skew): {alpha}")
    print()
    print("Query distribution (top 10 products):")
    for pid, count in query_distribution.most_common(10):
        print(f"  {pid}: {count} queries")
    print()

    # Emit experiment config as counters
    emit_counter("num_products", num_products, operation="simulation", trace_id=run_id, logger=logger, run_id=run_id)
    emit_counter("total_queries", total_queries, operation="simulation", trace_id=run_id, logger=logger, run_id=run_id)
    emit_counter("alpha", alpha, operation="simulation", trace_id=run_id, logger=logger, run_id=run_id)
    emit_counter("min_delay", min_delay, operation="simulation", trace_id=run_id, logger=logger, run_id=run_id)
    emit_counter("max_delay", max_delay, operation="simulation", trace_id=run_id, logger=logger, run_id=run_id)

    log: list[dict] = []
    product_query_counts = {pid: 0 for pid in sampled}

    with httpx.Client() as client:
        for i, pid in enumerate(schedule, 1):
            product_query_counts[pid] += 1
            query_num = product_query_counts[pid]

            # Emit span for each query (mirrors API metrics)
            with span(
                "query_total",
                operation="query",
                trace_id=run_id,
                logger=logger,
                run_id=run_id,
                product_id=pid,
                query_num=query_num,
            ) as ctx:
                result = query_product(client, api_base, pid, top_k)

                from_graph = result.get("from_graph", 0)
                from_vector = result.get("from_vector", 0)
                latency_ms = result.get("request_total_ms")
                inference_status = result.get("inference_status")

                # Set counts on span context
                ctx.set_count("from_graph", from_graph)
                ctx.set_count("from_vector", from_vector)
                ctx.set_attr("inference_status", inference_status)
                if latency_ms:
                    ctx.set_attr("api_latency_ms", latency_ms)

            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id,
                "product_id": pid,
                "query_num": query_num,
                "from_graph": from_graph,
                "from_vector": from_vector,
                "inference_status": inference_status,
                "latency_ms": latency_ms,
                "job_id": result.get("job_id"),
            }
            log.append(entry)

            latency = latency_ms or "?"
            print(
                f"  [{i}/{total_queries}] product={pid} query#{query_num} "
                f"graph={from_graph} vector={from_vector} latency={latency}ms "
                f"inference={inference_status}"
            )

            if i < total_queries:
                delay = random.uniform(min_delay, max_delay)
                time.sleep(delay)

    # --- Summary ---
    def avg(entries, key):
        vals = [e[key] for e in entries if e[key] is not None]
        return sum(vals) / len(vals) if vals else 0

    # Group by query number (1st query, 2nd query, etc.)
    by_query_num = {}
    for entry in log:
        qn = entry["query_num"]
        if qn not in by_query_num:
            by_query_num[qn] = []
        by_query_num[qn].append(entry)

    # Hot vs cold products (top 20% vs bottom 80%)
    top_20_percent_count = max(1, len(sampled) // 5)
    hot_products = set([pid for pid, _ in query_distribution.most_common(top_20_percent_count)])
    hot_entries = [e for e in log if e["product_id"] in hot_products]
    cold_entries = [e for e in log if e["product_id"] not in hot_products]

    # Emit summary metrics
    avg_graph = avg(log, 'from_graph')
    avg_vector = avg(log, 'from_vector')
    avg_latency = avg(log, 'latency_ms')

    emit_counter("avg_from_graph", avg_graph, operation="simulation_summary", trace_id=run_id, logger=logger, run_id=run_id)
    emit_counter("avg_from_vector", avg_vector, operation="simulation_summary", trace_id=run_id, logger=logger, run_id=run_id)
    emit_counter("avg_latency_ms", avg_latency, operation="simulation_summary", trace_id=run_id, logger=logger, run_id=run_id)

    # Emit hot vs cold metrics
    emit_counter("hot_avg_from_graph", avg(hot_entries, 'from_graph'), operation="simulation_summary", trace_id=run_id, logger=logger, run_id=run_id, segment="hot")
    emit_counter("hot_avg_from_vector", avg(hot_entries, 'from_vector'), operation="simulation_summary", trace_id=run_id, logger=logger, run_id=run_id, segment="hot")
    emit_counter("cold_avg_from_graph", avg(cold_entries, 'from_graph'), operation="simulation_summary", trace_id=run_id, logger=logger, run_id=run_id, segment="cold")
    emit_counter("cold_avg_from_vector", avg(cold_entries, 'from_vector'), operation="simulation_summary", trace_id=run_id, logger=logger, run_id=run_id, segment="cold")

    print("\n=== Summary ===")
    print(f"Run ID: {run_id}")
    print(f"Total queries: {len(log)}")
    print(f"Products sampled: {len(sampled)}")
    print(f"Overall avg: graph={avg_graph:.1f}  vector={avg_vector:.1f}  latency={avg_latency:.0f}ms")
    print()
    print("By query number (showing progression from cold to warm):")
    for qn in sorted(by_query_num.keys())[:10]:  # Show first 10 rounds
        entries = by_query_num[qn]
        qn_avg_graph = avg(entries, 'from_graph')
        qn_avg_vector = avg(entries, 'from_vector')
        print(f"  Query #{qn}: avg graph={qn_avg_graph:.1f}  avg vector={qn_avg_vector:.1f}  count={len(entries)}")

        # Emit per-query-number metrics
        emit_counter(f"query_num_{qn}_avg_from_graph", qn_avg_graph, operation="simulation_progression", trace_id=run_id, logger=logger, run_id=run_id, query_num=qn)
        emit_counter(f"query_num_{qn}_avg_from_vector", qn_avg_vector, operation="simulation_progression", trace_id=run_id, logger=logger, run_id=run_id, query_num=qn)

    print()
    print("Hot products (top 20%) vs. Cold products (bottom 80%):")
    print(f"  Hot:  avg graph={avg(hot_entries, 'from_graph'):.1f}  avg vector={avg(hot_entries, 'from_vector'):.1f}  queries={len(hot_entries)}")
    print(f"  Cold: avg graph={avg(cold_entries, 'from_graph'):.1f}  avg vector={avg(cold_entries, 'from_vector'):.1f}  queries={len(cold_entries)}")

    # Flush logger to ensure all metrics are pushed
    for handler in logger.handlers:
        handler.flush()

    print(f"\nMetrics pushed to Loki (run_id={run_id})")
    print(f"View in Grafana: http://localhost:3000 (filter by job=simulation, run_id={run_id})")


def main():
    parser = argparse.ArgumentParser(description="v1_random_sample experiment")
    parser.add_argument("--api", default=DEFAULT_API, help="API base URL")
    parser.add_argument("--products", type=int, default=12, help="Number of products to sample (10-15)")
    parser.add_argument("--total-queries", type=int, default=10, help="Total number of queries to execute")
    parser.add_argument("--min-delay", type=float, default=2.0, help="Min delay between queries (seconds)")
    parser.add_argument("--max-delay", type=float, default=5.0, help="Max delay between queries (seconds)")
    parser.add_argument("--top-k", type=int, default=10, help="Top-K recommendations per query")
    parser.add_argument("--alpha", type=float, default=1.2, help="Power-law exponent (1.0-2.0, higher = more skewed)")
    parser.add_argument("--no-loki", action="store_true", help="Disable Loki metrics push (file-only logging)")
    args = parser.parse_args()

    run_experiment(
        api_base=args.api,
        num_products=args.products,
        total_queries=args.total_queries,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        top_k=args.top_k,
        alpha=args.alpha,
        loki_enabled=not args.no_loki,
    )


if __name__ == "__main__":
    main()
