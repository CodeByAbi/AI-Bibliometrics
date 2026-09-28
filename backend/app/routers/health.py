"""Health check router.

Docs Reference: docs/06 Api Design.md §6.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from backend.app.db.pool import check_db_health
from backend.app.models.health import HealthResponse
from backend.app.services.ollama import check_ollama_health

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="System Health & Dependency Probe",
    description="Inspects PostgreSQL connectivity, canonical table readiness, pgvector status, and Ollama LLM service.",
)
async def get_health() -> HealthResponse:
    """Execute dependency health probes across DB, pgvector, and Ollama."""
    db_health = await check_db_health()
    llm_health, embed_health = await check_ollama_health()

    # Determine overall system health status
    if db_health.status == "disconnected":
        system_status = "unhealthy"
    elif db_health.silver_tables_ready and db_health.pgvector_ready:
        system_status = "healthy"
    else:
        system_status = "degraded"

    return HealthResponse(
        status=system_status,
        version="1.0.0",
        database=db_health,
        llm_service=llm_health,
        embedding_service=embed_health,
    )
