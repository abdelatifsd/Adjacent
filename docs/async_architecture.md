# Async Edge Inference Architecture

> Design sketch for decoupling query latency from LLM inference.

## Current Architecture (Synchronous)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           User Query                                     │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                  │
│  │   Embed     │───▶│   Vector    │───▶│   Filter    │                  │
│  │   Query     │    │   Search    │    │  Connected  │                  │
│  └─────────────┘    └─────────────┘    └─────────────┘                  │
│                                              │                           │
│                                              ▼                           │
│                     ┌──────────────────────────────────┐                │
│                     │      LLM Inference (BLOCKING)     │ ◀── 300-2000ms│
│                     │   anchor + candidates → patches   │                │
│                     └──────────────────────────────────┘                │
│                                              │                           │
│                                              ▼                           │
│                     ┌─────────────┐    ┌─────────────┐                  │
│                     │ Materialize │───▶│   Store     │                  │
│                     │   Edges     │    │   Edges     │                  │
│                     └─────────────┘    └─────────────┘                  │
│                                              │                           │
│                                              ▼                           │
│                              Return Recommendations                      │
└─────────────────────────────────────────────────────────────────────────┘

Total latency: 400-2500ms (dominated by LLM call)
```

**Problems:**
- High latency on first query for any anchor
- LLM failures block the entire request
- No opportunity for batching across concurrent queries
- Rate limits can cause cascading timeouts

---

## Proposed Architecture (Async)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           User Query                                     │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                     Query Service (Fast Path)                    │    │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │    │
│  │  │   Embed     │───▶│   Vector    │───▶│   Graph     │          │    │
│  │  │   Query     │    │   Search    │    │   Query     │          │    │
│  │  └─────────────┘    └─────────────┘    └─────────────┘          │    │
│  │                              │                │                  │    │
│  │                              ▼                ▼                  │    │
│  │                     ┌─────────────────────────────┐             │    │
│  │                     │    Merge & Rank Results     │             │    │
│  │                     └─────────────────────────────┘             │    │
│  │                                    │                             │    │
│  │                                    ▼                             │    │
│  │                     Return Recommendations (< 100ms)             │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              │ (async)                                   │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                     Inference Queue                              │    │
│  │  ┌───────────────────────────────────────────────────────────┐  │    │
│  │  │ { anchor_id, candidate_ids, priority, enqueued_at }       │  │    │
│  │  └───────────────────────────────────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                     Inference Worker(s)                          │    │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │    │
│  │  │   Batch     │───▶│    LLM      │───▶│ Materialize │          │    │
│  │  │   Tasks     │    │  Inference  │    │  & Store    │          │    │
│  │  └─────────────┘    └─────────────┘    └─────────────┘          │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Component Design

### 1. Query Service (Fast Path)

The query service handles user-facing requests with guaranteed low latency.

```python
@dataclass
class QueryResult:
    """Result from the fast query path."""
    anchor_id: str
    recommendations: List[Recommendation]
    
    # Metadata about result composition
    from_graph: int          # Count from existing edges
    from_vector: int         # Count from vector similarity (no edge yet)
    
    # Inference status
    inference_status: Literal["complete", "pending", "not_needed"]
    inference_task_id: Optional[str] = None


class QueryService:
    """Fast-path query handler. Never blocks on LLM."""
    
    def __init__(
        self,
        vector_store: Neo4jVectorStore,
        edge_store: Neo4jEdgeStore,
        embedding_service: EmbeddingService,
        inference_queue: InferenceQueue,
    ):
        self._vector_store = vector_store
        self._edge_store = edge_store
        self._embedding_service = embedding_service
        self._inference_queue = inference_queue
    
    def query(
        self,
        product_id: str,
        *,
        top_k: int = 10,
        include_vector_fallback: bool = True,
    ) -> QueryResult:
        """
        Get recommendations with guaranteed low latency.
        
        1. Query existing graph edges (fast)
        2. If insufficient, supplement with vector similarity
        3. Enqueue inference task for graph enrichment (async)
        """
        # Step 1: Get existing graph edges
        graph_results = self._edge_store.get_neighbors(
            anchor_id=product_id,
            limit=top_k,
        )
        
        # Step 2: If we have enough high-quality edges, return early
        if len(graph_results) >= top_k:
            return QueryResult(
                anchor_id=product_id,
                recommendations=self._to_recommendations(graph_results, source="graph"),
                from_graph=len(graph_results),
                from_vector=0,
                inference_status="complete",
            )
        
        # Step 3: Supplement with vector similarity
        anchor = self._fetch_product(product_id)
        embedding = self._get_embedding(anchor)
        
        vector_results = self._vector_store.similarity_search(
            query_embedding=embedding,
            top_k=top_k * 2,  # Fetch extra for filtering
        )
        
        # Filter out anchor and already-connected products
        graph_ids = {r["candidate_id"] for r in graph_results}
        vector_candidates = [
            r for r in vector_results
            if r["product"]["id"] != product_id
            and r["product"]["id"] not in graph_ids
        ]
        
        # Step 4: Enqueue inference task (async)
        task_id = None
        if vector_candidates:
            task_id = self._inference_queue.enqueue(
                InferenceTask(
                    anchor_id=product_id,
                    candidate_ids=[c["product"]["id"] for c in vector_candidates[:top_k]],
                )
            )
        
        # Step 5: Merge results
        combined = self._merge_results(
            graph_results=graph_results,
            vector_results=vector_candidates[:top_k - len(graph_results)],
            limit=top_k,
        )
        
        return QueryResult(
            anchor_id=product_id,
            recommendations=combined,
            from_graph=len(graph_results),
            from_vector=len(combined) - len(graph_results),
            inference_status="pending" if task_id else "complete",
            inference_task_id=task_id,
        )
