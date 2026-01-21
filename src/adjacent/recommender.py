# src/adjacent/recommender.py
"""
Core recommendation loop for Adjacent.

This module orchestrates:
1. Vector similarity search (candidate retrieval)
2. Graph filtering (skip already-connected candidates)
3. LLM inference (edge patch generation)
4. Edge materialization (patch → full edge)
5. Edge storage (Neo4j upsert)
6. Recommendation retrieval (neighbors query)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase
from openai import OpenAI

from adjacent.embeddings import EmbeddingService, EmbeddingProvider, HuggingFaceEmbedding, OpenAIEmbedding
from adjacent.stores import Neo4jVectorStore
from adjacent.stores.neo4j_edge_store import Neo4jEdgeStore, Neo4jEdgeStoreConfig
from adjacent.llm.edge_inference import EdgeInferenceService, EdgeInferenceConfig
from adjacent.llm.views import project, project_many
from adjacent.graph.materializer import EdgeMaterializer, compute_edge_id, canonical_pair

logger = logging.getLogger(__name__)


@dataclass
class RecommenderConfig:
    """Configuration for the Recommender service."""
    
    # Neo4j connection
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "adjacent123"
    
    # Embedding provider
    embedding_provider: str = "huggingface"  # "huggingface" or "openai"
    embedding_model: Optional[str] = None    # Provider-specific model override
    
    # LLM inference
    llm_model: str = "gpt-4o-mini"
    openai_api_key: Optional[str] = None
    
    # Paths (relative to project root)
    system_prompt_path: Path = field(default_factory=lambda: Path("src/adjacent/prompts/edge_infer.system.txt"))
    user_prompt_path: Path = field(default_factory=lambda: Path("src/adjacent/prompts/edge_infer.user.txt"))
    edge_patch_schema_path: Path = field(default_factory=lambda: Path("schemas/edge_patch.json"))
    
    # Search parameters
    top_k_candidates: int = 10
    max_recommendations: int = 10
    min_confidence: float = 0.0  # Minimum confidence for returned recommendations


@dataclass
class Recommendation:
    """A single recommendation result."""
    
    product_id: str
    edge_type: str
    confidence: float
    status: str
    source: str  # "graph" (existing edge) or "inferred" (new from this query)
    notes: Optional[str] = None


@dataclass 
class RecommendationResult:
    """Result of a recommend() call."""
    
    anchor_id: str
    recommendations: List[Recommendation]
    candidates_retrieved: int
    candidates_filtered: int  # Already connected
    edges_inferred: int       # New edges from LLM
    edges_reinforced: int     # Existing edges reinforced


class Recommender:
    """
    Core recommendation engine.
    
    Orchestrates the lazy graph construction loop:
    query → embed → retrieve → filter → infer → materialize → store → return
    """
    
    def __init__(self, config: RecommenderConfig):
        self.config = config
        self._validate_config()
        
        # Initialize embedding provider
        self._embedding_provider = self._create_embedding_provider()
        self._embedding_service = EmbeddingService(self._embedding_provider)
        
        # Initialize stores
        self._vector_store = Neo4jVectorStore(
            uri=config.neo4j_uri,
            user=config.neo4j_user,
            password=config.neo4j_password,
        )
        
        edge_store_config = Neo4jEdgeStoreConfig(
            uri=config.neo4j_uri,
            user=config.neo4j_user,
            password=config.neo4j_password,
        )
        self._edge_store = Neo4jEdgeStore(edge_store_config)
        
        # Initialize LLM inference (lazy - only if API key provided)
        self._llm_client: Optional[OpenAI] = None
        self._edge_inference: Optional[EdgeInferenceService] = None
        if config.openai_api_key:
            self._init_llm_inference()
        
        # Edge materializer (stateless)
        self._materializer = EdgeMaterializer()
    
    def _validate_config(self) -> None:
        """Validate configuration."""
        if self.config.embedding_provider == "openai" and not self.config.openai_api_key:
            raise ValueError("openai_api_key required when embedding_provider='openai'")
    
    def _create_embedding_provider(self) -> EmbeddingProvider:
        """Create the configured embedding provider."""
        if self.config.embedding_provider == "openai":
            return OpenAIEmbedding(
                api_key=self.config.openai_api_key,
                model=self.config.embedding_model or "text-embedding-3-small",
            )
        elif self.config.embedding_provider == "huggingface":
            return HuggingFaceEmbedding(
                model_name=self.config.embedding_model or "sentence-transformers/all-MiniLM-L6-v2"
            )
        else:
            raise ValueError(f"Unknown embedding provider: {self.config.embedding_provider}")
    
    def _init_llm_inference(self) -> None:
        """Initialize LLM inference components."""
        self._llm_client = OpenAI(api_key=self.config.openai_api_key)
        
        inference_config = EdgeInferenceConfig(
            model=self.config.llm_model,
            system_prompt_path=self.config.system_prompt_path,
            user_prompt_path=self.config.user_prompt_path,
            edge_schema_path=self.config.edge_patch_schema_path,
        )
        
        self._edge_inference = EdgeInferenceService(
            client=self._llm_client,
            config=inference_config,
        )
    
    def close(self) -> None:
        """Release resources."""
        if self._vector_store:
            self._vector_store.close()
        if self._edge_store:
            self._edge_store.close()
    
    def __enter__(self) -> "Recommender":
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
    
    def _fetch_product(self, product_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a product from Neo4j by ID."""
        cypher = """
        MATCH (p:Product {id: $product_id})
        RETURN p {
            .id,
            .title,
            .description,
            .category,
            .brand,
            .tags,
            .price,
            .currency,
            .embedding
        } AS product
        """
        driver = GraphDatabase.driver(
            self.config.neo4j_uri,
            auth=(self.config.neo4j_user, self.config.neo4j_password),
        )
        with driver:
            with driver.session() as session:
                result = session.run(cypher, product_id=product_id)
                record = result.single()
                if record:
                    return dict(record["product"])
        return None
    
    def _get_or_compute_embedding(self, product: Dict[str, Any]) -> List[float]:
        """Get product embedding, computing if necessary."""
        # If product already has embedding, use it
        if product.get("embedding"):
            return product["embedding"]
        
        # Otherwise, compute from description
        text = product.get("description", "")
        if not text:
            raise ValueError(f"Product {product.get('id')} has no description for embedding")
        
        result = self._embedding_service.embed_query(text)
        return result.embedding
    
    def recommend(
        self,
        product_id: str,
        *,
        skip_inference: bool = False,
        edge_types: Optional[List[str]] = None,
    ) -> RecommendationResult:
        """
        Get recommendations for a product.
        
        This is the core loop:
        1. Fetch the anchor product
        2. Get its embedding (or compute it)
        3. Vector search for top-K candidates
        4. Filter out already-connected candidates
        5. If new candidates exist and LLM is configured, infer edges
        6. Materialize and store new edges
        7. Return all recommendations (existing + new)
        
        Args:
            product_id: The anchor product ID
            skip_inference: If True, skip LLM inference (return only existing edges)
            edge_types: Optional filter for specific edge types
            
        Returns:
            RecommendationResult with recommendations and statistics
        """
        logger.info("Recommending for product: %s", product_id)
        
        # Step 1: Fetch anchor product
        anchor = self._fetch_product(product_id)
        if not anchor:
            raise ValueError(f"Product not found: {product_id}")
        
        # Step 2: Get embedding
        anchor_embedding = self._get_or_compute_embedding(anchor)
        
        # Step 3: Vector similarity search
        search_results = self._vector_store.similarity_search(
            query_embedding=anchor_embedding,
            top_k=self.config.top_k_candidates + 1,  # +1 because anchor might be in results
        )
        
        # Remove anchor from results if present
        candidates = [
            r for r in search_results 
            if r["product"]["id"] != product_id
        ][:self.config.top_k_candidates]
        
        candidates_retrieved = len(candidates)
        logger.info("Retrieved %d candidates via vector search", candidates_retrieved)
        
        # Step 4: Filter out already-connected candidates
        candidate_ids = [c["product"]["id"] for c in candidates]
        # Candidates that are already connected to the anchor - we'll use them to filter
        connected_ids = self._edge_store.get_anchor_edges(product_id, candidate_ids)
        
        new_candidate_ids = set(candidate_ids) - connected_ids

        # What the LLM will edge-connect on
        new_candidates = [c for c in candidates if c["product"]["id"] in new_candidate_ids]
        
        candidates_filtered = len(connected_ids)
        logger.info("Filtered %d already-connected candidates", candidates_filtered)
        
        # Step 5 & 6: LLM inference and materialization (if applicable)
        edges_inferred = 0
        edges_reinforced = 0
        inferred_product_ids: set[str] = set()
        
        if new_candidates and not skip_inference and self._edge_inference:
            logger.info("Inferring edges for %d new candidates", len(new_candidates))
            
            # Project to LLMProductView for consistent input
            anchor_view = project(anchor)
            candidate_views = project_many([c["product"] for c in new_candidates])
            
            # Call LLM with projected views
            try:
                edge_patches = self._edge_inference.construct_patch(
                    anchor=anchor_view,
                    candidates=candidate_views,
                )
                
                logger.info("LLM returned %d edge patches", len(edge_patches))
                
                # Materialize and store each patch
                for patch in edge_patches:
                    # Compute edge_id to check if edge exists
                    edge_type = patch["edge_type"]
                    a, b = canonical_pair(patch["from_id"], patch["to_id"])
                    edge_id = compute_edge_id(edge_type, a, b)
                    
                    # Check for existing edge
                    existing_edge = self._edge_store.get_edge(edge_id)
                    
                    # Materialize
                    full_edge = self._materializer.materialize(
                        patch=patch,
                        anchor_id=product_id,
                        existing_edge=existing_edge,
                    )
                    
                    # Store
                    self._edge_store.upsert_edge(full_edge)
                    
                    # Track for result
                    if existing_edge:
                        edges_reinforced += 1
                    else:
                        edges_inferred += 1
                        # Track which products got new edges
                        other_id = b if a == product_id else a
                        inferred_product_ids.add(other_id)
                
                logger.info("Stored %d new edges, reinforced %d existing", edges_inferred, edges_reinforced)
                
            except Exception as e:
                logger.error("LLM inference failed: %s", e)
                # Continue without new inferences
        
        # Step 7: Fetch all recommendations (existing + newly created)
        neighbors = self._edge_store.get_neighbors(
            anchor_id=product_id,
            limit=self.config.max_recommendations,
            edge_type=edge_types[0] if edge_types and len(edge_types) == 1 else None,
            min_conf=self.config.min_confidence if self.config.min_confidence > 0 else None,
        )
        
        # Build recommendation list
        recommendations: List[Recommendation] = []
        for neighbor in neighbors:
            # Filter by edge_types if multiple specified
            if edge_types and neighbor.get("edge_type") not in edge_types:
                continue
            
            rec = Recommendation(
                product_id=neighbor["candidate_id"],
                edge_type=neighbor.get("edge_type", "UNKNOWN"),
                confidence=neighbor.get("confidence_0_to_1", 0.0),
                status=neighbor.get("status", "UNKNOWN"),
                source="inferred" if neighbor["candidate_id"] in inferred_product_ids else "graph",
                notes=neighbor.get("notes"),
            )
            recommendations.append(rec)
        
        return RecommendationResult(
            anchor_id=product_id,
            recommendations=recommendations,
            candidates_retrieved=candidates_retrieved,
            candidates_filtered=candidates_filtered,
            edges_inferred=edges_inferred,
            edges_reinforced=edges_reinforced,
        )
    
    def recommend_similar(self, product_id: str, **kwargs) -> RecommendationResult:
        """Convenience method: recommend only SIMILAR_TO edges."""
        return self.recommend(product_id, edge_types=["SIMILAR_TO"], **kwargs)
    
    def recommend_complements(self, product_id: str, **kwargs) -> RecommendationResult:
        """Convenience method: recommend only COMPLEMENTS edges."""
        return self.recommend(product_id, edge_types=["COMPLEMENTS"], **kwargs)
    
    def recommend_substitutes(self, product_id: str, **kwargs) -> RecommendationResult:
        """Convenience method: recommend only SUBSTITUTE_FOR edges."""
        return self.recommend(product_id, edge_types=["SUBSTITUTE_FOR"], **kwargs)


