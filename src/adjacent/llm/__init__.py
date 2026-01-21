"""LLM inference components for edge discovery."""

from adjacent.llm.views import LLMProductView, project, project_many
from adjacent.llm.edge_inference import EdgeInferenceService, EdgeInferenceConfig

__all__ = [
    "LLMProductView",
    "project",
    "project_many",
    "EdgeInferenceService",
    "EdgeInferenceConfig",
]
