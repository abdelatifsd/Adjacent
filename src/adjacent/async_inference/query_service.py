# src/adjacent/async_inference/query_service.py
"""
Fast-path query service with async inference.

Returns recommendations immediately from graph + vector search,
while enqueueing LLM inference for graph enrichment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from redis import Redis
from rq import Queue

from adjacent.embeddings import EmbeddingService, HuggingFaceEmbedding, OpenAIEmbedding
from adjacent.stores import Neo4jVectorStore
from adjacent.stores.neo4j_edge_store import Neo4jEdgeStore, Neo4jEdgeStoreConfig
from adjacent.async_inference.config import AsyncConfig
from adjacent.db import Neo4jContext
from commons.metrics import span, emit_counter, generate_trace_id

logger = logging.getLogger(__name__)


@dataclass
class Recommendation:
    """A single recommendation."""

    product_id: str
    edge_type: Optional[str]
    confidence: Optional[float]
    source: Literal["graph", "vector"]  # Where this rec came from
    score: Optional[float] = None  # Vector similarity score (if from vector)


@dataclass
class QueryResult:
    """Result from a query() call."""

    anchor_id: str
    recommendations: List[Recommendation]

    # Composition stats
    from_graph: int
    from_vector: int

    # Inference status
    inference_status: Literal["complete", "enqueued", "skipped"]
    job_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API response."""
        return {
            "anchor_id": self.anchor_id,
            "recommendations": [
                {
                    "product_id": r.product_id,
                    "edge_type": r.edge_type,
                    "confidence": r.confidence,
                    "source": r.source,
                    "score": r.score,
                }
                for r in self.recommendations
            ],
            "from_graph": self.from_graph,
            "from_vector": self.from_vector,
            "inference_status": self.inference_status,
            "job_id": self.job_id,
        }