```

### 2. Inference Queue

Manages pending inference tasks with deduplication and prioritization.

```python
@dataclass
class InferenceTask:
    """A pending edge inference task."""
    task_id: str
    anchor_id: str
    candidate_ids: List[str]
    priority: int = 0              # Higher = more urgent
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Deduplication key
    @property
    def dedup_key(self) -> str:
        return f"{self.anchor_id}:{','.join(sorted(self.candidate_ids))}"


class InferenceQueue(Protocol):
    """Abstract interface for inference task queue."""
    
    def enqueue(self, task: InferenceTask) -> str:
        """Enqueue a task, returns task_id. Deduplicates by anchor+candidates."""
        ...
    
    def dequeue_batch(self, max_size: int = 10) -> List[InferenceTask]:
        """Dequeue up to max_size tasks for processing."""
        ...
    
    def mark_complete(self, task_id: str) -> None:
        """Mark a task as completed."""
        ...
    
    def mark_failed(self, task_id: str, error: str) -> None:
        """Mark a task as failed with error message."""
        ...
    
    def get_status(self, task_id: str) -> Optional[TaskStatus]:
        """Get status of a task."""
        ...


# Implementation options:
# - RedisQueue: Redis lists + sets for deduplication
# - PostgresQueue: Database table with FOR UPDATE SKIP LOCKED
# - InMemoryQueue: Simple queue for single-process deployments
```

### 3. Inference Worker

Processes inference tasks from the queue, with batching support.

```python
class InferenceWorker:
    """
    Background worker that processes inference tasks.
    
    Supports batching multiple anchors into fewer LLM calls
    when they share candidate pools.
    """
    
    def __init__(
        self,
        queue: InferenceQueue,
        edge_inference: EdgeInferenceService,
        edge_store: Neo4jEdgeStore,
        materializer: EdgeMaterializer,
        *,
        batch_size: int = 5,
        poll_interval: float = 1.0,
    ):
        self._queue = queue
        self._edge_inference = edge_inference
        self._edge_store = edge_store
        self._materializer = materializer
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._running = False
    
    def run(self) -> None:
        """Main worker loop."""
        self._running = True
        logger.info("Inference worker started")
        
        while self._running:
            tasks = self._queue.dequeue_batch(max_size=self._batch_size)
            
            if not tasks:
                time.sleep(self._poll_interval)
                continue
            
            for task in tasks:
                try:
                    self._process_task(task)
                    self._queue.mark_complete(task.task_id)
                except Exception as e:
                    logger.error("Task %s failed: %s", task.task_id, e)
                    self._queue.mark_failed(task.task_id, str(e))
    
    def _process_task(self, task: InferenceTask) -> None:
        """Process a single inference task."""
        logger.info("Processing task %s: anchor=%s, candidates=%d",
                    task.task_id, task.anchor_id, len(task.candidate_ids))
        
        # Fetch products
        anchor = self._fetch_product(task.anchor_id)
        candidates = [self._fetch_product(cid) for cid in task.candidate_ids]
        candidates = [c for c in candidates if c is not None]
        
        if not candidates:
            logger.info("No valid candidates for task %s", task.task_id)
            return
        
        # LLM inference
        patches = self._edge_inference.construct_patch(
            anchor=anchor,
            candidates=candidates,
        )
        
        logger.info("Task %s: LLM returned %d patches", task.task_id, len(patches))
        
        # Materialize and store
        for patch in patches:
            edge_id = compute_edge_id(
                patch["edge_type"],
                *canonical_pair(patch["from_id"], patch["to_id"])
            )
            existing = self._edge_store.get_edge(edge_id)
            
            full_edge = self._materializer.materialize(
                patch=patch,
                anchor_id=task.anchor_id,
                existing_edge=existing,
            )
            
            self._edge_store.upsert_edge(full_edge)
    
    def stop(self) -> None:
        """Signal worker to stop."""
        self._running = False
```

### 4. Anchor Status Tracking

Track inference status per anchor for smarter query decisions.

```python
@dataclass
class AnchorStatus:
    """Inference status for an anchor product."""
    anchor_id: str
    last_inference_at: Optional[datetime]
    inference_count: int
    edge_count: int
    status: Literal["fresh", "stale", "never_inferred"]


