<div align="center">

# Adjacent

<img src="assets/brand/adjacent-logo.png" alt="Adjacent — open-source cold-start recommendation and knowledge graph framework" width="420">

</div>

<br />

Adjacent is an open-source framework for cold-start recommendation and graph discovery. It enables teams to build recommendation structure from a catalog alone, while observing how semantic graphs emerge and mature through real usage.

---

## Demo Video

**[▶ Watch the full demo on Loom](https://www.loom.com/share/018c20b00b84470da28c89616f870a76)**

In this demo, you'll see:

- Live recommendation queries via the API
- How repeated queries trigger lazy graph construction
- The resulting structure in Neo4j as edges accumulate
- How latency and inference behavior change over time in Grafana

The demo is intentionally light on implementation details and focuses on illustrating the system's behavior and dynamics. Setup and architecture are covered elsewhere in the documentation.

---

## Quick Start

Get Adjacent running in 5 minutes:

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (with Docker Compose)
- [uv](https://docs.astral.sh/uv/) - Fast Python package manager
- Python 3.11+
- OpenAI API key (for LLM inference)

### Installation

```bash
# Clone the repository
git clone https://github.com/abdelatifsd/adjacent.git
cd adjacent

# Run setup script (installs dependencies, creates .env)
./scripts/setup.sh

# Add your OpenAI API key to .env
echo "OPENAI_API_KEY=sk-your-key-here" >> .env

# Start everything
make dev
```

That's it! The system will:
1. Start infrastructure (Neo4j, Redis, Grafana, Loki)
2. Ingest demo e-commerce data
3. Embed products using HuggingFace
4. Start API server and worker

### Access Points

- **API Documentation:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Grafana Dashboard:** [http://localhost:3000](http://localhost:3000) (admin/admin)
- **Neo4j Browser:** [http://localhost:7475](http://localhost:7475) (neo4j/adjacent123)

### Quick Commands

```bash
make dev           # Start everything
make dev-logs      # View logs
make dev-status    # Check service health
make dev-down      # Stop all services
make dev-clean     # Clean everything (removes volumes from docker)
```

**Run the simulation** (traffic experiment; see [simulate/v1_random_sample/README.md](simulate/v1_random_sample/README.md)):

```bash
python simulate/v1_random_sample/run.py
```

In Grafana, select **Job → simulation** to view simulation metrics.

### First Query

```bash
# Check system status
curl http://localhost:8000/v1/system/status | jq

# Get recommendations for a product
curl http://localhost:8000/v1/query/<product_id>?top_k=10 | jq
```

### Development Workflow

The `make dev` command runs everything in Docker with hot reload enabled. Changes to Python files in `src/` are automatically picked up.

**For advanced development** (native Python with separate terminals):

```bash
# Terminal 1: Start infrastructure
make reset-full

# Terminal 2: Start API
make api-start

# Terminal 3: Start worker
make worker
```

This gives you more control and better debugging visibility, but requires managing multiple processes.

**Testing different embedding providers:**

```bash
# Use HuggingFace (default, runs locally)
make embed

# Use OpenAI embeddings (requires OPENAI_API_KEY in .env)
make embed-openai
```

---

## Why Adjacent Exists

Most recommendation systems assume you already have:

- User clicks
- Purchases
- Sessions
- Co-occurrence logs

But many catalogs start with none of that.

Adjacent explores a different question:

> Can we build useful recommendation structure starting only from a catalog and make it cheaper, faster, and more reliable as the system is used?

### The Core Idea

Instead of committing to heavy offline pipelines or permanent LLM inference, Adjacent is designed to transition from vector-heavy to graph-native recommendations.

- **Embeddings** provide the initial signal
- **LLMs** infer relationships asynchronously, only where needed
- A **knowledge graph** is built lazily, anchored to real queries

**LLM inference is not part of the serving path.** It is used only to construct and reinforce structure.

Once a product's local graph becomes sufficiently dense, queries for that product rely purely on graph-based retrieval. Meaning, no LLM inference, lower latency, and lower cost.

**As the graph matures:**
- LLM calls drop
- Latency improves
- Cost amortizes naturally

What begins as a cold-start solution gradually becomes a reusable semantic asset: one that supports recommendations first, then broader reasoning and analysis as the graph matures.

### Design Rationale

Adjacent is designed around the hypothesis that:
1. Graph queries become faster and cheaper than repeated embedding+LLM inference
2. Real query demand naturally builds useful structure
3. Edge reuse across products amortizes inference cost

Initial testing on product catalogs confirms these patterns, but large-scale validation remains open research.

---

## See It In Action

Once the system is running (`make dev`), here's how to explore Adjacent's behavior:

### 1. View Your Product Nodes in Neo4j

Open the Neo4j Browser at [http://localhost:7475](http://localhost:7475) and run:

```cypher
MATCH (p:Product) RETURN p
```

You'll see your product catalog as disconnected nodes - no relationships yet.

<img src="assets/examples/nodes_display_neo4j.png" alt="Product nodes in Neo4j" width="800">

### 2. Query the API

Open the FastAPI docs at [http://localhost:8000/docs](http://localhost:8000/docs). Navigate to the `/v1/query/{product_id}` endpoint:

1. Pick a product ID from your catalog
2. Set `top_k` (e.g., 10 recommendations)
3. Click **Execute**

<img src="assets/examples/fastapi_endpoint_display.png" alt="FastAPI endpoint" width="800">

The API returns recommendations immediately using embeddings. Meanwhile, a background worker runs LLM inference to discover and materialize edges.

### 3. Watch the Graph Evolve

Go back to Neo4j and rerun the same Cypher query. After a few API calls, you'll see edges forming between products:

<img src="assets/examples/formed_graph_in_neo4j_zoomed_out.png" alt="Graph forming - zoomed out" width="800">

Zoom in to inspect the relationship types and structure:

<img src="assets/examples/formed_graph_neo4j_zoomed_in.png" alt="Graph forming - zoomed in" width="800">

Each edge represents an LLM-inferred relationship (`SUBSTITUTE_FOR`, `PAIRS_WITH`) that will be reinforced as more queries pass through.

### 4. Monitor System Behavior in Grafana

Open Grafana at [http://localhost:3000](http://localhost:3000) (admin/admin) to observe how the system evolves:

**Graph Evolution** - Watch retrieval transition from vector-to-graph based:

<img src="assets/examples/grafana_graph_evolution_viz.png" alt="Graph evolution metrics" width="800">

**Query & LLM Latency** - See how latency decreases over time as the graph matures and fewer LLM calls are needed:

<img src="assets/examples/grafana_query_and_llm_latency_viz.png" alt="Query and LLM latency" width="800">

**Token Economics** - Track LLM token usage and observe how it amortizes as the graph becomes self-sufficient:

<img src="assets/examples/grafana_token_usage_over_time.png" alt="Token usage over time" width="800">

---

## Key Design Principles

### 1. Lazy, Not Exhaustive

- No full pairwise graph construction
- No offline O(N²) jobs
- Edges are inferred only when a product is queried

This keeps the system:
- Cheap to start
- Fast to prototype
- Aligned with real usage

### 2. Anchors Drive Truth

- Every graph inference is tied to a **query anchor**
- An anchor is the product that triggered inference
- Edges are reinforced only when they appear under **distinct anchors**

This reduces reliance on single-shot LLM inference

### 3. Deterministic Where Possible, LLM Only Where Necessary

| Component | Approach |
|-----------|----------|
| Product normalization | Deterministic |
| Canonical edge IDs | Deterministic |
| Confidence scoring | Deterministic |
| Relationship inference | LLM |

LLMs are used only to infer relationships that cannot be derived mechanically.

### 4. Schema-First, Flat, Auditable

- Products, edges, and patches are all validated against JSON Schemas
- Edge schemas are flat by design (no nested objects)
- Everything written to Neo4j can be logged as JSONL for traceability

---

## What Adjacent Is (and Isn't)

| It is | It is not |
|-------|-----------|
| A cold-start recommendation engine | A production-ready recommender |
| A graph-first design exploration | A replacement for collaborative filtering |
| A framework for experimenting with LLM-assisted structure discovery | A UI product |

---

## System Architecture

### 1. Product Ingest & Normalization

- **Input:** user-provided JSON catalog
- **Enforced schema:** [`schemas/product.json`](schemas/product.json)
- **Normalized fields:** title, description, category, tags, etc.
- **Storage:** `(:Product)` nodes in Neo4j

### 2. Embedding Layer

Pluggable embedding providers:
- OpenAI
- HuggingFace (local)
- *(extensible to others)*

Products are embedded in batches. Vectors are stored directly on Product nodes, and a Neo4j vector index is used for similarity search.

### 3. Query Flow (Core Loop)

When a product `X` is queried:

```
1. Fetch existing graph neighbors of X (Neo4j edges)
2. If needed, retrieve vector-similar candidates to fill the response to top-K
3. Filter vector candidates: exclude any already connected to X
4. Enqueue an async inference job: infer_edges(anchor=X, candidate_ids=[...])
5. Return recommendations immediately (graph + vector mix)
6. Worker runs later:
   - Calls LLM with (anchor=X, candidates=[...]) and receives edge patches for:
     - Anchor↔candidate edges (X↔B, X↔C, etc.)
     - Candidate↔candidate edges (B↔C, B↔D, etc.) among the provided candidates
   - Materializes + upserts edges into Neo4j (reinforcement via anchors_seen)
```

This loop repeats, gradually enriching the graph with both direct and transitive relationships.

---

## Edge Model

All recommendation edges are:

- **Symmetric**
- **Canonicalized** (`from_id <= to_id`)
- **Typed**
- **Confidence-scored**
- **Anchor-reinforced**

### Edge Types (v1)

Adjacent uses two orthogonal relationship primitives — chosen to span the full space of meaningful product relationships while minimizing ambiguous overlap. The goal is that the LLM can apply a single clean decision test for every product pair, rather than adjudicating vague boundaries between overlapping gradations.

| Type | Description | Decision test |
|------|-------------|---------------|
| `SUBSTITUTE_FOR` | Products that serve the same need and are interchangeable | Would a user want this *instead of* the anchor? |
| `PAIRS_WITH` | Products that work together, enhance each other, or commonly co-appear | Would a user want this *alongside* the anchor? |

> **Note:** No behavioral semantics are assumed. These are world-knowledge relationships, not user-interaction claims.

### Edge Type Uniqueness

**Design decision:** Multiple edge types can exist between the same product pair.

The `edge_id` is computed as `hash(edge_type + from_id + to_id)`, meaning:
- `B↔C (SUBSTITUTE_FOR)` and `B↔C (PAIRS_WITH)` are **separate edges**
- Each has its own `anchors_seen` and confidence score
- Both can coexist in the graph (a product pair can be substitutable in one context and commonly paired in another)

**Implication:** When querying recommendations, you may see the same product pair with different edge types. The one with higher confidence (more anchors) is typically more reliable.

---

## Anchors, Confidence & Reinforcement

An edge becomes trustworthy not because the LLM said so once, but because **it keeps reappearing under different anchors**. An anchor is the product that triggered a query — every graph inference is tied to one. Edges are reinforced exclusively by **third-party anchors**: products other than the endpoints of the edge.

### Reinforcement Rules

When the LLM infers an edge, the system checks if that edge already exists:

| Scenario | Action |
|----------|--------|
| Edge is new | Create with `anchors_seen=[current_anchor]`, confidence=0.55 |
| Edge exists, anchor is new | Append anchor to `anchors_seen`, recalculate confidence |
| Edge exists, anchor already seen | No change (same anchor can't reinforce twice) |

**Example lifecycle:**

```
Query anchor A → candidates [B, C, D]
  └── LLM infers B↔C (PAIRS_WITH)
  └── Edge created: B↔C, anchors_seen=[A], confidence=0.55, status=PROPOSED

Query anchor E → candidates [B, C, F]
  └── LLM re-infers B↔C (PAIRS_WITH)
  └── Edge exists! anchors_seen=[A, E], confidence=0.63, status=PROPOSED

Query anchor G → candidates [B, C, X]
  └── LLM re-infers B↔C (PAIRS_WITH)
  └── Edge exists! anchors_seen=[A, E, G], confidence=0.70, status=ACTIVE
```

B and C themselves cannot reinforce their own edge — once B↔C exists, both endpoints detect the connection via an undirected graph lookup and are filtered out before inference runs.

### Filtering Logic

Anchor↔candidate and candidate↔candidate edges are filtered differently:

**Anchor↔Candidate:** Already-connected vector candidates are filtered out entirely before the LLM call.

```
Query B → C appears as vector candidate
  ↓
Check: Does B-C exist?
  ├─ No  → Include C → LLM(B, [C, ...]) → Create B-C
  └─ Yes → Filter C → No LLM call for B-C
```

**Candidate↔Candidate:** We **do NOT filter** these before the LLM call. Re-inference from different anchors IS the reinforcement mechanism — the current anchor is recorded in `anchors_seen` regardless. This is how the example above works: B↔C is strengthened because independent anchor queries (A, E, G) each led to its discovery.

### Confidence Scoring

**Confidence grows via a capped exponential heuristic:**
- Base confidence: 0.55 (single anchor)
- Growth rate: 0.15 per additional anchor
- Hard cap: 0.95 (no false certainty)
- ACTIVE threshold: 0.70 (typically ~3 distinct anchors)

Confidence serves a dual purpose: it gates edge status (PROPOSED → ACTIVE) and acts as a ranking signal. High-confidence edges are ranked above newer, less-validated ones — no separate scoring model required. This gives two ranking dimensions at query time without additional computation: **relevance** (edge type and graph structure) and **reliability** (confidence score).

### Known Tension: Candidate-Candidate Edges Constrain Future Exploration

When B is a candidate during A's query, any B↔C edge the LLM infers is incidental — A was the focus, not B. When B is later queried as an anchor, C is already a graph neighbor. C occupies a graph slot and is excluded from inference, so B's own anchor query explores a space already partially shaped by inferences made in someone else's context.

The compounding effect: by the time B is queried directly, it may already have a dense neighborhood built from incidental candidate-candidate inferences across many prior anchor queries. Its own anchor query — the one where it is the explicit subject — contributes the least new information.

**Future direction:** Edge provenance is already tracked via `created_kind` (`anchor_candidate` vs `candidate_candidate`). A future adjustment could use this to give anchor-inferred edges priority in neighbor retrieval, and exclude candidate-inferred edges below a confidence threshold from the graph slot count — preserving more exploration budget for when a product is queried as an anchor directly.

---

## Storage Strategy

**Neo4j** is the primary store:
- Products
- Recommendation edges
- Vector index

**Optional JSONL logs** for:
- Edge patches
- Materialized edges
- Debugging and replay

> No separate vector DB is required.

---

## Assumptions

Adjacent explicitly assumes:

1. You have **no behavioral data**
2. Product descriptions are **semantically meaningful**
3. LLMs can infer reasonable **world-knowledge relationships**
4. Global graph coherence is less important than **local correctness**
5. Reinforcement over time matters more than **single-shot accuracy**

---

## Known Limitations & Future Considerations

The following are acknowledged limitations in v1, documented here to inform future iteration:

### 1. LLM Usage Is Front-Loaded

LLM inference in Adjacent is naturally amortized. As graph neighborhoods mature, vector candidates are filtered and inference is skipped. Early runs may incur higher LLM usage, but mature regions rely primarily on graph traversal.

Future work may explore explicit saturation thresholds, caching, or batch inference.

### 2. No Edge Decay or Dispute Mechanism

The graph is monotonic in v1. Edges are never removed or demoted. This is an intentional simplification prioritizing auditability and clarity.

Future iterations may introduce decay, review, or type migration mechanisms.

### 3. Single Embedding Field

Currently `embed_text` is derived only from description.

**For richer retrieval:**
- Concatenate title + category + tags into `embed_text`
- The `EmbeddingConfig` is designed for this  -  extend `FIELDS` tuple and bump `VERSION`

### 4. Local Correctness Over Global Optimality

Adjacent favors local correctness around active anchors rather than enforcing global graph coherence. This tradeoff simplifies reasoning but may allow early inaccuracies to persist.

### 5. No Formal Evaluation Framework

Adjacent does not include a built-in evaluation framework for validating inferred edges against ground truth.

Edges are inferred by an LLM using world knowledge and reinforced over time via distinct anchor contexts. While this reinforcement mechanism reduces the impact of single-shot errors, it does not guarantee correctness. Incorrect or weakly grounded edges may persist, particularly in early stages or in low-diversity query regimes.

This reflects a broader challenge in cold-start recommendation: when no behavioral data exists, there is no obvious objective signal to evaluate against.

**That said, Adjacent is designed to be inspectable and auditable by construction:**

- Edge provenance is tracked
- Confidence grows only via anchor diversity
- Early edges are capped and require repeated reinforcement

**Future directions may include:**

- Meta-evaluation agents that review edges for consistency or plausibility
- Human-in-the-loop review workflows
- Dataset-specific validation heuristics

Adjacent prioritizes useful structure over perfect certainty, under the assumption that a weak but improving semantic graph is often more valuable than no structure at all.

---

## Extensions and Research Directions

Adjacent is minimal in v1. However, the architecture enables several natural extensions beyond cold-start recommendation.

### 1. Multimodal Catalog Understanding

While v1 uses text-only embeddings, the same pipeline can support multimodal representations: image embeddings from product photos, text-image fusion, or video and audio for media catalogs. These embeddings can seed vector retrieval, inform LLM inference, and propagate into the graph structure. This allows Adjacent to operate on catalogs where semantics are visual or multimodal by nature, such as fashion, furniture, or art.

### 2. Graph as a Queryable Medium

The constructed graph is not just a recommendation artifact; it is a structured semantic medium. With an MCP-style interface (Model Context Protocol for LLM tool use), an agent could query the graph directly, reason over neighborhoods and edge types. In this framing, Adjacent becomes a semantic memory layer rather than just a recommender backend.

### 3. Knowledge Graph Construction for Representation Learning

As the graph grows, it encodes higher-order structure that can be reused. Node embeddings learned from the graph (via Node2Vec, GNNs, or similar methods) and edge-type-aware representations can enrich product embeddings with relational context. These representations could support clustering, classification, downstream ML tasks, or bootstrapping supervised models once labels appear. In this sense, Adjacent can act as a pre-training signal generator for later ML pipelines.

---

## Roadmap

The following features and improvements are planned for future versions:

- [x] **Grafana Visualizations** - Add more dashboards and panels in Grafana using the metrics provided (query latency, inference counts, graph vs vector mix, etc.)
- [x] **MCP Integration** - Implement Model Context Protocol server to enable LLM agents to query and reason over the knowledge graph directly
- [ ] **Custom Edge Construction Service** - Deploy a GPU-based LLM inference service using open-source Llama models (e.g., 3B parameter) on AWS to enable custom edge construction as an alternative to OpenAI, reducing cost and enabling self-hosted inference
- [ ] **GNN-Based Knowledge Graph Analysis** - Investigate the use of constructed knowledge graphs in downstream tasks, including graph neural networks and other techniques for information distillation, representation enrichment, and catalog-level learning
- [ ] **Evaluation Framework** - Build a validation system for assessing edge quality, including meta-evaluation agents and dataset-specific validation heuristics
- [ ] **Rate Limiting & Authentication** - Add production-grade API hardening with rate limiting, authentication, and request throttling
- [ ] **Negative Edge Tracking** - Track candidate pairs that the LLM consistently doesn't connect, preventing redundant inference retries (see [docs/graph_convergence.md](docs/graph_convergence.md))

Contributions are welcome. See issues for active discussions.

---

## License

Adjacent is released under the [MIT License](LICENSE).

Copyright (c) 2026 Adjacent Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
