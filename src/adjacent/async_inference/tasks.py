# src/adjacent/async_inference/tasks.py
"""
RQ tasks for async edge inference.

These tasks are enqueued by QueryService and processed by workers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase
from openai import OpenAI

from adjacent.stores.neo4j_edge_store import Neo4jEdgeStore, Neo4jEdgeStoreConfig
from adjacent.llm.edge_inference import EdgeInferenceService, EdgeInferenceConfig
from adjacent.llm.views import project, project_many
from adjacent.graph.materializer import EdgeMaterializer, compute_edge_id, canonical_pair
from adjacent.async_inference.config import AsyncConfig

logger = logging.getLogger(__name__)


def _fetch_product(config: AsyncConfig, product_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a product from Neo4j."""
    driver = GraphDatabase.driver(
        config.neo4j_uri,
        auth=(config.neo4j_user, config.neo4j_password),
    )
    cypher = """
    MATCH (p:Product {id: $product_id})
    RETURN p {
        .id, .title, .description, .category, .brand, .tags, .price, .currency
    } AS product
    """
    with driver:
        with driver.session() as session:
            result = session.run(cypher, product_id=product_id)
            record = result.single()
            if record:
                return dict(record["product"])
    return None


def _fetch_products(config: AsyncConfig, product_ids: List[str]) -> List[Dict[str, Any]]:
    """Fetch multiple products from Neo4j."""
    driver = GraphDatabase.driver(
        config.neo4j_uri,
        auth=(config.neo4j_user, config.neo4j_password),
    )
    cypher = """
    MATCH (p:Product)
    WHERE p.id IN $product_ids
    RETURN p {
        .id, .title, .description, .category, .brand, .tags, .price, .currency
    } AS product
    """
    with driver:
        with driver.session() as session:
            result = session.run(cypher, product_ids=product_ids)
            return [dict(record["product"]) for record in result]


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
    
    # Fetch products
    anchor = _fetch_product(config, anchor_id)
    if not anchor:
        return {
            "anchor_id": anchor_id,
            "edges_created": 0,
            "edges_reinforced": 0,
            "error": f"Anchor product not found: {anchor_id}",
        }
    
    candidates = _fetch_products(config, candidate_ids)
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
    edges_reinforced = 0
    anchor_edges_created = 0
    candidate_edges_created = 0
    
    with Neo4jEdgeStore(edge_store_config) as edge_store:
        for patch in patches:
            edge_type = patch["edge_type"]
            a, b = canonical_pair(patch["from_id"], patch["to_id"])
            edge_id = compute_edge_id(edge_type, a, b)
            
            existing = edge_store.get_edge(edge_id)
            
            full_edge = materializer.materialize(
                patch=patch,
                anchor_id=anchor_id,
                existing_edge=existing,
            )
            
            edge_store.upsert_edge(full_edge)
            
            if existing:
                edges_reinforced += 1
            else:
                edges_created += 1
                # Track edge type (anchor vs candidate-candidate)
                if a == anchor_id or b == anchor_id:
                    anchor_edges_created += 1
                else:
                    candidate_edges_created += 1
    
    # Update anchor's inference timestamp
    _mark_anchor_inferred(config, anchor_id)
    
    logger.info("Inference complete: anchor=%s, created=%d, reinforced=%d",
                anchor_id, edges_created, edges_reinforced)
    
    return {
        "anchor_id": anchor_id,
        "edges_created": edges_created,
        "anchor_edges_created": anchor_edges_created,
        "candidate_edges_created": candidate_edges_created,
        "edges_reinforced": edges_reinforced,
    }


def _mark_anchor_inferred(config: AsyncConfig, anchor_id: str) -> None:
    """Update anchor product with inference timestamp."""
    driver = GraphDatabase.driver(
        config.neo4j_uri,
        auth=(config.neo4j_user, config.neo4j_password),
    )
    cypher = """
    MATCH (p:Product {id: $anchor_id})
    SET p.last_inference_at = datetime(),
        p.inference_count = coalesce(p.inference_count, 0) + 1
    """
    with driver:
        with driver.session() as session:
            session.run(cypher, anchor_id=anchor_id)
