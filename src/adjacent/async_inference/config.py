# src/adjacent/async_inference/config.py
"""Configuration for async inference components."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class AsyncConfig:
    """
    Configuration for async inference infrastructure.

    Shared by QueryService (enqueuer) and Worker (processor).

    Note: Path fields are automatically converted from strings during
    deserialization, making this config safe for Redis/RQ serialization.
    """

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "adjacent_inference"

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7688"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "adjacent123"

    # Embedding
    embedding_provider: str = "huggingface"
    embedding_model: Optional[str] = None

    # LLM
    openai_api_key: Optional[str] = None
    llm_model: str = "gpt-4o-mini"

    # Paths (auto-converted from str when deserialized from Redis)
    system_prompt_path: Path = field(
        default_factory=lambda: Path("src/adjacent/prompts/edge_infer.system.txt")
    )
    user_prompt_path: Path = field(
        default_factory=lambda: Path("src/adjacent/prompts/edge_infer.user.txt")
    )
    edge_patch_schema_path: Path = field(
        default_factory=lambda: Path("schemas/edge_patch.json")
    )

    # Query settings
    top_k_candidates: int = 10
    max_recommendations: int = 10

    # Worker settings
    job_timeout: int = 300  # 5 minutes max per job

    # Endpoint reinforcement settings
    allow_endpoint_reinforcement: bool = True  # Enable endpoint reinforcement
    endpoint_reinforcement_threshold: int = (
        5  # Only reinforce if anchors_seen count < this value
    )
    endpoint_reinforcement_max_confidence: float = (
        0.70  # Don't reinforce if confidence >= this
    )

    def __post_init__(self):
        """Convert string paths to Path objects after deserialization."""
        # Handle paths that might come in as strings (from Redis serialization)
        if isinstance(self.system_prompt_path, str):
            self.system_prompt_path = Path(self.system_prompt_path)
        if isinstance(self.user_prompt_path, str):
            self.user_prompt_path = Path(self.user_prompt_path)
        if isinstance(self.edge_patch_schema_path, str):
            self.edge_patch_schema_path = Path(self.edge_patch_schema_path)