class AnchorStatusStore:
    """Track inference status per anchor in Neo4j."""
    
    def get_status(self, anchor_id: str) -> AnchorStatus:
        """Get inference status for an anchor."""
        cypher = """
        MATCH (p:Product {id: $anchor_id})
        OPTIONAL MATCH (p)-[r:RECOMMENDATION]-()
        RETURN p.last_inference_at AS last_inference,
               p.inference_count AS inference_count,
               count(r) AS edge_count
        """
        # ... implementation
    
    def mark_inferred(self, anchor_id: str) -> None:
        """Update anchor after successful inference."""
        cypher = """
        MATCH (p:Product {id: $anchor_id})
        SET p.last_inference_at = datetime(),
            p.inference_count = coalesce(p.inference_count, 0) + 1
        """
        # ... implementation
```

---

## Integration Points

### API Layer

```python
# FastAPI example
from fastapi import FastAPI, BackgroundTasks

app = FastAPI()
query_service = QueryService(...)

@app.get("/recommend/{product_id}")
async def get_recommendations(
    product_id: str,
    top_k: int = 10,
) -> QueryResult:
    """
    Get recommendations for a product.
    
    Returns immediately with best available results.
    If graph is incomplete, inference runs async and
    subsequent queries will have richer results.
    """
    return query_service.query(product_id, top_k=top_k)


@app.get("/recommend/{product_id}/status")
async def get_inference_status(product_id: str) -> AnchorStatus:
    """Check inference status for an anchor."""
    return anchor_status_store.get_status(product_id)
```

### Worker Deployment

```python
# worker.py - Run separately from API
from adjacent.async_inference import InferenceWorker, RedisQueue

def main():
    queue = RedisQueue(redis_url="redis://localhost:6379")
    worker = InferenceWorker(
        queue=queue,
        edge_inference=EdgeInferenceService(...),
        edge_store=Neo4jEdgeStore(...),
        materializer=EdgeMaterializer(),
        batch_size=5,
    )
    
    try:
        worker.run()
    except KeyboardInterrupt:
        worker.stop()

if __name__ == "__main__":
    main()
```

---

## Migration Path

### Phase 1: Add Queue Infrastructure
- Implement `InferenceQueue` interface
- Add `AnchorStatusStore` 
- No changes to existing `Recommender` behavior

### Phase 2: Add Worker
- Implement `InferenceWorker`
- Deploy worker alongside existing system
- Both sync and async paths work

### Phase 3: Add QueryService
- Implement fast-path `QueryService`
- New API endpoints use `QueryService`
- Old endpoints still use sync `Recommender`

### Phase 4: Migrate
- Switch all traffic to async path
- Deprecate sync `Recommender.recommend()`
- Keep `Recommender` for batch/offline use

---

## Trade-offs

| Aspect | Sync (Current) | Async (Proposed) |
|--------|----------------|------------------|
| First query latency | High (LLM call) | Low (vector only) |
| Result quality on first query | Best available | May be "thin" |
| LLM failures | Block request | Graceful degradation |
| Batching opportunity | None | Yes (worker batches) |
| Complexity | Simple | More moving parts |
| Consistency | Immediate | Eventually consistent |

---

## Optional Enhancements

### 1. Warm-up / Pre-inference
Queue popular products for inference before they're queried:

```python
def warmup_popular_products(top_n: int = 1000):
    """Pre-infer edges for popular products."""
    popular = get_popular_products(limit=top_n)
    for product_id in popular:
        if not has_recent_inference(product_id):
            inference_queue.enqueue(InferenceTask(
                anchor_id=product_id,
                candidate_ids=get_vector_neighbors(product_id),
                priority=1,  # Lower than user-triggered
            ))
```

### 2. Inference Budgeting
Limit LLM calls per time window:

```python
class BudgetedInferenceQueue(InferenceQueue):
    """Queue with rate limiting."""
    
    def __init__(self, max_calls_per_minute: int = 60):
        self._rate_limiter = RateLimiter(max_calls_per_minute)
    
    def dequeue_batch(self, max_size: int = 10) -> List[InferenceTask]:
        available = self._rate_limiter.available()
        return super().dequeue_batch(min(max_size, available))
```

### 3. Priority Lanes
Separate queues for different priority levels:

```python
class PriorityInferenceQueue:
    """Multi-lane queue with priority."""
    
    def __init__(self):
        self._high = RedisQueue("inference:high")
        self._normal = RedisQueue("inference:normal")
        self._low = RedisQueue("inference:low")
    
    def enqueue(self, task: InferenceTask) -> str:
        if task.priority >= 10:
            return self._high.enqueue(task)
        elif task.priority >= 5:
            return self._normal.enqueue(task)
        else:
            return self._low.enqueue(task)
    
    def dequeue_batch(self, max_size: int) -> List[InferenceTask]:
        # Drain high priority first, then normal, then low
        tasks = self._high.dequeue_batch(max_size)
        if len(tasks) < max_size:
            tasks.extend(self._normal.dequeue_batch(max_size - len(tasks)))
        if len(tasks) < max_size:
            tasks.extend(self._low.dequeue_batch(max_size - len(tasks)))
        return tasks
```
