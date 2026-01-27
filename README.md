<div align="center">

<img src="assets/brand/adjacent-logo.png" alt="Adjacent Logo" width="420">

</div>

<br />

Adjacent is an open-source experimentation framework for building recommendation systems without behavioral data. It transforms a raw product catalog into a semantic, graph-based recommendation engine using embeddings, LLM inference, and a lazily constructed knowledge graph.

Intended for: ML engineers and researchers exploring cold-start recommendation, semantic retrieval, and graph-augmented inference.

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
git clone <repo-url>
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
make dev-clean     # Clean everything (removes volumes)
```

### First Query

```bash
# Check system status
curl http://localhost:8000/v1/system/status | jq

# Get recommendations for a product
curl http://localhost:8000/v1/query/<product_id>?top_k=10 | jq
```

See [docs/system_dynamics.md](docs/system_dynamics.md) for understanding cold start behavior and system evolution.

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

But many catalogs start with **none of that**.

Adjacent answers a different question:

> *Given only a product catalog, can we infer useful recommendation structure — and improve it over time — without precomputing everything upfront?*

---

## Core Idea

Adjacent builds recommendations in three layers:

1. **Semantic similarity** (embeddings)
2. **LLM-inferred relationships** (typed edges)
3. **A lazily constructed graph** that grows only when queried

Instead of building a massive graph ahead of time, the graph is constructed on demand, anchored to actual queries.

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

This avoids hallucinated certainty from a single LLM call.

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
3. Decide which *vector* candidates should be sent for async inference (endpoint reinforcement gating):
   - Not connected → Include
   - Connected but still “early” → Include (endpoint reinforcement)
   - Connected and “mature” → Filter out
4. Enqueue an async inference job: infer_edges(anchor=X, candidate_ids=[...])
5. Return recommendations immediately (graph + vector mix)
6. Worker runs later:
   - Calls LLM with (anchor=X, candidates=[...]) and receives edge patches for:
     - Anchor↔candidate edges (X↔B, X↔C, etc.)
     - Candidate↔candidate edges (B↔C, B↔D, etc.) among the provided candidates
   - Materializes + upserts edges into Neo4j (reinforcement via anchors_seen)
```

This loop repeats, gradually enriching the graph with both direct and transitive relationships.

**Key Decision Point (Step 3):**
- With endpoint reinforcement enabled: already-connected *vector* candidates can be re-sent for inference, but only up to a threshold (default: 2 anchors, confidence < 0.70)
- After threshold: Only third-party anchors can reinforce the edge
- This balances fast reinforcement for popular products with efficiency

---

## Documentation & Guides

### Getting Started

- **[System Dynamics & Cold Start Guide](docs/system_dynamics.md)** - Essential reading for first-time users
  - Understand cold start behavior and what to expect
  - Learn how the system evolves from vector-only to graph+vector
  - First run checklist and validation steps
  - Use the `/v1/system/status` endpoint to monitor system health

- **[API Reference](docs/api.md)** - Complete HTTP API documentation
  - Quick start instructions
  - Environment setup
  - Endpoint reference with examples
  - Running the server and worker

### Deep Dives

- **[Async Architecture](docs/async_architecture.md)** - Detailed explanation of the inference system
  - How QueryService and Worker interact
  - Job queueing and status tracking
  - Performance characteristics

- **[Metrics Guide](docs/metrics.md)** - Performance instrumentation details

### Key Endpoints

```bash
# Check system health and dynamics
curl http://localhost:8000/v1/system/status

# Get recommendations
curl http://localhost:8000/v1/query/product_123?top_k=10

# Check job status
curl http://localhost:8000/v1/jobs/abc-123
```

---

## Edge Model

All recommendation edges are:

- **Symmetric**
- **Canonicalized** (`from_id <= to_id`)
- **Typed**
- **Confidence-scored**
- **Anchor-reinforced**

### Edge Types (v1)

| Type | Description |
|------|-------------|
| `SIMILAR_TO` | Products with similar attributes/purpose |
| `COMPLEMENTS` | Products that work well together |
| `SUBSTITUTE_FOR` | Products that can replace each other |
| `OFTEN_USED_WITH` | Products commonly used in conjunction |

