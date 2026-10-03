"""Health check router.

Docs Reference: docs/06 Api Design.md §6.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, status
from backend.app.db.pool import check_db_health
from backend.app.models.health import HealthResponse
from backend.app.services.ollama import check_ollama_health

router = APIRouter(tags=["Health"])


def check_evidence_layer_health() -> bool:
    """Probe Phase 5 Evidence layer readiness without touching the database.

    Returns True only if ``EvidenceUnifier`` exposes its canonical
    multi-source API (``from_sql``/``from_vector``/``from_graph``/
    ``from_analytics``/``unify``), ``EvidenceRanker`` exposes its
    deterministic ranking API, and ``EvidenceSet`` exposes its
    serialization + short-circuit surface. Any import or structural
    mismatch yields False (never raises) so health stays sanitized.
    """
    try:
        from backend.app.services.evidence.models import EvidenceItem, EvidenceSet
        from backend.app.services.evidence.ranker import EvidenceRanker
        from backend.app.services.evidence.unifier import EvidenceUnifier

        for method in ("from_sql", "from_vector", "from_graph", "from_analytics", "unify"):
            if not callable(getattr(EvidenceUnifier, method, None)):
                return False
        for method in ("rank_evidence_objects", "rank_sources", "rank_items"):
            if not callable(getattr(EvidenceRanker, method, None)):
                return False
        for attr in ("is_empty", "to_metrics_json", "to_chunks_text", "to_untrusted_evidence_block"):
            if not hasattr(EvidenceSet, attr):
                return False
        if not hasattr(EvidenceItem, "model_fields"):
            return False
        return True
    except Exception:
        return False


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="System Health & Dependency Probe",
    description="Inspects PostgreSQL connectivity, canonical table readiness, pgvector status, Phase 5 Evidence layer, and Ollama LLM service.",
)
async def get_health() -> HealthResponse:
    """Execute dependency health probes across DB, pgvector, Evidence layer, and Ollama."""
    # P1 async-*: DB + Ollama probes are independent → run concurrently so
    # health latency is max(probe) instead of sum(probe) (matters most when
    # Ollama is unreachable and its timeout would otherwise be additive).
    db_health, ollama_pair = await asyncio.gather(
        check_db_health(),
        check_ollama_health(),
    )
    llm_health, embed_health = ollama_pair
    evidence_ready = check_evidence_layer_health()

    # Determine overall system health status
    if db_health.status == "disconnected":
        system_status = "unhealthy"
    elif db_health.silver_tables_ready and db_health.pgvector_ready and evidence_ready:
        system_status = "healthy"
    else:
        system_status = "degraded"

    return HealthResponse(
        status=system_status,
        version="1.0.0",
        database=db_health,
        llm_service=llm_health,
        embedding_service=embed_health,
        evidence_layer_ready=evidence_ready,
    )
