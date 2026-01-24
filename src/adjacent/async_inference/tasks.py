# src/adjacent/async_inference/tasks.py
"""
RQ tasks for async edge inference.

These tasks are enqueued by QueryService and processed by workers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from openai import OpenAI

from adjacent.stores.neo4j_edge_store import Neo4jEdgeStore, Neo4jEdgeStoreConfig
from adjacent.llm.edge_inference import EdgeInferenceService, EdgeInferenceConfig
from adjacent.llm.views import project, project_many
from adjacent.graph.materializer import EdgeMaterializer, compute_edge_id, canonical_pair
from adjacent.async_inference.config import AsyncConfig
from adjacent.db import Neo4jContext

logger = logging.getLogger(__name__)


def infer_edges(
    anchor_id: str,
    candidate_ids: List[str],
    config_dict: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    RQ task: Infer edges between anchor and candidates.
    
    This runs in a worker process. It:
    1. Fetches anchor and candidate products from Neo4j
    2. Calls LLM for edge inference
    3. Materializes and stores edges
    4. Returns summary statistics
    
    Args:
        anchor_id: The anchor product ID
        candidate_ids: List of candidate product IDs
        config_dict: Optional config overrides (serialized for RQ)
        
    Returns:
        Dict with inference results:
        - anchor_id
        - edges_created
        - edges_reinforced
        - errors (if any)
    """
    # Reconstruct config (RQ serializes args, so we pass dict)
    if config_dict:
        config = AsyncConfig(**config_dict)
    else:
        config = AsyncConfig()
    
    logger.info("Starting inference task: anchor=%s, candidates=%d", anchor_id, len(candidate_ids))

    # Validate we have LLM credentials
    if not config.openai_api_key:
        return {
            "anchor_id": anchor_id,
            "edges_created": 0,
            "edges_reinforced": 0,
            "error": "No OpenAI API key configured",
        }

    # Create shared Neo4j context for this task
    neo4j_ctx = Neo4jContext(
        uri=config.neo4j_uri,
        user=config.neo4j_user,
        password=config.neo4j_password,
    )

    try:
        # Fetch products
        anchor = neo4j_ctx.fetch_product(anchor_id)
        if not anchor:
            return {
                "anchor_id": anchor_id,
                "edges_created": 0,
                "edges_reinforced": 0,
                "error": f"Anchor product not found: {anchor_id}",
            }

        candidates = neo4j_ctx.fetch_products(candidate_ids)
        if not candidates:
            logger.info("No valid candidates found for anchor %s", anchor_id)
            return {
                "anchor_id": anchor_id,
                "edges_created": 0,
                "edges_reinforced": 0,
            }

        # Initialize LLM service
        client = OpenAI(api_key=config.openai_api_key)
        inference_config = EdgeInferenceConfig(
            model=config.llm_model,
            system_prompt_path=config.system_prompt_path,
            user_prompt_path=config.user_prompt_path,
            edge_schema_path=config.edge_patch_schema_path,
        )
        edge_inference = EdgeInferenceService(client=client, config=inference_config)

        # Initialize stores
        edge_store_config = Neo4jEdgeStoreConfig(
            uri=config.neo4j_uri,
            user=config.neo4j_user,
            password=config.neo4j_password,
        )
        materializer = EdgeMaterializer()

        # Project to LLM views
        anchor_view = project(anchor)
        candidate_views = project_many(candidates)

        # Call LLM
        try:
            patches = edge_inference.construct_patch(
                anchor=anchor_view,
                candidates=candidate_views,
            )
        except Exception as e:
            logger.error("LLM inference failed: %s", e)
            return {
                "anchor_id": anchor_id,
                "edges_created": 0,
                "edges_reinforced": 0,
                "error": f"LLM inference failed: {e}",
            }

        logger.info("LLM returned %d patches for anchor %s", len(patches), anchor_id)

        # Materialize and store
        edges_created = 0
        # "reinforced" = anchors_seen actually gained the current anchor_id
        edges_reinforced = 0
        edges_noop_existing = 0
        anchor_edges_created = 0
        candidate_edges_created = 0

        with Neo4jEdgeStore(edge_store_config, driver=neo4j_ctx.driver) as edge_store:
            for patch in patches:
                edge_type = patch["edge_type"]
                a, b = canonical_pair(patch["from_id"], patch["to_id"])
                edge_id = compute_edge_id(edge_type, a, b)

                existing = edge_store.get_edge(edge_id)
                existing_anchors = set((existing or {}).get("anchors_seen", []) or [])

                full_edge = materializer.materialize(
                    patch=patch,
                    anchor_id=anchor_id,
                    existing_edge=existing,
                )

                edge_store.upsert_edge(full_edge)

                if existing:
                    # Only count as reinforced if this anchor is newly observed.
                    if anchor_id not in existing_anchors:
                        edges_reinforced += 1
                    else:
                        edges_noop_existing += 1
                else:
                    edges_created += 1
                    # Track edge type (anchor vs candidate-candidate)
                    if a == anchor_id or b == anchor_id:
                        anchor_edges_created += 1
                    else:
                        candidate_edges_created += 1

        # Update anchor's inference timestamp
        _mark_anchor_inferred(neo4j_ctx, anchor_id)

        logger.info("Inference complete: anchor=%s, created=%d, reinforced=%d",
                    anchor_id, edges_created, edges_reinforced)

        return {
            "anchor_id": anchor_id,
            "edges_created": edges_created,
            "anchor_edges_created": anchor_edges_created,
            "candidate_edges_created": candidate_edges_created,
            "edges_reinforced": edges_reinforced,
            "edges_noop_existing": edges_noop_existing,
        }
    finally:
        neo4j_ctx.close()


def _mark_anchor_inferred(neo4j_ctx: Neo4jContext, anchor_id: str) -> None:
    """Update anchor product with inference timestamp."""
    cypher = """
    MATCH (p:Product {id: $anchor_id})
    SET p.last_inference_at = datetime(),
        p.inference_count = coalesce(p.inference_count, 0) + 1
    """
    with neo4j_ctx.driver.session() as session:
        session.run(cypher, anchor_id=anchor_id)