> **Note:** No behavioral semantics are assumed. These are world-knowledge relationships, not user-interaction claims.

### Edge Type Uniqueness

**Important design decision:** Multiple edge types can exist between the same product pair.

The `edge_id` is computed as `hash(edge_type + from_id + to_id)`, meaning:
- `B↔C (COMPLEMENTS)` and `B↔C (SUBSTITUTE_FOR)` are **separate edges**
- Each has its own `anchors_seen` and confidence score
- Both can coexist in the graph

**Why allow this?**
- A product pair may genuinely have multiple relationship types
- Example: A keyboard COMPLEMENTS a mouse AND is OFTEN_USED_WITH a mouse
- The LLM prompt instructs "choose the single best edge_type" per call, but different anchor contexts may yield different judgments

**Implication:** When querying recommendations, you may see the same product pair with different edge types. The one with higher confidence (more anchors) is typically more reliable.

---

## Anchors & Confidence

An edge becomes trustworthy not because the LLM said so once, but because **it keeps reappearing under different anchors**.

### How Reinforcement Works

When the LLM infers an edge, the system checks if that edge already exists:

| Scenario | Action |
|----------|--------|
| Edge is new | Create with `anchors_seen=[current_anchor]`, confidence=0.55 |
| Edge exists, anchor is new | Append anchor to `anchors_seen`, recalculate confidence |
| Edge exists, anchor already seen | No change (same anchor can't reinforce twice) |

**Example: Candidate↔Candidate Reinforcement**

```
Query anchor A → candidates [B, C, D]
  └── LLM infers B↔C (COMPLEMENTS)
  └── Edge created: B↔C, anchors_seen=[A], confidence=0.55, status=PROPOSED

Query anchor E → candidates [B, C, F]
  └── LLM re-infers B↔C (COMPLEMENTS)
  └── Edge exists! anchors_seen=[A, E], confidence=0.63, status=PROPOSED

Query anchor G → candidates [B, C, X]
  └── LLM re-infers B↔C (COMPLEMENTS)
  └── Edge exists! anchors_seen=[A, E, G], confidence=0.70, status=ACTIVE
```

**Key insight:** The edge B↔C was discovered from three different anchor contexts. With endpoint reinforcement enabled, B or C themselves can also serve as anchors (up to the threshold), but after the threshold, only third-party anchors (A, E, G, etc.) can reinforce the edge.

**Confidence grows via a capped exponential heuristic:**
- Base confidence: 0.55 (single anchor)
- Growth rate: 0.15 per additional anchor
- Hard cap: 0.95 (no false certainty)
- ACTIVE threshold: 0.70 (typically ~3 distinct anchors)

---

## Filtering & Reinforcement Logic

### Reinforcement Flow

The system uses a **two-phase reinforcement strategy**:

1. **Endpoint Reinforcement** (when enabled): Edges can be reinforced by querying their endpoints (B or C for edge B-C), but only up to a threshold
2. **Third-Party Anchor Reinforcement**: After the threshold, edges can only be reinforced via third-party anchors (A, E, G, etc.)

This balances fast reinforcement for popular products with efficiency (avoiding redundant LLM calls).

### Anchor↔Candidate Edges

**Default Behavior (Endpoint Reinforcement Enabled):**

Before asking the LLM, we check if candidates are already connected to the anchor:
- **Not connected**: Include candidate → LLM will create new edge
- **Connected with low anchors (< threshold)**: Include candidate → LLM will reinforce edge
- **Connected with high anchors (≥ threshold)**: Filter out → Avoid redundant inference

**Configuration:**
- `allow_endpoint_reinforcement: bool = True` - Enable/disable endpoint reinforcement
- `endpoint_reinforcement_threshold: int = 2` - Max anchors_seen count for endpoint reinforcement
- `endpoint_reinforcement_max_confidence: float = 0.70` - Max confidence for endpoint reinforcement

**Note on multiple edge types:** If multiple semantic edge types exist between the same pair (e.g., `A↔C (COMPLEMENTS)` and `A↔C (SUBSTITUTE_FOR)`), endpoint reinforcement gating uses the **maximum** anchors_seen count and **maximum** confidence across those types. This keeps filtering stable and prevents repeatedly re-inferencing a pair once *any* relationship type is already “mature”.

**Flow Diagram:**

```
Query B → C appears as vector candidate
  ↓
Check: Does B-C exist?
  ├─ No → Include C → LLM(B, [C, ...]) → Create B-C
  └─ Yes → Check metadata:
      ├─ anchors_seen < 2 AND confidence < 0.70?
      │   └─ Yes → Include C → LLM(B, [C, ...]) → Reinforce B-C
      └─ No → Filter C → No LLM call for B-C
```

**Example with Endpoint Reinforcement:**

```
Initial: B-C exists, anchors_seen=[A], confidence=0.55

Query B → C appears as candidate
  └── Check: B-C has 1 anchor (< threshold of 2)
  └── Include C → LLM(B, [C, D, E])
  └── LLM re-infers B-C → anchors_seen=[A, B], confidence=0.63

Query B again → C appears as candidate
  └── Check: B-C has 2 anchors (≥ threshold of 2)
  └── Filter C → LLM(B, [D, E]) only
  └── B-C not reinforced (threshold reached)

Query G → candidates [B, C]
  └── LLM(G, [B, C]) → LLM infers B-C
  └── anchors_seen=[A, B, G], confidence=0.70 → ACTIVE
```

**Why This Design?**
- **Early edges** (few anchors) benefit from endpoint reinforcement → faster confidence growth
- **Mature edges** (many anchors) rely on third-party anchors → avoids redundant calls
- **Popular products** queried frequently can still reinforce their edges (up to threshold)
- **Efficiency**: Prevents infinite reinforcement loops from repeated endpoint queries

**Legacy Behavior (Endpoint Reinforcement Disabled):**

If `allow_endpoint_reinforcement=False`, all connected candidates are filtered out:
- Reinforcement only happens via **reciprocal discovery** (query B, find A as candidate)
- More conservative, but edges may take longer to reach ACTIVE status

### Candidate↔Candidate Edges

We **do NOT filter** candidate↔candidate edges before the LLM call.

**Why no filtering?**
- Candidate↔candidate edges are discovered indirectly (via anchor queries)
- Re-inference from different anchors IS the reinforcement mechanism
- The current anchor is recorded in `anchors_seen` regardless

**Example:**
```
Query A → candidates [B, C] → LLM infers B↔C → created (anchors_seen=[A])
Query E → candidates [B, C] → LLM re-infers B↔C → reinforced (anchors_seen=[A, E])
```

The edge B↔C is strengthened because two independent anchor queries both led to its discovery.

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

*These are intentional tradeoffs, not oversights.*

---

## Known Limitations & Future Considerations

The following are acknowledged limitations in v1, documented here to inform future iteration:

### 1. Token Costs at Scale

Each query may enqueue an LLM call with anchor + candidates (when there are eligible vector candidates and an API key is configured). At high query volume, costs can escalate.

**Future mitigations:**
- Caching inference results for repeated queries
- Batching multiple anchors into a single LLM call
- A "saturation threshold" where densely-connected graph regions skip LLM inference

### 2. No Edge Decay or Dispute Mechanism

Once an edge is created, it persists indefinitely.

**Future versions may need:**
- Edge expiry for stale relationships (e.g., seasonal products)
- A dispute/downvote mechanism to flag bad edges
- Type migration (e.g., `SIMILAR_TO` → `SUBSTITUTE_FOR` as evidence changes)

### 3. Single Embedding Field

Currently `embed_text` is derived only from description.

**For richer retrieval:**
- Concatenate title + category + tags into `embed_text`
- The `EmbeddingConfig` is designed for this — extend `FIELDS` tuple and bump `VERSION`

### 4. Hybrid Search Not Implemented

The current `Neo4jVectorStore` implements pure vector similarity. True hybrid search (vector + keyword/fulltext) would require:
- A fulltext index on Product nodes
- Score blending between vector similarity and BM25/keyword relevance

This often improves precision for product search use cases.

### 5. Graph Monotonicity

The graph only grows — edges are never removed or demoted in v1. This is intentional for simplicity but means early bad inferences persist. A future "edge review" or confidence decay mechanism could address this.

---

## License

*[Add license information here]*
