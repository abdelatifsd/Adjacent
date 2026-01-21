# src/adjacent/llm/edge_inference.py

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openai import OpenAI

from adjacent.llm.views import LLMProductView, project


@dataclass(frozen=True)
class EdgeInferenceConfig:
    model: str
    system_prompt_path: Path
    user_prompt_path: Path
    edge_schema_path: Path
    schema_name: str = "RecommendationEdgePatch"


class EdgeInferenceService:
    """Infer recommendation edges for an anchor product against candidate products.
    
    Accepts either raw product dicts or LLMProductView objects. Raw dicts are
    automatically projected to LLMProductView for consistent LLM input.
    """

    def __init__(self, client: OpenAI, config: EdgeInferenceConfig):
        self.client = client
        self.config = config

        self._system_prompt = self._load_text(config.system_prompt_path)
        self._user_prompt_template = self._load_text(config.user_prompt_path)

        edge_schema = self._load_json(config.edge_schema_path)
        self._patch_schema = self._build_patch_schema(edge_schema=edge_schema)

    @staticmethod
    def _load_text(path: Path) -> str:
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Schema file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _build_patch_schema(edge_schema: Dict[str, Any]) -> Dict[str, Any]:
        # Wrap the edge schema so the model returns: { "edges": [ ... ] }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["edges"],
            "properties": {
                "edges": {
                    "type": "array",
                    "items": edge_schema,
                    "default": [],
                }
            },
        }

    def _to_view(self, product: Union[Dict[str, Any], LLMProductView]) -> LLMProductView:
        """Convert product to LLMProductView if needed."""
        if isinstance(product, LLMProductView):
            return product
        return project(product)

    def _render_user_prompt(
        self,
        anchor: LLMProductView,
        candidates: List[LLMProductView],
    ) -> str:
        """Render user prompt with anchor and candidates as JSON."""
        return self._user_prompt_template.format(
            ANCHOR_JSON=json.dumps(anchor.to_dict(), ensure_ascii=False),
            CANDIDATES_JSON=json.dumps([c.to_dict() for c in candidates], ensure_ascii=False),
        )

    def construct_patch(
        self,
        anchor: Union[Dict[str, Any], LLMProductView],
        candidates: List[Union[Dict[str, Any], LLMProductView]],
        *,
        request_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Infer edges between anchor and candidates.
        
        Args:
            anchor: Anchor product (raw dict or LLMProductView)
            candidates: Candidate products (raw dicts or LLMProductView)
            request_id: Optional request ID for tracking
            
        Returns:
            List of edge patch dicts conforming to edge_patch.json schema
        """
        # Project to LLMProductView for consistent input
        anchor_view = self._to_view(anchor)
        candidate_views = [self._to_view(c) for c in candidates]
        
        user_prompt = self._render_user_prompt(anchor=anchor_view, candidates=candidate_views)

        resp = self.client.responses.create(
            model=self.config.model,
            instructions=self._system_prompt,
            input=user_prompt,
            # Structured Outputs: enforce JSON schema
            text={
                "format": {
                    "type": "json_schema",
                    "name": self.config.schema_name,
                    "schema": self._patch_schema,
                    "strict": True,
                }
            },
            metadata={"request_id": request_id} if request_id else None,
        )

        # The SDK provides resp.output_text (string) for text output.
        # It should be a JSON object matching PATCH_SCHEMA because strict=True.
        payload = json.loads(resp.output_text)
        edges = payload.get("edges", [])

        if not isinstance(edges, list):
            raise ValueError("Structured output returned non-list 'edges' field")

        return edges
