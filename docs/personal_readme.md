# Personal README / Mental Model (Adjacent)

This is a **personal quick-reference** for understanding Adjacent’s **use-cases**, **flow of operations**, and **reinforcement logic**, mapped to the current code.

---

## What Adjacent is good for (use-cases)

- **Cold start recommendations**: you have a catalog, no behavioral logs.
- **On-demand enrichment**: you don’t precompute a full \(O(N^2)\) graph; you grow it as products are queried.
- **Research / iteration**: you want an auditable, schema-first pipeline where edges are inferred and reinforced over time.

---

## Key objects & where they live (code map)

- **Fast-path query handler (returns immediately)**: `src/adjacent/async_inference/query_service.py`
  - `QueryService.query(product_id, top_k=..., skip_inference=...)`
  - Returns `QueryResult` containing:
    - `recommendations` (mix of `graph` + `vector`)
    - `from_graph`, `from_vector`
    - `inference_status` + `job_id` (if enqueued)

- **Background worker job (LLM inference + graph write)**: `src/adjacent/async_inference/tasks.py`
  - `infer_edges(anchor_id, candidate_ids, config_dict=None)`

- **Edge materialization / reinforcement**: `src/adjacent/graph/materializer.py`
  - `EdgeMaterializer.materialize(patch, anchor_id, existing_edge=None)`
  - Dedupes anchors: same anchor can’t reinforce twice.

- **Neo4j edge store**: `src/adjacent/stores/neo4j_edge_store.py`
  - `get_neighbors(anchor_id, limit, ...)` (graph recs)
  - `get_anchor_edges_with_metadata(anchor_id, candidate_ids)` (used for endpoint reinforcement gating)
  - `upsert_edge(edge)` (MERGE by `edge_id`)

- **Config knobs**: `src/adjacent/async_inference/config.py`
  - `allow_endpoint_reinforcement`
  - `endpoint_reinforcement_threshold`
  - `endpoint_reinforcement_max_confidence`

---

## Flow of operations (end-to-end)

### A) User queries product A (fast path)

**Goal**: return recommendations fast, then (optionally) enqueue a background job to enrich the graph.

1. **Fetch anchor product A**
2. **Graph recs first**: fetch existing neighbors of A from Neo4j
3. **Vector fallback** (only if needed to reach `top_k`):
   - embed A (or use stored embedding)
   - vector search top candidates
   - remove duplicates (don’t re-return items already returned from graph)
4. **Decide which vector candidates should be sent to LLM** (async enrichment):
   - if a candidate is *not connected* to A → include (edge creation opportunity)
   - if candidate is connected to A → include only if endpoint-reinforcement gating allows it
5. **Enqueue async job** (RQ) with:
   - `anchor_id = "A"`
   - `candidate_ids = [...]` (filtered list)
6. **Return `QueryResult` immediately** (the job runs later)

Important mental note:
- The async worker **does not re-run retrieval**. It uses exactly the `candidate_ids` passed by the fast path.

---

### B) Worker runs `infer_edges(A, candidate_ids=[...])` (slow path)

1. Fetch product A + candidates from Neo4j
2. Call the LLM with **(anchor=A, candidates=[...])**
3. LLM returns a list of **edge patches** that may include:
   - **Anchor↔Candidate** edges: A↔Ci
   - **Candidate↔Candidate** edges: Ci↔Cj among the provided candidate set
4. For each patch:
   - Compute `edge_id` (depends on `edge_type` + canonical endpoints)
   - Load existing edge (if present)
   - Materialize (possibly append anchor to `anchors_seen`)
   - Upsert into Neo4j (MERGE by `edge_id`)

Then future queries for A tend to return more graph neighbors, reducing reliance on vector fallback.

---

## Reinforcement logic (the core “truth” mechanism)

### What “reinforcement” means

An edge becomes more trustworthy when it is observed under **distinct anchors**:

- Edge exists
- A new query anchor triggers an inference call that re-suggests this edge
- If that anchor is new for that edge, we append it to `anchors_seen`

