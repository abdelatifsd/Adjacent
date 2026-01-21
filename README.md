# Adjacent

> Cold-start product recommendations via lazy graph construction.

Adjacent is an open-source experimentation framework for building recommendation systems without behavioral data. It turns a raw product catalog into a semantic + graph-based recommendation engine by combining embeddings, LLM inference, and a lazily constructed knowledge graph.

This project is intentionally scoped as a research-grade prototype: the goal is clarity, correctness, and extensibility — not premature optimization or UI polish.

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
1. Embed X
2. Retrieve top-K semantically similar candidates
3. Filter out candidates already connected to X in the graph
4. Send remaining (anchor, candidates) to the LLM
5. LLM returns edge patches (partial edge info)
6. Edge patches are materialized deterministically
7. Edges are written to Neo4j
8. Recommendations are returned
```

This loop repeats, gradually enriching the graph.

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

---

## Anchors & Confidence

An edge becomes trustworthy not because the LLM said so once, but because **it keeps reappearing under different anchors**.

**Example:**

```
Query A → edge (B, C) inferred
Query F → edge (B, C) inferred again
Query Q → edge (B, C) inferred again
```

Each distinct anchor reinforces the edge.

**Confidence grows via a capped exponential heuristic:**
- Fast initial growth
- Diminishing returns
- Hard upper bound (no false certainty)

---

## Why Filter Existing Edges Before LLM Calls?

Before asking the LLM, we remove candidates already connected to the anchor.

**Why?**
- Avoid re-inferring settled edges
- Prevent contradictions
- Reduce token usage
- Ensure the graph grows monotonically

**Important nuance:**
- This does not prevent candidates from being connected to each other later
- Those relationships will be inferred when they become anchors themselves

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

Each query triggers an LLM call with anchor + candidates. At high query volume, costs can escalate.

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
