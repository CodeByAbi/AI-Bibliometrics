"""Ask router endpoint (/api/v1/ask).

Docs Reference: docs/06 Api Design.md §5, docs/05 Retrieval Rag Design.md §4 (EvidenceSet).

HTTP contract note (api-design): this endpoint intentionally returns
``200 + status="not_found"`` for zero-evidence queries instead of HTTP 404.
The response envelope is an AI answer contract consumed by the 2-panel UI —
``status`` drives the empty-state rendering, not the HTTP layer. Transport
errors still use 4xx/5xx via the global exception handlers.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request, status

from backend.app.core.logging import logger
from backend.app.db.pool import get_pool
from backend.app.models.ask import (
    AskRequest,
    AskResponse,
    DebugInfo,
)
from backend.app.services.evidence.models import EvidenceSet
from backend.app.services.evidence.unifier import EvidenceUnifier
from backend.app.services.retrievers.graph_retriever import GraphRetriever
from backend.app.services.retrievers.hybrid_retriever import HybridRetriever
from backend.app.services.retrievers.sql_retriever import SqlRetriever
from backend.app.services.retrievers.vector_retriever import VectorRetriever
from backend.app.services.router import (
    EntityResolutionGate,
    EntityResolutionResult,
    QuestionRouter,
    RouteDecision,
)
from backend.app.services.synthesizer.answer import (
    GraphAnswerSynthesizer,
    HybridAnswerSynthesizer,
    SqlAnswerSynthesizer,
    SynthesizedGraphResponse,
    SynthesizedHybridResponse,
    SynthesizedSqlResponse,
    SynthesizedVectorResponse,
    VectorAnswerSynthesizer,
)
from backend.app.services.synthesizer.llm import LlmAnswerSynthesizer

router = APIRouter(tags=["Ask"])

NOT_FOUND_ANSWER = "Data tidak ditemukan dalam database untuk kriteria pencarian tersebut."

SynthResult = (
    SynthesizedSqlResponse
    | SynthesizedVectorResponse
    | SynthesizedGraphResponse
    | SynthesizedHybridResponse
    | None
)


def _debug_evidence_set(
    synth_result: SynthResult,
    ev_set: EvidenceSet,
) -> dict[str, Any] | None:
    """Serialize the normalized EvidenceSet for developer_mode inspection.

    Returns a JSON-safe dict mirroring the canonical EvidenceSet fields so
    multi-source retrieval can be traced back to its deduplicated evidence
    before synthesis (Phase 5 acceptance: no raw-row-to-LLM bypass).
    """
    return {
        "query": ev_set.query,
        "evidence_objects": [ev.model_dump() for ev in ev_set.evidence_objects],
        "sources": [s.model_dump() for s in ev_set.sources],
        "items_count": len(ev_set.items),
        "filters_ignored": list(ev_set.filters_ignored),
        "sql_executed": ev_set.sql_executed,
        "is_empty": ev_set.is_empty,
    }


async def _maybe_llm_refine(
    *,
    question: str,
    ev_set: EvidenceSet,
    route: str,
    synth_answer: str,
    synth_unverified: list[str],
    latencies: dict[str, float],
    llm_opt_in: bool,
) -> tuple[str, list[str], str]:
    """Opt-in LLM narrative synthesis (Fase 7 B1) with deterministic fallback.

    Returns ``(answer, unverified_citations, synthesis_backend)`` where the
    backend is one of ``"deterministic"`` (flag off), ``"llm"`` (Ollama
    synthesis verified), or ``"deterministic-fallback"`` (LLM attempted but
    unusable). Only ever called with a non-empty ``EvidenceSet`` — the
    zero-evidence short-circuits above guarantee 0 LLM calls for not_found
    (AC-RAG-4).
    """
    if not llm_opt_in:
        return synth_answer, synth_unverified, "deterministic"
    t_llm = time.perf_counter()
    refined = await LlmAnswerSynthesizer.refine(
        question,
        ev_set,
        route=route,
        fallback_answer=synth_answer,
        fallback_unverified=synth_unverified,
    )
    latencies["llm_synthesis_ms"] = round((time.perf_counter() - t_llm) * 1000, 2)
    return refined.answer, refined.unverified_citations, refined.synthesis_backend


def _stamp_total(latencies: dict[str, float], start_time: float) -> float:
    """Record total_ms latency and return it."""
    total_elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
    latencies["total_ms"] = total_elapsed_ms
    return total_elapsed_ms


def _make_debug(
    *,
    payload: AskRequest,
    decision: RouteDecision,
    latencies: dict[str, float],
    sql_executed: str | None = None,
    synthesis_backend: str | None = None,
    embedding_backend: str | None = None,
    scored_chunks: list[dict[str, Any]] | None = None,
    evidence_set: dict[str, Any] | None = None,
) -> DebugInfo | None:
    """Build DebugInfo when developer_mode is on, else None."""
    if not payload.developer_mode:
        return None
    return DebugInfo(
        sql_executed=sql_executed,
        route_reasoning=decision.reasoning,
        latency_breakdown_ms=latencies,
        synthesis_backend=synthesis_backend,  # type: ignore[arg-type]
        embedding_backend=embedding_backend,  # type: ignore[arg-type]
        scored_chunks=scored_chunks,  # type: ignore[arg-type]
        evidence_set=evidence_set,  # type: ignore[arg-type]
    )


async def _handle_sql_route(
    *,
    conn: Any,
    payload: AskRequest,
    decision: RouteDecision,
    resolution: EntityResolutionResult,
    req_id: str,
    start_time: float,
    latencies: dict[str, float],
) -> AskResponse:
    t2 = time.perf_counter()
    sql_result = await SqlRetriever.retrieve(
        conn,
        payload.question,
        filters=payload.filters,
        resolved_author_id=resolution.resolved_author_id,
        resolved_institution_id=resolution.resolved_institution_id,
    )
    latencies["sql_retrieval_ms"] = round((time.perf_counter() - t2) * 1000, 2)

    # Phase 5 gate: normalize rows -> canonical EvidenceSet exactly once.
    t_ev = time.perf_counter()
    ev_set = EvidenceUnifier.from_sql(
        payload.question, sql_result, filters=payload.filters
    )
    latencies["evidence_unify_ms"] = round((time.perf_counter() - t_ev) * 1000, 2)

    if ev_set.is_empty:
        total_elapsed_ms = _stamp_total(latencies, start_time)
        return AskResponse(
            request_id=req_id,
            status="not_found",
            route="SQLRoute",
            answer=NOT_FOUND_ANSWER,
            evidence_objects=[],
            sources=[],
            candidates=None,
            filters_ignored=list(ev_set.filters_ignored),
            answered_via_fallback=decision.answered_via_fallback,
            unverified_citations=[],
            debug=_make_debug(
                payload=payload,
                decision=decision,
                latencies=latencies,
                sql_executed=sql_result.sql_executed,
                evidence_set=_debug_evidence_set(None, ev_set),
            ),
        )

    t3 = time.perf_counter()
    synth_result = SqlAnswerSynthesizer.synthesize(
        payload.question,
        sql_result,
        filters=payload.filters,
        evidence_set=ev_set,
    )
    latencies["synthesis_ms"] = round((time.perf_counter() - t3) * 1000, 2)

    final_answer, final_unverified, synthesis_backend = await _maybe_llm_refine(
        question=payload.question,
        ev_set=ev_set,
        route="SQLRoute",
        synth_answer=synth_result.answer,
        synth_unverified=synth_result.unverified_citations,
        latencies=latencies,
        llm_opt_in=bool(payload.llm_synthesis),
    )

    total_elapsed_ms = _stamp_total(latencies, start_time)

    logger.info(
        "Ask completed: status='%s' route='SQLRoute' total_ms=%s sql='%s'",
        synth_result.status,
        total_elapsed_ms,
        sql_result.sql_executed,
        extra={"request_id": req_id, "endpoint": "/api/v1/ask", "route": "SQLRoute"},
    )

    return AskResponse(
        request_id=req_id,
        status=synth_result.status,  # type: ignore[arg-type]
        route="SQLRoute",
        answer=final_answer,
        evidence_objects=synth_result.evidence_objects,
        sources=synth_result.sources,
        candidates=None,
        filters_ignored=sql_result.filters_ignored,
        answered_via_fallback=decision.answered_via_fallback,
        unverified_citations=final_unverified,
        debug=_make_debug(
            payload=payload,
            decision=decision,
            latencies=latencies,
            sql_executed=sql_result.sql_executed,
            synthesis_backend=synthesis_backend,
            evidence_set=_debug_evidence_set(
                synth_result, synth_result.evidence_set or EvidenceSet(query=payload.question)
            ),
        ),
    )


async def _handle_vector_route(
    *,
    conn: Any,
    payload: AskRequest,
    decision: RouteDecision,
    resolution: EntityResolutionResult,
    req_id: str,
    start_time: float,
    latencies: dict[str, float],
) -> AskResponse:
    t2 = time.perf_counter()
    vector_result = await VectorRetriever.retrieve(
        conn,
        payload.question,
        filters=payload.filters,
        resolved_author_id=resolution.resolved_author_id,
        resolved_institution_id=resolution.resolved_institution_id,
    )
    latencies["vector_retrieval_ms"] = round((time.perf_counter() - t2) * 1000, 2)
    if vector_result.embedding_ms is not None:
        latencies["embedding_ms"] = round(vector_result.embedding_ms, 2)

    # Phase 5 gate: normalize chunks -> canonical EvidenceSet exactly once.
    t_ev = time.perf_counter()
    ev_set = EvidenceUnifier.from_vector(
        payload.question, vector_result, filters=payload.filters
    )
    latencies["evidence_unify_ms"] = round((time.perf_counter() - t_ev) * 1000, 2)

    if ev_set.is_empty:
        _stamp_total(latencies, start_time)
        return AskResponse(
            request_id=req_id,
            status="not_found",
            route="VectorRoute",
            answer=NOT_FOUND_ANSWER,
            evidence_objects=[],
            sources=[],
            candidates=None,
            filters_ignored=list(ev_set.filters_ignored),
            answered_via_fallback=decision.answered_via_fallback,
            unverified_citations=[],
            debug=_make_debug(
                payload=payload,
                decision=decision,
                latencies=latencies,
                sql_executed=vector_result.sql_executed,
                embedding_backend=vector_result.embedding_backend,
                evidence_set=_debug_evidence_set(None, ev_set),
            ),
        )

    t3 = time.perf_counter()
    synth_result = VectorAnswerSynthesizer.synthesize(
        payload.question,
        vector_result,
        filters=payload.filters,
        evidence_set=ev_set,
    )
    latencies["synthesis_ms"] = round((time.perf_counter() - t3) * 1000, 2)

    final_answer, final_unverified, synthesis_backend = await _maybe_llm_refine(
        question=payload.question,
        ev_set=ev_set,
        route="VectorRoute",
        synth_answer=synth_result.answer,
        synth_unverified=synth_result.unverified_citations,
        latencies=latencies,
        llm_opt_in=bool(payload.llm_synthesis),
    )

    total_elapsed_ms = _stamp_total(latencies, start_time)

    logger.info(
        "Ask completed: status='%s' route='VectorRoute' total_ms=%s matches=%d",
        synth_result.status,
        total_elapsed_ms,
        vector_result.match_count,
        extra={"request_id": req_id, "endpoint": "/api/v1/ask", "route": "VectorRoute"},
    )

    scored_chunks = (
        [
            {
                "publication_id": m.publication_id,
                "title": m.title,
                "year": m.year,
                "doi": m.doi,
                "chunk_id": m.chunk_id,
                "similarity_score": round(m.similarity_score, 4),
            }
            for m in vector_result.matches
        ]
        if payload.developer_mode
        else None
    )

    return AskResponse(
        request_id=req_id,
        status=synth_result.status,  # type: ignore[arg-type]
        route="VectorRoute",
        answer=final_answer,
        evidence_objects=synth_result.evidence_objects,
        sources=synth_result.sources,
        candidates=None,
        filters_ignored=vector_result.filters_ignored,
        answered_via_fallback=decision.answered_via_fallback,
        unverified_citations=final_unverified,
        debug=_make_debug(
            payload=payload,
            decision=decision,
            latencies=latencies,
            sql_executed=vector_result.sql_executed,
            synthesis_backend=synthesis_backend,
            embedding_backend=vector_result.embedding_backend,
            scored_chunks=scored_chunks,
            evidence_set=_debug_evidence_set(
                synth_result, synth_result.evidence_set or EvidenceSet(query=payload.question)
            ),
        ),
    )


async def _handle_graph_route(
    *,
    conn: Any,
    payload: AskRequest,
    decision: RouteDecision,
    resolution: EntityResolutionResult,
    req_id: str,
    start_time: float,
    latencies: dict[str, float],
) -> AskResponse:
    t2 = time.perf_counter()
    graph_result = await GraphRetriever.retrieve(
        conn,
        payload.question,
        filters=payload.filters,
        resolved_author_id=resolution.resolved_author_id,
        resolved_author_name=resolution.resolved_author_name,
        resolved_institution_id=resolution.resolved_institution_id,
        resolved_institution_name=resolution.resolved_institution_name,
    )
    latencies["graph_retrieval_ms"] = round((time.perf_counter() - t2) * 1000, 2)

    # Phase 5/6 gate: normalize graph edges -> canonical EvidenceSet exactly once.
    t_ev = time.perf_counter()
    ev_set = EvidenceUnifier.from_graph(
        payload.question, graph_result, filters=payload.filters
    )
    latencies["evidence_unify_ms"] = round((time.perf_counter() - t_ev) * 1000, 2)

    if ev_set.is_empty:
        _stamp_total(latencies, start_time)
        return AskResponse(
            request_id=req_id,
            status="not_found",
            route="GraphRoute",
            answer=NOT_FOUND_ANSWER,
            evidence_objects=[],
            sources=[],
            candidates=None,
            filters_ignored=list(ev_set.filters_ignored),
            answered_via_fallback=decision.answered_via_fallback,
            unverified_citations=[],
            debug=_make_debug(
                payload=payload,
                decision=decision,
                latencies=latencies,
                sql_executed=graph_result.sql_executed,
                evidence_set=_debug_evidence_set(None, ev_set),
            ),
        )

    t3 = time.perf_counter()
    synth_result = GraphAnswerSynthesizer.synthesize(
        payload.question,
        graph_result,
        filters=payload.filters,
        evidence_set=ev_set,
    )
    latencies["synthesis_ms"] = round((time.perf_counter() - t3) * 1000, 2)
    _stamp_total(latencies, start_time)

    final_answer, final_unverified, synthesis_backend = await _maybe_llm_refine(
        question=payload.question,
        ev_set=ev_set,
        route="GraphRoute",
        synth_answer=synth_result.answer,
        synth_unverified=synth_result.unverified_citations,
        latencies=latencies,
        llm_opt_in=bool(payload.llm_synthesis),
    )

    return AskResponse(
        request_id=req_id,
        status=synth_result.status,  # type: ignore[arg-type]
        route="GraphRoute",
        answer=final_answer,
        evidence_objects=synth_result.evidence_objects,
        sources=synth_result.sources,
        candidates=None,
        filters_ignored=graph_result.filters_ignored,
        answered_via_fallback=decision.answered_via_fallback,
        unverified_citations=final_unverified,
        debug=_make_debug(
            payload=payload,
            decision=decision,
            latencies=latencies,
            sql_executed=graph_result.sql_executed,
            synthesis_backend=synthesis_backend,
            evidence_set=_debug_evidence_set(synth_result, synth_result.evidence_set or ev_set),
        ),
    )


async def _handle_hybrid_route(
    *,
    conn: Any,
    payload: AskRequest,
    decision: RouteDecision,
    resolution: EntityResolutionResult,
    req_id: str,
    start_time: float,
    latencies: dict[str, float],
) -> AskResponse:
    t2 = time.perf_counter()
    hybrid_result = await HybridRetriever.retrieve(
        conn=conn,
        question=payload.question,
        filters=payload.filters,
        resolved_author_id=resolution.resolved_author_id,
        resolved_author_name=resolution.resolved_author_name,
        resolved_institution_id=resolution.resolved_institution_id,
        resolved_institution_name=resolution.resolved_institution_name,
    )
    latencies["hybrid_retrieval_ms"] = round((time.perf_counter() - t2) * 1000, 2)

    # 1. Zero-evidence short circuit at retriever boundary (<200ms invariant)
    if hybrid_result.is_empty:
        total_elapsed_ms = _stamp_total(latencies, start_time)
        logger.info(
            "Ask completed: status='not_found' route='HybridRoute' total_ms=%s (0 hybrid records)",
            total_elapsed_ms,
            extra={"request_id": req_id, "endpoint": "/api/v1/ask", "route": "HybridRoute"},
        )
        empty_set = EvidenceSet(
            query=payload.question,
            evidence_objects=[],
            sources=[],
            items=[],
            filters_ignored=hybrid_result.filters_ignored,
            sql_executed=hybrid_result.sql_executed,
        )
        return AskResponse(
            request_id=req_id,
            status="not_found",
            route="HybridRoute",
            answer=NOT_FOUND_ANSWER,
            evidence_objects=[],
            sources=[],
            candidates=None,
            filters_ignored=hybrid_result.filters_ignored,
            answered_via_fallback=decision.answered_via_fallback,
            unverified_citations=[],
            debug=_make_debug(
                payload=payload,
                decision=decision,
                latencies=latencies,
                sql_executed=hybrid_result.sql_executed,
                evidence_set=_debug_evidence_set(None, empty_set),
            ),
        )

    # 2. Unify evidence: heterogeneous topic/expert/pub items -> canonical EvidenceSet
    t_unify = time.perf_counter()
    ev_set = EvidenceUnifier.from_hybrid(
        payload.question, hybrid_result, filters=payload.filters
    )
    latencies["evidence_unify_ms"] = round((time.perf_counter() - t_unify) * 1000, 2)

    if ev_set.is_empty:
        total_elapsed_ms = _stamp_total(latencies, start_time)
        logger.info(
            "Ask completed: status='not_found' route='HybridRoute' total_ms=%s (0 normalized evidence objects)",
            total_elapsed_ms,
            extra={"request_id": req_id, "endpoint": "/api/v1/ask", "route": "HybridRoute"},
        )
        return AskResponse(
            request_id=req_id,
            status="not_found",
            route="HybridRoute",
            answer=NOT_FOUND_ANSWER,
            evidence_objects=[],
            sources=[],
            candidates=None,
            filters_ignored=hybrid_result.filters_ignored,
            answered_via_fallback=decision.answered_via_fallback,
            unverified_citations=[],
            debug=_make_debug(
                payload=payload,
                decision=decision,
                latencies=latencies,
                sql_executed=hybrid_result.sql_executed,
                evidence_set=_debug_evidence_set(None, ev_set),
            ),
        )

    # 3. Grounded synthesis + citation verification
    t_synth = time.perf_counter()
    synth_result = HybridAnswerSynthesizer.synthesize(
        payload.question,
        hybrid_result,
        filters=payload.filters,
        evidence_set=ev_set,
    )
    latencies["synthesis_ms"] = round((time.perf_counter() - t_synth) * 1000, 2)

    final_answer, final_unverified, synthesis_backend = await _maybe_llm_refine(
        question=payload.question,
        ev_set=ev_set,
        route="HybridRoute",
        synth_answer=synth_result.answer,
        synth_unverified=synth_result.unverified_citations,
        latencies=latencies,
        llm_opt_in=bool(payload.llm_synthesis),
    )

    total_elapsed_ms = _stamp_total(latencies, start_time)
    logger.info(
        "Ask completed: status='%s' route='HybridRoute' total_ms=%s evidence_count=%d sources_count=%d",
        synth_result.status,
        total_elapsed_ms,
        len(synth_result.evidence_objects),
        len(synth_result.sources),
        extra={"request_id": req_id, "endpoint": "/api/v1/ask", "route": "HybridRoute"},
    )

    return AskResponse(
        request_id=req_id,
        status=synth_result.status,  # type: ignore[arg-type]
        route="HybridRoute",
        answer=final_answer,
        evidence_objects=synth_result.evidence_objects,
        sources=synth_result.sources,
        candidates=None,
        filters_ignored=hybrid_result.filters_ignored,
        answered_via_fallback=decision.answered_via_fallback,
        unverified_citations=final_unverified,
        debug=_make_debug(
            payload=payload,
            decision=decision,
            latencies=latencies,
            sql_executed=hybrid_result.sql_executed,
            synthesis_backend=synthesis_backend,
            evidence_set=_debug_evidence_set(synth_result, synth_result.evidence_set or ev_set),
        ),
    )


RouteHandler = Callable[..., Awaitable[AskResponse]]

ROUTE_HANDLERS: dict[str, RouteHandler] = {
    "SQLRoute": _handle_sql_route,
    "VectorRoute": _handle_vector_route,
    "GraphRoute": _handle_graph_route,
    "HybridRoute": _handle_hybrid_route,
}


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
            total_elapsed_ms = _stamp_total(latencies, start_time)
            logger.info(
                "Ask completed: status='not_found' route='%s' total_ms=%s (entity gate)",
                decision.route,
                total_elapsed_ms,
                extra={"request_id": req_id, "endpoint": "/api/v1/ask", "route": decision.route},
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
                debug=_make_debug(payload=payload, decision=decision, latencies=latencies),
            )

        # Disambiguation short-circuit: If ambiguous candidates found
        if resolution.status == "needs_clarification":
            _stamp_total(latencies, start_time)
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
                debug=_make_debug(payload=payload, decision=decision, latencies=latencies),
            )

        # 3-5. Route dispatch to per-route handlers (retrieval + Evidence gate + synthesis).
        handler = ROUTE_HANDLERS.get(decision.route)
        if handler is not None:
            return await handler(
                conn=conn,
                payload=payload,
                decision=decision,
                resolution=resolution,
                req_id=req_id,
                start_time=start_time,
                latencies=latencies,
            )

        # 6. Fallback for unhandled routes
        _stamp_total(latencies, start_time)
        return AskResponse(
            request_id=req_id,
            status="not_found",
            route=decision.route,
            answer="Data tidak ditemukan dalam database untuk rute tersebut.",
            evidence_objects=[],
            sources=[],
            candidates=None,
            filters_ignored=[],
            answered_via_fallback=decision.answered_via_fallback,
            unverified_citations=[],
            debug=_make_debug(payload=payload, decision=decision, latencies=latencies),
        )