### Same anchor can’t reinforce twice

Materialization prevents duplicates:

- If `anchor_id` is already in `anchors_seen`, no new reinforcement occurs (even if the LLM repeats the edge).

### Worker metrics (how to interpret them)

In `infer_edges`:
- `edges_created`: new edges written
- `edges_reinforced`: existing edges where `anchors_seen` gained the current anchor (real reinforcement)
- `edges_noop_existing`: edge existed but anchor already in `anchors_seen` (no-op)

---

## Endpoint reinforcement (what it is, what it’s for)

### Terminology

For edge A–C:
- **Endpoints** are A and C.
- **Third-party anchors** are other products (E, G, …) whose candidate sets included both A and C.

### Why endpoint reinforcement exists

Without it, once A–C exists, querying A may never send C back to the LLM (depending on retrieval and filtering), which can slow reinforcement for popular products.

With endpoint reinforcement enabled, the fast path may allow a connected candidate C to be re-sent to the worker **up to a bounded point**.

### The gating rule (current behavior)

When deciding whether a connected candidate C can be re-inferred from endpoint A:

- Allow if:
  - `anchor_count < endpoint_reinforcement_threshold`, AND
  - `confidence < endpoint_reinforcement_max_confidence`

### Multi-edge-type safety (important)

Adjacent allows multiple semantic edge types between the same pair (A–C can have multiple `edge_id`s).

Endpoint gating aggregates across those types:
- uses **max anchors_seen count** and **max confidence** across edge types for that pair

This makes the gate stable and conservative:
- once *any* relationship type between A and C is “mature”, we stop paying to re-infer the pair via the endpoint route.

---

## “Returned from the graph” vs “vector candidate” (common confusion)

In a query:
- **Graph results** are neighbors of A already stored as edges. They appear with `source="graph"`.
- **Vector results** fill the remaining slots and appear with `source="vector"`.
- Only **vector results** are candidates for the async LLM job.

So if C is already returned from graph neighbors, it won’t be re-sent to the worker in that call.

---

## Concrete scenario walkthroughs (copy/paste mental models)

### Scenario 1: Cold start query A (empty graph neighborhood)

Assume:
- A has no stored neighbors yet

Flow:
- QueryService returns top-k vector recs
- It enqueues async job with those candidate IDs
- Worker writes initial A↔Ci edges + some Ci↔Cj edges

Next time A is queried:
- graph neighbors exist → user gets graph recs first
- fewer vector recs → fewer candidates need inference

### Scenario 2: Query A with vector candidates [D, B, C, X]

Assume:
- A–B exists (low anchors/confidence)
- A–D exists (already “mature”)
- A–C doesn’t exist
- A–X doesn’t exist

Expected:
- B can be re-sent to the worker (endpoint reinforcement) if gate allows
- D is filtered out by the gate (already mature)
- C and X are included (creation opportunity)

Worker then:
- reinforces A–B (if anchor is new)
- creates A–C and A–X
- may emit (B–C, C–X, …) candidate↔candidate edges among the provided candidates

### Scenario 3: Same-anchor no-op

Assume:
- A–C exists and `anchors_seen` already includes A

Even if the LLM returns A–C again for anchor A:
- materializer will not add A again
- `edges_noop_existing` increments

---

## Operational knobs / “what changes behavior”

- **`top_k`** in `QueryService.query()`: controls response size and how many vector candidates you might send for inference.
- **Endpoint reinforcement settings**:
  - lower thresholds → cheaper, more conservative
  - higher thresholds → faster “maturation”, more cost
- **Candidate set size K** bounds candidate↔candidate edges:
  - worst-case pair count is \(K(K-1)/2\) (per edge type), even though LLM output is typically limited by prompt/schema.

---

## Quick “gotchas” checklist

- Multiple edge types between same pair:
  - must dedupe neighbors and aggregate gating metadata (done)
- “reinforced” metrics:
  - distinguish real anchor additions vs no-op repeats (done)
- Worker does not re-run retrieval:
  - candidate list quality is decided by fast path

