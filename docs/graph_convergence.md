# Graph Convergence Behavior

This document explains how the graph converges over repeated queries, and potential strategies for handling edge cases.

## The Retry Scenario

When querying an anchor product, if the LLM creates fewer edges than `top_k`, the system will re-trigger inference on subsequent queries for the unconnected candidates.

**Example flow:**

1. Query anchor A with `top_k=10`
2. Vector search returns candidates [B, C, D, E, F, G, H, I, J, K]
3. LLM creates edges to only 7: [B, C, D, E, F, G, H]
4. Next query for A: `get_neighbors` returns 7, `need_more = 3`
5. Vector search returns the same candidates (deterministic)
6. After filtering connected ones, [I, J, K] are sent to inference again

Since vector search is deterministic (same embedding → same results), the same unconnected candidates may be retried indefinitely if the LLM consistently doesn't create edges for them.

## Why Convergence Is Observed

In practice, queries tend to converge naturally due to:

1. **LLM non-determinism** — The LLM may create edges on retry that it skipped before
2. **Low-confidence edges still count** — An edge with confidence=0.1 is still "connected" for filtering purposes, preventing future retries for that candidate
3. **Candidate↔candidate edges accumulate** — Even if anchor↔candidate edges aren't created, the LLM may create edges between candidates, enriching the graph indirectly

## Potential Solutions

If convergence becomes an issue (observable via repeated inference on the same pairs), consider:

| Approach | Complexity | Description |
|----------|------------|-------------|
| **Negative edge tracking** | Low | Store `(anchor, candidate, attempts)` for pairs the LLM doesn't connect. Skip inference after N failures. |
| **Expanding window** | Low | Progressively increase vector search limit (top-15, top-20, etc.) to discover candidates beyond the initial neighborhood. |
| **Re-anchoring** | High | Use a connected candidate as a new anchor point to explore from a different vantage in the semantic space. Requires a validation strategy to ensure discovered products remain relevant to the original anchor. |

## Current Behavior

The current behavior is acceptable for most use cases:

- Users get graph + vector fallback recommendations immediately
- Inference retries are bounded by the `need_more` count (typically 1-3 candidates)
- Cost is amortized over time as the graph matures

## Instrumentation

To quantify how often retries occur, monitor:

- `candidates_enqueued` metric per query
- Repeated `(anchor_id, candidate_id)` pairs in inference jobs
- Ratio of `edges_created` vs `candidates_count` in task results

If retries are frequent and non-converging, implement negative edge tracking as the first intervention.