class QueryService:
    """
    Fast-path query handler.

    Returns recommendations immediately with low latency.
    Enqueues LLM inference asynchronously for graph enrichment.
    """

    def __init__(self, config: AsyncConfig):
        self.config = config

        # Redis + RQ
        self._redis = Redis.from_url(config.redis_url)
        self._queue = Queue(config.queue_name, connection=self._redis)

        # Embedding provider
        self._embedding_service = self._create_embedding_service()

        # Shared Neo4j driver
        self._neo4j_ctx = Neo4jContext(
            uri=config.neo4j_uri,
            user=config.neo4j_user,
            password=config.neo4j_password,
        )

        # Stores (share the driver)
        self._vector_store = Neo4jVectorStore(
            driver=self._neo4j_ctx.driver,
        )

        self._edge_store_config = Neo4jEdgeStoreConfig(
            uri=config.neo4j_uri,
            user=config.neo4j_user,
            password=config.neo4j_password,
        )

    def _create_embedding_service(self) -> EmbeddingService:
        """Create embedding service based on config."""
        if self.config.embedding_provider == "openai":
            if not self.config.openai_api_key:
                raise ValueError(
                    "OpenAI API key required for openai embedding provider"
                )
            provider = OpenAIEmbedding(
                api_key=self.config.openai_api_key,
                model=self.config.embedding_model or "text-embedding-3-small",
            )
        else:
            provider = HuggingFaceEmbedding(
                model_name=self.config.embedding_model
                or "sentence-transformers/all-MiniLM-L6-v2"
            )
        return EmbeddingService(provider)

    def close(self) -> None:
        """Release resources."""
        if self._vector_store:
            self._vector_store.close()
        if self._neo4j_ctx:
            self._neo4j_ctx.close()
        if self._redis:
            self._redis.close()

    def __enter__(self) -> "QueryService":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _fetch_product(self, product_id: str) -> Optional[Dict[str, Any]]:
        """Fetch product with embedding from Neo4j."""
        return self._neo4j_ctx.fetch_product(product_id)

    def _get_embedding(self, product: Dict[str, Any]) -> List[float]:
        """Get embedding from product or compute it."""
        if product.get("embedding"):
            return product["embedding"]

        text = product.get("description", "")
        if not text:
            raise ValueError(f"Product {product.get('id')} has no description")

        return self._embedding_service.embed_query(text).embedding

    def _increment_query_count(self, product_id: str) -> None:
        """Increment total_query_count for an anchor product."""
        cypher = """
        MATCH (p:Product {id: $product_id})
        SET p.total_query_count = coalesce(p.total_query_count, 0) + 1
        """
        with self._neo4j_ctx.driver.session() as session:
            session.run(cypher, product_id=product_id)

    def query(
        self,
        product_id: str,
        *,
        top_k: int = 10,
        skip_inference: bool = False,
        trace_id: str | None = None,
    ) -> QueryResult:
        """
        Get recommendations with guaranteed low latency.

        1. Query existing graph edges
        2. If needed, supplement with vector similarity
        3. Enqueue inference for new candidates (async)
        4. Return merged results immediately

        Args:
            product_id: Anchor product ID
            top_k: Number of recommendations to return
            skip_inference: If True, don't enqueue inference task
            trace_id: Optional trace ID for request correlation

        Returns:
            QueryResult with recommendations and metadata
        """
        trace_id = trace_id or generate_trace_id()
        logger.info("Query for product: %s (trace_id=%s)", product_id, trace_id)

        with span(
            "query_total",
            operation="query",
            trace_id=trace_id,
            logger=logger,
            product_id=product_id,
        ) as total_ctx:
            # Fetch anchor
            with span(
                "fetch_anchor", operation="query", trace_id=trace_id, logger=logger
            ):
                anchor = self._fetch_product(product_id)
                if not anchor:
                    raise ValueError(f"Product not found: {product_id}")

            # Increment query count for this anchor
            with span(
                "increment_query_count", operation="query", trace_id=trace_id, logger=logger
            ):
                self._increment_query_count(product_id)

            recommendations: List[Recommendation] = []
            from_graph = 0
            from_vector = 0
            candidates_enqueued = 0

            # Step 1: Get existing graph edges
            with Neo4jEdgeStore(
                self._edge_store_config, driver=self._neo4j_ctx.driver
            ) as edge_store:
                with span(
                    "graph_neighbors",
                    operation="query",
                    trace_id=trace_id,
                    logger=logger,
                ) as ctx:
                    neighbors = edge_store.get_neighbors(
                        anchor_id=product_id,
                        limit=top_k,
                    )

                    for n in neighbors:
                        recommendations.append(
                            Recommendation(
                                product_id=n["candidate_id"],
                                edge_type=n.get("edge_type"),
                                confidence=n.get("confidence_0_to_1"),
                                source="graph",
                            )
                        )
                    from_graph = len(recommendations)
                    ctx.set_count("from_graph", from_graph)

                    logger.info("Found %d graph edges for %s", from_graph, product_id)

                # Step 2: If we need more, use vector search
                need_more = top_k - len(recommendations)
                vector_candidates = []

                if need_more > 0:
                    with span(
                        "vector_search",
                        operation="query",
                        trace_id=trace_id,
                        logger=logger,
                    ) as ctx:
                        embedding = self._get_embedding(anchor)

                        search_results = self._vector_store.similarity_search(
                            query_embedding=embedding,
                            top_k=top_k + from_graph + 1,  # Extra for filtering
                            fields=["id"],  # Minimal projection - only need ID
                        )

                        # Filter out anchor and already-recommended
                        existing_ids = {r.product_id for r in recommendations}
                        existing_ids.add(product_id)

                        for result in search_results:
                            pid = result["product"]["id"]
                            if pid not in existing_ids and len(recommendations) < top_k:
                                recommendations.append(
                                    Recommendation(
                                        product_id=pid,
                                        edge_type=None,  # No edge yet
                                        confidence=None,
                                        source="vector",
                                        score=result.get("score"),
                                    )
                                )
                                vector_candidates.append(pid)
                                existing_ids.add(pid)

                        from_vector = len(vector_candidates)
                        ctx.set_count("from_vector", from_vector)
                        logger.info(
                            "Added %d vector results for %s", from_vector, product_id
                        )

                # Step 3: Enqueue inference for new candidates
                job_id = None
                inference_status: Literal["complete", "enqueued", "skipped"] = (
                    "complete"
                )

                # Get candidates not yet connected to anchor
                if not skip_inference and self.config.openai_api_key:
                    with span(
                        "enqueue_inference",
                        operation="query",
                        trace_id=trace_id,
                        logger=logger,
                    ) as ctx:
                        # Check which vector candidates need inference
                        all_candidate_ids = [
                            r.product_id
                            for r in recommendations
                            if r.source == "vector"
                        ]

                        if all_candidate_ids:
                            if self.config.allow_endpoint_reinforcement:
                                # Get edges with metadata to check reinforcement eligibility
                                connected_edges = (
                                    edge_store.get_anchor_edges_with_metadata(
                                        product_id, all_candidate_ids
                                    )
                                )

                                new_candidates = []
                                for cid in all_candidate_ids:
                                    if cid not in connected_edges:
                                        # Not connected, include for inference
                                        new_candidates.append(cid)
                                    else:
                                        # Connected - check if we should allow reinforcement
                                        edge_info = connected_edges[cid]
                                        anchor_count = int(
                                            edge_info.get("max_anchor_count", 0)
                                        )
                                        confidence = float(
                                            edge_info.get("max_confidence_0_to_1", 0.0)
                                        )

                                        # Allow reinforcement if:
                                        # 1. Anchor count is below threshold, AND
                                        # 2. Confidence is below max threshold
                                        if (
                                            anchor_count
                                            < self.config.endpoint_reinforcement_threshold
                                            and confidence
                                            < self.config.endpoint_reinforcement_max_confidence
                                        ):
                                            new_candidates.append(cid)
                                            logger.debug(
                                                "Allowing endpoint reinforcement for %s-%s "
                                                "(anchors=%d, confidence=%.2f)",
                                                product_id,
                                                cid,
                                                anchor_count,
                                                confidence,
                                            )
                                        # else: filter out (too many anchors or high confidence)
                            else:
                                # Original behavior: filter all connected candidates
                                connected = edge_store.get_anchor_edges(
                                    product_id, all_candidate_ids
                                )
                                new_candidates = [
                                    cid
                                    for cid in all_candidate_ids
                                    if cid not in connected
                                ]

                            if new_candidates:
                                # Serialize config for RQ (dataclass → dict)
                                config_dict = {
                                    "neo4j_uri": self.config.neo4j_uri,
                                    "neo4j_user": self.config.neo4j_user,
                                    "neo4j_password": self.config.neo4j_password,
                                    "openai_api_key": self.config.openai_api_key,
                                    "llm_model": self.config.llm_model,
                                    "system_prompt_path": str(
                                        self.config.system_prompt_path
                                    ),
                                    "user_prompt_path": str(
                                        self.config.user_prompt_path
                                    ),
                                    "edge_patch_schema_path": str(
                                        self.config.edge_patch_schema_path
                                    ),
                                }

                                job = self._queue.enqueue(
                                    "adjacent.async_inference.tasks.infer_edges",
                                    anchor_id=product_id,
                                    candidate_ids=new_candidates,
                                    config_dict=config_dict,
                                    trace_id=trace_id,
                                    job_timeout=self.config.job_timeout,
                                )
                                job_id = job.id
                                inference_status = "enqueued"
                                candidates_enqueued = len(new_candidates)
                                ctx.set_count(
                                    "candidates_enqueued", candidates_enqueued
                                )
                                logger.info(
                                    "Enqueued inference job %s for %d candidates",
                                    job_id,
                                    len(new_candidates),
                                )
                else:
                    if skip_inference:
                        inference_status = "skipped"
                    elif not self.config.openai_api_key:
                        inference_status = "skipped"  # No API key

            # Emit counters for query parameters and results
            emit_counter(
                "top_k", top_k, operation="query", trace_id=trace_id, logger=logger
            )
            emit_counter(
                "skip_inference",
                1 if skip_inference else 0,
                operation="query",
                trace_id=trace_id,
                logger=logger,
            )

            # Set total counts on outer span
            total_ctx.set_count("from_graph", from_graph)
            total_ctx.set_count("from_vector", from_vector)
            total_ctx.set_count("candidates_enqueued", candidates_enqueued)

            return QueryResult(
                anchor_id=product_id,
                recommendations=recommendations,
                from_graph=from_graph,
                from_vector=from_vector,
                inference_status=inference_status,
                job_id=job_id,
            )

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Check status of an inference job."""
        from rq.job import Job

        try:
            job = Job.fetch(job_id, connection=self._redis)
            return {
                "job_id": job_id,
                "status": job.get_status(),
                "result": job.result if job.is_finished else None,
                "error": str(job.exc_info) if job.is_failed else None,
            }
        except Exception as e:
            return {
                "job_id": job_id,
                "status": "not_found",
                "error": str(e),
            }
