"""Ask router endpoint (/api/v1/ask).

Docs Reference: docs/06 Api Design.md §5, docs/05 Retrieval Rag Design.md.
"""

from __future__ import annotations

import time
import uuid
from fastapi import APIRouter, Request, status
from backend.app.core.logging import logger
from backend.app.db.pool import get_pool
from backend.app.models.ask import (
    AskRequest,
    AskResponse,
    DebugInfo,
)
from backend.app.services.retrievers.sql_retriever import SqlRetriever
from backend.app.services.retrievers.vector_retriever import VectorRetriever
from backend.app.services.router import EntityResolutionGate, QuestionRouter
from backend.app.services.synthesizer.answer import (
    SqlAnswerSynthesizer,
    VectorAnswerSynthesizer,
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
    """Handle research query request via multi-route retrieval and grounded synthesis."""
    start_time = time.perf_counter()
    req_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    latencies: dict[str, float] = {}

    logger.info(
        "Processing ask request: question='%s' filters=%s developer_mode=%s",
        payload.question,
        payload.filters.model_dump() if payload.filters else {},
        payload.developer_mode,
        extra={"request_id": req_id, "endpoint": "/api/v1/ask"},
    )

    # 1. Route classification
    t0 = time.perf_counter()
    decision = QuestionRouter.classify_route(payload.question, payload.filters)
    latencies["routing_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    logger.info(
        "Routed ask request: route='%s' fallback=%s reasoning='%s'",
        decision.route,
        decision.answered_via_fallback,
        decision.reasoning,
        extra={"request_id": req_id, "endpoint": "/api/v1/ask", "route": decision.route},
    )

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 2. Entity resolution gate
        t1 = time.perf_counter()
        resolution = await EntityResolutionGate.resolve_entities(
            conn, payload.question, payload.filters
        )
        latencies["entity_resolution_ms"] = round((time.perf_counter() - t1) * 1000, 2)

        logger.info(
            "Entity gate resolved: status='%s' author=%s institution=%s",
            resolution.status,
            resolution.resolved_author_name,
            resolution.resolved_institution_name,
            extra={"request_id": req_id, "endpoint": "/api/v1/ask", "route": decision.route},
        )

        # Unknown-entity short-circuit: mentioned names with zero candidates (FR2.4)
        if resolution.status == "not_found":
            total_elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            latencies["total_ms"] = total_elapsed_ms
            logger.info(
                "Ask completed: status='not_found' route='%s' total_ms=%s (entity gate)",
                decision.route,
                total_elapsed_ms,
                extra={"request_id": req_id, "endpoint": "/api/v1/ask", "route": decision.route},
            )
            debug_data = None
            if payload.developer_mode:
                debug_data = DebugInfo(
                    sql_executed=None,
                    route_reasoning=decision.reasoning,
                    latency_breakdown_ms=latencies,
                )
            return AskResponse(
                request_id=req_id,
                status="not_found",
                route=decision.route,
                answer=resolution.clarification_message
                or "Data tidak ditemukan dalam database untuk kriteria pencarian tersebut.",
                evidence_objects=[],
                sources=[],
                candidates=None,
                filters_ignored=[],
                answered_via_fallback=decision.answered_via_fallback,
                unverified_citations=[],
                debug=debug_data,
            )

        # Disambiguation short-circuit: If ambiguous candidates found
        if resolution.status == "needs_clarification":
            total_elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            latencies["total_ms"] = total_elapsed_ms
            debug_data = None
            if payload.developer_mode:
                debug_data = DebugInfo(
                    sql_executed=None,
                    route_reasoning=decision.reasoning,
                    latency_breakdown_ms=latencies,
                )
            return AskResponse(
                request_id=req_id,
                status="needs_clarification",
                route=decision.route,
                answer=resolution.clarification_message
                or "Ditemukan beberapa entitas yang cocok. Silakan pilih salah satu:",
                evidence_objects=[],
                sources=[],
                candidates=resolution.candidates,
                filters_ignored=[],
                answered_via_fallback=decision.answered_via_fallback,
                unverified_citations=[],
                debug=debug_data,
            )

        # 3. SQLRoute Execution (Phase 3 Core)
        if decision.route == "SQLRoute":
            t2 = time.perf_counter()
            sql_result = await SqlRetriever.retrieve(
                conn,
                payload.question,
                filters=payload.filters,
                resolved_author_id=resolution.resolved_author_id,
                resolved_institution_id=resolution.resolved_institution_id,
            )
            latencies["sql_retrieval_ms"] = round((time.perf_counter() - t2) * 1000, 2)

            t3 = time.perf_counter()
            synth_result = SqlAnswerSynthesizer.synthesize(
                payload.question,
                sql_result,
                filters=payload.filters,
            )
            latencies["synthesis_ms"] = round((time.perf_counter() - t3) * 1000, 2)

            total_elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            latencies["total_ms"] = total_elapsed_ms

            logger.info(
                "Ask completed: status='%s' route='SQLRoute' total_ms=%s sql='%s'",
                synth_result.status,
                total_elapsed_ms,
                sql_result.sql_executed,
                extra={"request_id": req_id, "endpoint": "/api/v1/ask", "route": "SQLRoute"},
            )

            debug_data = None
            if payload.developer_mode:
                debug_data = DebugInfo(
                    sql_executed=sql_result.sql_executed,
                    route_reasoning=decision.reasoning,
                    latency_breakdown_ms=latencies,
                )

            return AskResponse(
                request_id=req_id,
                status=synth_result.status,  # type: ignore[arg-type]
                route="SQLRoute",
                answer=synth_result.answer,
                evidence_objects=synth_result.evidence_objects,
                sources=synth_result.sources,
                candidates=None,
                filters_ignored=sql_result.filters_ignored,
                answered_via_fallback=decision.answered_via_fallback,
                unverified_citations=synth_result.unverified_citations,
                debug=debug_data,
            )

        # 4. VectorRoute Execution (Phase 4 Core)
        if decision.route == "VectorRoute":
            t2 = time.perf_counter()
            vector_result = await VectorRetriever.retrieve(
                conn,
                payload.question,
                filters=payload.filters,
                resolved_author_id=resolution.resolved_author_id,
                resolved_institution_id=resolution.resolved_institution_id,
            )
            latencies["vector_retrieval_ms"] = round((time.perf_counter() - t2) * 1000, 2)

            t3 = time.perf_counter()
            synth_result = VectorAnswerSynthesizer.synthesize(
                payload.question,
                vector_result,
                filters=payload.filters,
            )
            latencies["synthesis_ms"] = round((time.perf_counter() - t3) * 1000, 2)

            total_elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            latencies["total_ms"] = total_elapsed_ms

            logger.info(
                "Ask completed: status='%s' route='VectorRoute' total_ms=%s matches=%d",
                synth_result.status,
                total_elapsed_ms,
                vector_result.match_count,
                extra={"request_id": req_id, "endpoint": "/api/v1/ask", "route": "VectorRoute"},
            )

            debug_data = None
            if payload.developer_mode:
                debug_data = DebugInfo(
                    sql_executed=vector_result.sql_executed,
                    route_reasoning=decision.reasoning,
                    latency_breakdown_ms=latencies,
                )

            return AskResponse(
                request_id=req_id,
                status=synth_result.status,  # type: ignore[arg-type]
                route="VectorRoute",
                answer=synth_result.answer,
                evidence_objects=synth_result.evidence_objects,
                sources=synth_result.sources,
                candidates=None,
                filters_ignored=vector_result.filters_ignored,
                answered_via_fallback=decision.answered_via_fallback,
                unverified_citations=synth_result.unverified_citations,
                debug=debug_data,
            )

        # 5. Other routes (GraphRoute, HybridRoute)
        # Honest not_found under the zero-evidence invariant until their
        # retrievers land (Fase 6/7). Never ok with empty evidence.
        total_elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        latencies["total_ms"] = total_elapsed_ms
        logger.info(
            "Ask completed: status='not_found' route='%s' total_ms=%s (route pending)",
            decision.route,
            total_elapsed_ms,
            extra={"request_id": req_id, "endpoint": "/api/v1/ask", "route": decision.route},
        )
        debug_data = None
        if payload.developer_mode:
            debug_data = DebugInfo(
                sql_executed=None,
                route_reasoning=decision.reasoning,
                latency_breakdown_ms=latencies,
            )

        route_labels = {
            "GraphRoute": "penelusuran jaringan kolaborasi (Fase 6)",
            "HybridRoute": "analisis tren topik dan kepakaran (Fase 7)",
        }
        detail = route_labels.get(decision.route, decision.route)

        return AskResponse(
            request_id=req_id,
            status="not_found",
            route=decision.route,
            answer=(
                f"Kueri Anda terklasifikasi ke {decision.route} ({detail}), "
                "yang belum tersedia pada irisan vertikal saat ini. "
                "Data tidak ditemukan dalam database untuk rute tersebut."
            ),
            evidence_objects=[],
            sources=[],
            candidates=None,
            filters_ignored=[],
            answered_via_fallback=decision.answered_via_fallback,
            unverified_citations=[],
            debug=debug_data,
        )
