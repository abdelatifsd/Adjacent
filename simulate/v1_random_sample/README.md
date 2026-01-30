# v1_random_sample — Cold-Start Graph Evolution Experiment

## What This Does

This script simulates realistic production query traffic to observe how Adjacent's lazy graph construction evolves over time. It samples a subset of products from the catalog and generates a weighted query schedule that mirrors real-world e-commerce behavior: some products are "hot" (frequently queried), most are "cold" (rarely queried).

The experiment tracks the key metric Adjacent optimizes for: **the shift from vector-sourced to graph-sourced recommendations** as the system learns semantic relationships through LLM inference.

## Why This Matters

Adjacent's core thesis is that lazy, query-anchored graph construction can reduce dependency on expensive behavioral data. This experiment validates:

1. **Graph evolution**: Do repeated queries to the same products trigger LLM inference and materialize edges?
2. **Vector → Graph transition**: As edges accumulate, do recommendations shift from vector similarity to graph traversal?
3. **Hotness matters**: Do "hot" products (queried frequently) show faster graph growth than "cold" products?
4. **Latency stability**: Does query latency remain stable as the graph grows?

## What Gets Optimized

- **Reduced vector reliance**: As graph edges materialize, fewer recommendations need expensive vector searches
- **Improved semantic quality**: Graph edges (COMPLEMENTS, SUBSTITUTE_FOR, etc.) capture relationships that pure vector similarity misses
- **LLM cost amortization**: Hot products amortize LLM inference costs across many queries
- **Query latency**: Graph traversal is faster than vector search at scale

## How It Works

1. **Sample products**: Randomly sample 10-15 products from the catalog
2. **Assign hotness**: Use a power-law distribution (Zipf-like, α=1.2 by default) to assign query frequencies
   - Top 20% of products get ~50% of queries (hot products)
   - Bottom 80% get the remaining ~50% (cold products)
3. **Generate schedule**: Create a weighted random query schedule with realistic spacing (1-5s delays)
4. **Execute queries**: Hit `/v1/perf/query/{product_id}` for each scheduled query
5. **Track metrics**: Log `from_graph`, `from_vector`, `latency_ms`, `inference_status` for every query
6. **Summarize**: Compute averages by query number (1st, 2nd, 3rd, etc.) and hot vs. cold products

## Usage

```bash
# Default: 12 products, 80 queries, α=1.2
python simulate/v1_random_sample/run.py

# More products, more queries, higher skew
python simulate/v1_random_sample/run.py --products 15 --total-queries 120 --alpha 1.5

# Faster simulation (shorter delays)
python simulate/v1_random_sample/run.py --min-delay 0.5 --max-delay 2.0

# Custom API endpoint
python simulate/v1_random_sample/run.py --api http://localhost:8000
```

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--api` | `http://localhost:8000` | API base URL |
| `--products` | 12 | Number of products to sample (10-15 recommended) |
| `--total-queries` | 80 | Total queries to execute |
| `--min-delay` | 1.0 | Min seconds between queries |
| `--max-delay` | 5.0 | Max seconds between queries |
| `--top-k` | 10 | Top-K recommendations per query |
| `--alpha` | 1.2 | Power-law exponent (1.0=mild, 1.5=moderate, 2.0=heavy skew) |

## Output

### Console Output

The script prints:
- Sampled products and query distribution
- Real-time query progress with `graph` vs. `vector` counts
- Summary statistics:
  - Overall averages (graph, vector, latency)
  - Progression by query number (1st query, 2nd query, etc.)
  - Hot vs. cold product comparison

Example:
```
=== Summary ===
Total queries: 80
Products sampled: 12
Overall avg: graph=3.2  vector=6.8  latency=45ms

By query number (showing progression from cold to warm):
  Query #1: avg graph=0.0  avg vector=10.0  count=12
  Query #2: avg graph=1.5  avg vector=8.5  count=11
  Query #3: avg graph=3.2  avg vector=6.8  count=9
  Query #4: avg graph=4.8  avg vector=5.2  count=8
  ...

Hot products (top 20%) vs. Cold products (bottom 80%):
  Hot:  avg graph=5.3  avg vector=4.7  queries=42
  Cold: avg graph=1.8  avg vector=8.2  queries=38
```

### JSONL Log

Results are written to `simulate/v1_random_sample/results/run_YYYYMMDD_HHMMSS.jsonl`.

Each line is a JSON object:
```json
{
  "ts": "2026-01-30T12:34:56.789Z",
  "product_id": "123",
  "query_num": 3,
  "from_graph": 4,
  "from_vector": 6,
  "inference_status": "enqueued",
  "latency_ms": 42,
  "job_id": "abc-123-def-456"
}
```

## Interpreting Results

### Success Indicators

✅ **Graph growth over time**: `from_graph` increases as `query_num` increases
✅ **Hot products warm faster**: Hot products show higher `from_graph` than cold products
✅ **Stable latency**: `latency_ms` stays < 100ms even as graph grows
✅ **Inference triggers**: `inference_status` is `"enqueued"` or `"complete"` (not `"skipped"`)

