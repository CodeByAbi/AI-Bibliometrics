"""Health check schema models.

Docs Reference: docs/06 Api Design.md §6.
"""

from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class DatabaseHealth(BaseModel):
    """Database connectivity and readiness status."""

    model_config = ConfigDict(frozen=True)

    status: Literal["connected", "disconnected"]
    role: str = Field(..., description="Active database user role")
    silver_tables_ready: bool = Field(..., description="True if all 9 canonical Silver tables exist")
    gold_tables_ready: bool = Field(..., description="True if Gold analytics tables exist")
    pgvector_ready: bool = Field(..., description="True if vector extension is active and embeddings populated")
    public_tables_count: int = Field(default=0, description="Number of base tables in public schema")
    error: Optional[str] = Field(None, description="Sanitized connection error message if disconnected")


class LLMServiceHealth(BaseModel):
    """LLM provider connectivity status."""

    model_config = ConfigDict(frozen=True)

    status: Literal["connected", "disconnected"]
    model: str = Field(..., description="Configured LLM model name")
    provider: str = Field(default="Ollama", description="LLM provider name")
    available_models: list[str] = Field(default_factory=list, description="List of available models in Ollama")
    error: Optional[str] = Field(None, description="Sanitized connection error message if disconnected")


class EmbeddingServiceHealth(BaseModel):
    """Embedding service status."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ready", "unreachable"]
    model: str = Field(..., description="Configured embedding model")
    dimension: int = Field(default=1024, description="Vector dimension")
    source: str = Field(default="pgvector (stored) + Ollama/HF", description="Embedding provider source")


class HealthResponse(BaseModel):
    """Unified system health check response."""

    model_config = ConfigDict(frozen=True)

    status: Literal["healthy", "degraded", "unhealthy"]
    version: str = Field(default="1.0.0", description="API application version")
    database: DatabaseHealth
    llm_service: LLMServiceHealth
    embedding_service: EmbeddingServiceHealth
    evidence_layer_ready: bool = Field(
        default=False,
        description="True if Phase 5 Evidence layer (EvidenceUnifier + EvidenceRanker + EvidenceSet) imports and exposes its canonical API",
    )
