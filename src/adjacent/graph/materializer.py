# src/adjacent/graph/materializer.py

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def compute_edge_id(edge_type: str, a: str, b: str) -> str:
    raw = f"{edge_type}:{a}:{b}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"edge_{digest}"


def confidence_from_anchors(
    n: int,
    base: float = 0.55,
    growth: float = 0.15,
    cap: float = 0.95,
) -> float:
    """
    Capped exponential growth.
    n = number of distinct anchors_seen

    """
    if n <= 0:
        return 0.0
    value = base + (1 - base) * (1 - (1 - growth) ** n)
    return min(cap, round(value, 3))


class EdgeMaterializer:
    """
    Converts LLM edge patches into stored RecommendationEdge records.
    """

    def materialize(
        self,
        *,
        patch: Dict[str, Any],
        anchor_id: str,
        existing_edge: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Args:
            patch: single edge patch from LLM (edge_patch.json)
            anchor_id: product ID that triggered inference
            existing_edge: previously stored edge, if any

        Returns:
            Fully materialized edge conforming to edge.json
        """

        edge_type = patch["edge_type"]
        a_raw, b_raw = patch["from_id"], patch["to_id"]
        a, b = canonical_pair(a_raw, b_raw)

        now = utc_now()

        if existing_edge is None:
            anchors_seen = [anchor_id]
            created_at = now
        else:
            anchors_seen = list(existing_edge.get("anchors_seen", []))
            created_at = existing_edge["created_at"]

            if anchor_id not in anchors_seen:
                anchors_seen.append(anchor_id)

        confidence = confidence_from_anchors(len(anchors_seen))

        status = "ACTIVE" if confidence >= 0.7 else "PROPOSED"

        edge = {
            "edge_id": compute_edge_id(edge_type, a, b),
            "edge_type": edge_type,
            "from_id": a,
            "to_id": b,
            "anchors_seen": anchors_seen,
            "confidence_0_to_1": confidence,
            "status": status,
            "created_at": created_at,
            "last_reinforced_at": now,
            "notes": patch.get("notes"),
            "edge_props": patch.get("edge_props", {}),
        }

        return edge
