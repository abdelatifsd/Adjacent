from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Set, Optional, List, Dict, Any
import re, json

from neo4j import GraphDatabase, Driver


@dataclass(frozen=True)
class Neo4jEdgeStoreConfig:
    uri: str
    user: str
    password: str
    product_label: str = "Product"
    rel_type: str = "RECOMMENDATION"  # single Neo4j relationship type container


class Neo4jEdgeStore:
    """
    Edge store for recommendation edges.

    Data model assumption:
      (:Product {id}) nodes exist.
      Relationship is a single Neo4j relationship type (default :RECOMMENDATION)
      and your schema's semantic 'edge_type' is stored as a relationship property.
    """

    def __init__(self, config: Neo4jEdgeStoreConfig, driver: Driver | None = None):
        """
        Initialize the edge store.

        Args:
            config: Store configuration
            driver: Optional shared Neo4j driver. If not provided, creates its own.
                    When providing a driver, the caller is responsible for closing it.
        """
        self.config = config
        self._validate_identifiers()
        self._owns_driver = driver is None
        self.driver: Driver = driver or GraphDatabase.driver(
            config.uri,
            auth=(config.user, config.password),
        )

    def close(self) -> None:
        """Close the driver if this instance owns it."""
        if self._owns_driver and self.driver:
            self.driver.close()

    def __enter__(self) -> "Neo4jEdgeStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _validate_identifiers(self) -> None:
        # Prevent Cypher injection via label/type interpolation.
        ident = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        if not ident.fullmatch(self.config.product_label):
            raise ValueError(f"Invalid product_label: {self.config.product_label}")
        if not ident.fullmatch(self.config.rel_type):
            raise ValueError(f"Invalid rel_type: {self.config.rel_type}")

    def get_anchor_edges(self, anchor_id: str, candidate_ids: Iterable[str]) -> Set[str]:
        """
        Return the set of candidate IDs that already have *any* recommendation
        edge with the anchor.

        Why we pass candidate_ids:
          We only care about overlap with the current top-k retrieval results.
          We do not want to fetch all neighbors of the anchor.

        Cypher logic:
          MATCH anchor node a
          MATCH any connected product c via the container relationship type
          Restrict c to the given candidate list
          Return the connected candidate IDs
        """
        candidates: List[str] = list(candidate_ids)
        if not candidates:
            return set()

        # Relationship type + label must be interpolated (Cypher doesn't parameterize them)
        cypher = f"""
        MATCH (a:{self.config.product_label} {{id: $anchor_id}})
        MATCH (a)-[:{self.config.rel_type}]-(c:{self.config.product_label})
        WHERE c.id IN $candidate_ids
        RETURN collect(DISTINCT c.id) AS connected_ids
        """

        with self.driver.session() as session:
            record = session.run(
                cypher,
                anchor_id=anchor_id,
                candidate_ids=candidates,
            ).single()

        if record is None:
            # Anchor didn't exist, or no matches.
            return set()

        connected = record.get("connected_ids", [])
        return set(connected)

    def get_anchor_edges_with_metadata(
        self, anchor_id: str, candidate_ids: Iterable[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Return a dict mapping candidate_id -> edge metadata for edges between
        anchor and candidates.
        
        Used for endpoint reinforcement: allows checking anchors_seen count
        and confidence to decide whether to allow reinforcement.
        
        NOTE: Multiple semantic edge types can exist between the same product
        pair. This method aggregates across *all* relationships between
        (anchor_id, candidate_id) so the caller's gating logic is stable.

        Returns:
            Dict[str, Dict[str, Any]] where keys are candidate IDs and values
            contain edge metadata:
            - max_anchor_count: int max number of anchors_seen across edge types
            - max_confidence_0_to_1: float max confidence across edge types
        """
        candidates: List[str] = list(candidate_ids)
        if not candidates:
            return {}
        
        cypher = f"""
        MATCH (a:{self.config.product_label} {{id: $anchor_id}})
        MATCH (a)-[r:{self.config.rel_type}]-(c:{self.config.product_label})
        WHERE c.id IN $candidate_ids
        RETURN
            c.id AS candidate_id,
            max(size(coalesce(r.anchors_seen, []))) AS max_anchor_count,
            max(coalesce(r.confidence_0_to_1, 0.0)) AS max_confidence
        """
        
        with self.driver.session() as session:
            records = list(session.run(
                cypher,
                anchor_id=anchor_id,
                candidate_ids=candidates,
            ))
        
        result = {}
        for rec in records:
            candidate_id = rec["candidate_id"]
            max_anchor_count = rec.get("max_anchor_count") or 0
            max_confidence = rec.get("max_confidence") or 0.0
            result[candidate_id] = {
                "max_anchor_count": int(max_anchor_count),
                "max_confidence_0_to_1": float(max_confidence),
            }
        
        return result

    def upsert_edge(self, edge: Dict[str, Any]) -> None:
        """
        Upsert a fully materialized edge (schema edge.json) into Neo4j.

        - MERGE endpoints by Product {id} (safe even if nodes already exist)
        - MERGE relationship by edge_id to avoid duplicates
        - Store semantic edge_type as relationship property r.edge_type
        - Store edge_props dict as JSON string property r.edge_props_json
        - Store datetimes as ISO strings (v1)
        """
        edge_id = edge["edge_id"]
        from_id = edge["from_id"]
        to_id = edge["to_id"]

        # Canonical ordering should already be enforced upstream.
        # We do not reorder here because that can hide upstream bugs.
        # If you want safety, uncomment below:
        # if from_id > to_id:
        #     raise ValueError("Edge endpoints not canonical: from_id must be <= to_id")

        # Build relationship properties, excluding endpoints and raw edge_props
        rel_props: Dict[str, Any] = {}
        for k, v in edge.items():
            if k in {"from_id", "to_id", "edge_props"}:
                continue
            rel_props[k] = v

        # Store edge_props as JSON string (v1)
        edge_props = edge.get("edge_props") or {}
        rel_props["edge_props_json"] = json.dumps(edge_props, ensure_ascii=False, separators=(",", ":"))

        cypher = f"""
        MERGE (a:{self.config.product_label} {{id: $from_id}})
        MERGE (b:{self.config.product_label} {{id: $to_id}})
        MERGE (a)-[r:{self.config.rel_type} {{edge_id: $edge_id}}]->(b)
        SET r += $rel_props
        """

        with self.driver.session() as session:
            session.run(
                cypher,
                from_id=from_id,
                to_id=to_id,
                edge_id=edge_id,
                rel_props=rel_props,
            )

    def get_edge(self, edge_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a stored edge by edge_id.

        Returns a dict shaped like your edge schema:
          - from_id, to_id
          - relationship properties
          - edge_props parsed from edge_props_json
        """
        cypher = f"""
        MATCH (a:{self.config.product_label})-[r:{self.config.rel_type} {{edge_id: $edge_id}}]-(b:{self.config.product_label})
        RETURN a.id AS a_id, b.id AS b_id, properties(r) AS props
        LIMIT 1
        """

        with self.driver.session() as session:
            record = session.run(cypher, edge_id=edge_id).single()

        if record is None:
            return None

        a_id: str = record["a_id"]
        b_id: str = record["b_id"]
        props: Dict[str, Any] = dict(record["props"] or {})

        edge_props_json = props.pop("edge_props_json", None)
        if edge_props_json:
            try:
                edge_props = json.loads(edge_props_json)
            except Exception:
                edge_props = {}
        else:
            edge_props = {}

        # Canonicalize endpoints on return
        if a_id <= b_id:
            from_id, to_id = a_id, b_id
        else:
            from_id, to_id = b_id, a_id

        out: Dict[str, Any] = {
            "from_id": from_id,
            "to_id": to_id,
            **props,
            "edge_props": edge_props,
        }
        return out

    def get_neighbors(
        self,
        anchor_id: str,
        limit: int,
        edge_type: Optional[str] = None,
        min_conf: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch neighbors for an anchor via stored recommendation edges.

        Filters:
          - edge_type: matches r.edge_type
          - min_conf: matches r.confidence_0_to_1 >= min_conf

        Returns list of dicts with:
          - candidate_id
          - from_id/to_id (canonical)
          - relationship properties
          - edge_props parsed from edge_props_json
        """
        where_parts: List[str] = ["a.id = $anchor_id"]
        params: Dict[str, Any] = {"anchor_id": anchor_id, "limit": int(limit)}

        if edge_type is not None:
            where_parts.append("r.edge_type = $edge_type")
            params["edge_type"] = edge_type

        if min_conf is not None:
            where_parts.append("r.confidence_0_to_1 >= $min_conf")
            params["min_conf"] = float(min_conf)

        where_clause = " AND ".join(where_parts)

        # NOTE: multiple relationships can exist between the same (a,b) pair
        # (one per semantic edge_type). We de-duplicate by candidate_id,
        # selecting the "best" relationship by confidence + recency.
        cypher = f"""
        MATCH (a:{self.config.product_label} {{id: $anchor_id}})-[r:{self.config.rel_type}]-(b:{self.config.product_label})
        WHERE {where_clause}
        WITH b.id AS candidate_id, properties(r) AS props
        ORDER BY props.confidence_0_to_1 DESC, props.last_reinforced_at DESC
        WITH candidate_id, collect(props)[0] AS props
        ORDER BY props.confidence_0_to_1 DESC, props.last_reinforced_at DESC
        LIMIT $limit
        RETURN candidate_id, props
        """

        with self.driver.session() as session:
            records = list(session.run(cypher, **params))

        out: List[Dict[str, Any]] = []
        for rec in records:
            candidate_id: str = rec["candidate_id"]
            props: Dict[str, Any] = dict(rec["props"] or {})

            edge_props_json = props.pop("edge_props_json", None)
            if edge_props_json:
                try:
                    edge_props = json.loads(edge_props_json)
                except Exception:
                    edge_props = {}
            else:
                edge_props = {}

            # canonical endpoints for the edge
            if anchor_id <= candidate_id:
                from_id, to_id = anchor_id, candidate_id
            else:
                from_id, to_id = candidate_id, anchor_id

            out.append(
                {
                    "candidate_id": candidate_id,
                    "from_id": from_id,
                    "to_id": to_id,
                    **props,
                    "edge_props": edge_props,
                }
            )

        return out