### Red Flags

❌ **No graph growth**: `from_graph` stays at 0 → LLM inference not working (check OPENAI_API_KEY, worker logs)
❌ **Latency spikes**: `latency_ms` > 500ms → Database bottleneck or inference blocking query path
❌ **Inference always skipped**: `inference_status` is always `"skipped"` → Worker not running or Redis misconfigured

## Metrics & Grafana Integration

### How It Works

The simulation uses the same `LokiHandler` and `commons.metrics` infrastructure as the API and Worker. It also writes to `logs/simulation.log`; **Promtail** tails that file and sends it to Loki with `job="simulation"` (using a pipeline that sets event time from the JSON `timestamp` field so points are spaced correctly). It emits the same span names (`query_total`) and counts (`from_graph`, `from_vector`) as production, differentiated only by the Loki `job` label:

- **Production**: `job="api"` / `job="worker"`
- **Simulation**: `job="simulation"`

This means the **existing "Adjacent Metrics" dashboard** works for both production and simulation data. A `$job` template variable at the top of the dashboard lets you switch between `api` (production) and `simulation`. No separate dashboard needed.

### Viewing Simulation Results

1. Open Grafana at `http://localhost:3000` (admin/admin)
2. Navigate to **Adjacent Metrics** dashboard
3. Use the **Job** dropdown at the top to select `simulation`
4. Adjust the time range to cover your simulation run

Worker panels (LLM Calls, Token Usage, Edge Lifecycle, etc.) show **all** worker activity regardless of job selection — they use `job="worker"` (not `$job`), so you see LLM metrics for both simulation-triggered and other API traffic when viewing simulation.

### Additional Metrics

Beyond the standard `query_total` spans, the simulation also emits summary counters:

| Event Type | Operation | Counter | Description |
|------------|-----------|---------|-------------|
| Counter | `simulation` | `num_products`, `total_queries`, `alpha` | Experiment configuration |
| Counter | `simulation_summary` | `avg_from_graph`, `avg_from_vector`, `avg_latency_ms` | Aggregate results |
| Counter | `simulation_summary` | `hot_avg_from_graph`, `cold_avg_from_graph` | Hot vs. cold comparison |
| Counter | `simulation_progression` | `query_num_N_avg_from_graph` | Per-query-number averages |

These can be queried in Grafana Explore with `{job="simulation"} | json | operation="simulation_summary"`.

## Integration with Monitoring

The experiment works seamlessly with Adjacent's monitoring stack:

1. **Grafana**: View metrics at `http://localhost:3000` — select `simulation` in the Job dropdown (no separate dashboard needed).
2. **Neo4j Browser**: Inspect graph state at `http://localhost:7475`
   ```cypher
   // See products queried in this experiment
   MATCH (p:Product) WHERE p.total_query_count > 0
   RETURN p.id, p.total_query_count ORDER BY p.total_query_count DESC LIMIT 20;

   // Count edges created
   MATCH ()-[e:RECOMMENDATION]->() RETURN count(e);

   // Inspect a hot product's edges
   MATCH (p:Product {id: "123"})-[e:RECOMMENDATION]->(related)
   RETURN related.id, e.edge_type, e.confidence, e.anchors_seen;
   ```

3. **RQ Dashboard**: Check inference job queue at `http://localhost:9181` (if rq-dashboard is installed)

## Next Steps

After running this experiment:

1. **Compare runs**: Change `--alpha` to see how query distribution affects graph growth
2. **Scale up**: Increase `--products` and `--total-queries` to stress-test the system
3. **Analyze logs**: Use the JSONL output for custom analysis (pandas, SQL, etc.)
4. **Inspect edges**: Use Neo4j Browser to validate edge quality and reinforcement logic
5. **Iterate**: Adjust the system (e.g., `endpoint_reinforcement_threshold`) and re-run

## Design Rationale

### Why Power-Law Distribution?

Real-world e-commerce traffic follows a power-law: a few products (bestsellers, trending items) get most of the traffic, while the long tail gets sparse queries. Using a Zipf-like distribution (α=1.2–1.5) mirrors this behavior and tests Adjacent's ability to:
- Quickly build dense graphs around hot products
- Still provide reasonable recommendations for cold products (via vector fallback)
- Amortize LLM costs where they matter most

### Why Random Delays?

Spacing queries with random delays (1-5s) simulates realistic production behavior:
- Prevents bursting the inference queue
- Allows async inference jobs to complete between queries
- Tests the system's ability to handle concurrent query patterns

### Why Interleaved Queries?

Unlike a "phase 1 + phase 2" approach, this experiment interleaves first-time and repeat queries. This is more realistic: in production, some users query product A for the first time while others are re-querying product B. This tests the system's ability to handle mixed cold/warm query patterns simultaneously.
