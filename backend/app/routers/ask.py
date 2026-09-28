"""Ask router endpoint (/api/v1/ask).

Docs Reference: docs/06 Api Design.md §5, docs/05 Retrieval Rag Design.md.
"""

from __future__ import annotations

import time
import uuid
from fastapi import APIRouter, Request, status
from backend.app.core.logging import logger
from backend.app.models.ask import (
    AskRequest,
    AskResponse,
    DebugInfo,
    EvidenceObject,
    SourceItem,
)

router = APIRouter(tags=["Ask"])


@router.post(
    "/ask",
    response_model=AskResponse,
    status_code=status.HTTP_200_OK,
    summary="Research Intelligence Q&A",
    description="Primary multi-route RAG endpoint returning grounded bibliometric answers with verified citations and structured EvidenceObjects.",
)
async def ask_question(
    payload: AskRequest,
    request: Request,
) -> AskResponse:
    """Handle research query request."""
    start_time = time.perf_counter()
    req_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())

    logger.info(
        "Processing ask request: question='%s' filters=%s developer_mode=%s",
        payload.question,
        payload.filters.model_dump() if payload.filters else {},
        payload.developer_mode,
        extra={"request_id": req_id, "endpoint": "/api/v1/ask"},
    )

    # Phase 2 Foundation Contract:
    # Provides validated boundary response while full Phase 3 (QueryRouter & SqlRetriever)
    # is being developed.
    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

    debug_data = None
    if payload.developer_mode:
        debug_data = DebugInfo(
            sql_executed=None,
            route_reasoning="Phase 2 Gateway Skeleton - Routing active in Phase 3",
            latency_breakdown_ms={"gateway_validation_ms": elapsed_ms},
        )

    return AskResponse(
        request_id=req_id,
        status="ok",
        route="SQLRoute",
        answer="Gateway API v1 aktif dan siap menerima kueri riset. Pipeline routing 4-jalur akan diaktifkan pada Fase 3.",
        evidence_objects=[],
        sources=[],
        candidates=None,
        filters_ignored=[],
        answered_via_fallback=False,
        unverified_citations=[],
        debug=debug_data,
    )