# ----------------------------
# CLI for testing
# ----------------------------
def main() -> None:
    """Simple CLI for testing recommendations."""
    import argparse
    import os
    
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    parser = argparse.ArgumentParser(description="Get recommendations for a product")
    parser.add_argument("product_id", help="Product ID to get recommendations for")
    parser.add_argument("--neo4j-uri", default="bolt://localhost:7688")
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument("--neo4j-password", default="adjacent123")
    parser.add_argument("--embedding-provider", choices=["huggingface", "openai"], default="huggingface")
    parser.add_argument("--openai-api-key", default=os.environ.get("OPENAI_API_KEY"))
    parser.add_argument("--llm-model", default="gpt-4o-mini")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--skip-inference", action="store_true", help="Skip LLM inference")
    parser.add_argument("--edge-type", help="Filter by edge type")
    args = parser.parse_args()
    
    config = RecommenderConfig(
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        embedding_provider=args.embedding_provider,
        openai_api_key=args.openai_api_key,
        llm_model=args.llm_model,
        top_k_candidates=args.top_k,
    )
    
    with Recommender(config) as recommender:
        result = recommender.recommend(
            args.product_id,
            skip_inference=args.skip_inference,
            edge_types=[args.edge_type] if args.edge_type else None,
        )
        
        print(f"\n=== Recommendations for {result.anchor_id} ===")
        print(f"Candidates retrieved: {result.candidates_retrieved}")
        print(f"Candidates filtered (already connected): {result.candidates_filtered}")
        print(f"New edges inferred: {result.edges_inferred}")
        print(f"Existing edges reinforced: {result.edges_reinforced}")
        print(f"\nRecommendations ({len(result.recommendations)}):")
        
        for i, rec in enumerate(result.recommendations, 1):
            print(f"  {i}. {rec.product_id}")
            print(f"     Type: {rec.edge_type} | Confidence: {rec.confidence:.2f} | Status: {rec.status}")
            print(f"     Source: {rec.source}")
            if rec.notes:
                print(f"     Notes: {rec.notes}")


if __name__ == "__main__":
    main()
