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
    local_model_state: Literal["loaded", "loading", "unavailable", "not_started"] = Field(
        default="not_started",
        description="State of the PREFERRED local SentenceTransformer path, which "
        "'status' does not cover: 'status' reflects only the Ollama probe and "
        "stays 'ready' while the local model is still cold. 'loaded' = resident "
        "(~150-450 ms per encode); 'loading' = the background pre-warm is still "
        "materialising weights and a VectorRoute request arriving now blocks on "
        "the same lock; 'unavailable' = load failed and requests fall back to "
        "Ollama. Measured cost of that blocking window: 167,679 ms with an empty "
        "HF cache, 12,012 ms once the cache is volume-backed.",
    )


class SynthesisHealth(BaseModel):
    """LLM answer-synthesis counters since process start.

    Exists because synthesis degrades silently (docs/05 §7): every failure is
    absorbed into the deterministic renderer, so HTTP status alone cannot reveal
    that the narrative synthesis is not running at all. A ``fallback_rate`` at or
    near ``1.0`` means the LLM path is effectively dead.

    Counters are process-local and reset on restart — see
    ``backend/app/services/synthesizer/stats.py`` for the full caveats.
    """

    model_config = ConfigDict(frozen=True)

    llm_calls: int = Field(
        default=0,
        description="Successful LLM syntheses since process start",
    )
    fallback_calls: int = Field(
        default=0,
        description="Degraded (deterministic-fallback) syntheses since start",
    )
    fallback_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Share of synthesis attempts served by the deterministic "
        "renderer, 0.0-1.0",
    )
    fallback_by_reason: dict[str, int] = Field(
        default_factory=dict,
        description="Fallback counts per canonical reason (timeout, unreachable, "
        "transport, http, empty, citation_stripped, unknown)",
    )
    last_llm_ms: float | None = Field(
        default=None,
        description="Wall time of the most recent successful LLM call in ms",
    )
    last_fallback_reason: str | None = Field(
        default=None,
        description="Canonical reason for the most recent fallback",
    )
    degraded: bool = Field(
        default=False,
        description="True once any attempt has fallen back",
    )
    scope: str = Field(
        default="process",
        description="Counter scope. Always 'process': counters reset on restart "
        "and are not shared across workers",
    )


class HealthResponse(BaseModel):
    """Unified system health check response."""

    model_config = ConfigDict(frozen=True)

    status: Literal["healthy", "degraded", "unhealthy"]
    version: str = Field(default="1.0.0", description="API application version")
    degraded: bool = Field(
        default=False,
        description="True when the service is answering but a capability is "
        "effectively dead — currently only the narrative synthesis path "
        "(attempted, never once succeeded). Deliberately separate from "
        "`status`: `status` stayed `healthy` while synthesis timed out on 100% "
        "of requests, because the deterministic renderer kept serving. A "
        "dashboard that reads only `status` could not see it; this field is at "
        "the top level so it cannot be missed. Does NOT flip on a single "
        "fallback, only on a path with no successes at all.",
    )
    database: DatabaseHealth
    llm_service: LLMServiceHealth
    embedding_service: EmbeddingServiceHealth
    synthesis: SynthesisHealth = Field(
        default_factory=SynthesisHealth,
        description="LLM answer-synthesis counters since process start; the only "
        "signal that distinguishes a working LLM from one that silently never "
        "succeeds",
    )
    evidence_layer_ready: bool = Field(
        default=False,
        description="True if Phase 5 Evidence layer (EvidenceUnifier + EvidenceRanker + EvidenceSet) imports and exposes its canonical API",
    )
