"""Health check router.

Docs Reference: docs/06 Api Design.md §6.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, status

from backend.app.db.pool import check_db_health
from backend.app.models.health import HealthResponse, SynthesisHealth
from backend.app.services.ollama import check_ollama_health
from backend.app.services.synthesizer.stats import get_synthesis_stats

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
    except Exception as exc:
        from backend.app.core.logging import logger

        logger.warning("Evidence-layer health probe failed: %s", exc, exc_info=True)
        return False


def build_synthesis_health() -> SynthesisHealth:
    """Project the process-local synthesis counters into the health payload.

    Read-only and never raises: a counter read must not be able to fail the
    health endpoint. Counters reset on restart, so treat a high
    ``fallback_rate`` as authoritative immediately after boot and treat
    recovery as needing a fresh sample rather than trusting the ratio forever.
    """
    try:
        snap = get_synthesis_stats().snapshot()
    except Exception:  # pragma: no cover - defensive
        return SynthesisHealth()
    return SynthesisHealth(
        llm_calls=snap.llm_calls,
        fallback_calls=snap.fallback_calls,
        fallback_rate=snap.fallback_rate,
        fallback_by_reason=dict(snap.fallback_by_reason),
        last_llm_ms=snap.last_llm_ms,
        last_fallback_reason=snap.last_fallback_reason,
        degraded=snap.degraded,
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="System Health & Dependency Probe",
    description=(
        "Inspects PostgreSQL connectivity, canonical table readiness, pgvector "
        "status, Phase 5 Evidence layer, Ollama LLM service, and answer-synthesis "
        "fallback counters."
    ),
)
async def get_health() -> HealthResponse:
    """Probe DB, pgvector, Evidence layer, Ollama, and synthesis counters."""
    # P1 async-*: DB + Ollama probes are independent → run concurrently so
    # health latency is max(probe) instead of sum(probe) (matters most when
    # Ollama is unreachable and its timeout would otherwise be additive).
    db_health, ollama_pair = await asyncio.gather(
        check_db_health(),
        check_ollama_health(),
    )
    llm_health, embed_health = ollama_pair
    evidence_ready = check_evidence_layer_health()

    # Determine overall system health status. A degraded synthesis path does NOT
    # downgrade system_status: docs/05 §7 requires the deterministic renderer to
    # serve successfully, so the system is genuinely operational. The counters
    # are reported for operators, not as an outage signal.
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
        synthesis=build_synthesis_health(),
        evidence_layer_ready=evidence_ready,
    )
