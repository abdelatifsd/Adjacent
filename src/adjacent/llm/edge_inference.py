# src/adjacent/llm/edge_inference.py

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openai import OpenAI

from adjacent.llm.views import LLMProductView, project


@dataclass(frozen=True)
class EdgeInferenceConfig:
    model: str
    system_prompt_path: Union[Path, str]
    user_prompt_path: Union[Path, str]
    edge_schema_path: Union[Path, str]
    schema_name: str = "RecommendationEdgePatch"


@dataclass(frozen=True)
class LLMInferenceResult:
    """Result from LLM inference including patches and metadata."""

    patches: List[Dict[str, Any]]
    metadata: Dict[str, Any]


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
    def _load_text(path: Union[Path, str]) -> str:
        """Load text from a file path (accepts Path or str)."""
        path = Path(path) if isinstance(path, str) else path
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _load_json(path: Union[Path, str]) -> Dict[str, Any]:
        """Load JSON from a file path (accepts Path or str)."""
        path = Path(path) if isinstance(path, str) else path
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

    @staticmethod
    def _hash_prompt(content: str) -> str:
        """Compute deterministic hash of prompt content.

        Uses SHA256 for stability; truncates to 16 chars for readability.
        Different prompt versions produce different hashes.
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def _to_view(
        self, product: Union[Dict[str, Any], LLMProductView]
    ) -> LLMProductView:
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
            CANDIDATES_JSON=json.dumps(
                [c.to_dict() for c in candidates], ensure_ascii=False
            ),
        )

    def construct_patch(
        self,
        anchor: Union[Dict[str, Any], LLMProductView],
        candidates: List[Union[Dict[str, Any], LLMProductView]],
        *,
        request_id: Optional[str] = None,
    ) -> LLMInferenceResult:
        """Infer edges between anchor and candidates.

        Args:
            anchor: Anchor product (raw dict or LLMProductView)
            candidates: Candidate products (raw dicts or LLMProductView)
            request_id: Optional request ID for tracking

        Returns:
            LLMInferenceResult with patches and metadata (token usage, model info, etc.)
        """
        # Project to LLMProductView for consistent input
        anchor_view = self._to_view(anchor)
        candidate_views = [self._to_view(c) for c in candidates]

        user_prompt = self._render_user_prompt(
            anchor=anchor_view, candidates=candidate_views
        )

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

        # Extract metadata for metrics instrumentation
        metadata = {
            # Identifiers
            "response_id": resp.id,
            "model": resp.model,
            "status": resp.status,
            # Prompt hashes for version tracking
            "system_prompt_hash": self._hash_prompt(self._system_prompt),
            "user_prompt_hash": self._hash_prompt(user_prompt),
            # Token counts (safe access with default 0)
            "input_tokens": resp.usage.input_tokens if resp.usage else 0,
            "output_tokens": resp.usage.output_tokens if resp.usage else 0,
            "total_tokens": resp.usage.total_tokens if resp.usage else 0,
            "cached_tokens": (
                resp.usage.input_tokens_details.cached_tokens
                if resp.usage and resp.usage.input_tokens_details
                else 0
            ),
            "reasoning_tokens": (
                resp.usage.output_tokens_details.reasoning_tokens
                if resp.usage and resp.usage.output_tokens_details
                else 0
            ),
        }

        # Add optional fields if present
        if resp.service_tier:
            metadata["service_tier"] = resp.service_tier

        return LLMInferenceResult(patches=edges, metadata=metadata)
