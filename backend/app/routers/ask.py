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
    FilterParams,
)
from backend.app.models.session import ConversationContext
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
from backend.app.services.session_service import (
    SessionService,
    render_conversation_block,
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
    conversation_block: str | None = None,
) -> tuple[str, list[str], str]:
    """Opt-in LLM narrative synthesis (Fase 7 B1) with deterministic fallback.

    Returns ``(answer, unverified_citations, synthesis_backend)`` where the
    backend is one of ``"deterministic"`` (flag off), ``"llm"`` (Ollama
    synthesis verified), or ``"deterministic-fallback"`` (LLM attempted but
    unusable). Only ever called with a non-empty ``EvidenceSet`` — the
    zero-evidence short-circuits above guarantee 0 LLM calls for not_found
    (AC-RAG-4).

    ``conversation_block`` is untrusted conversational recall, threaded
    explicitly rather than through a ContextVar so the prompt's provenance is
    greppable at every hop. It can only reach this narration step: routing,
    retrieval and citation verification all completed before it exists, so no
    value from the transcript can influence them.
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
        conversation_block=conversation_block,
    )
    latencies["llm_synthesis_ms"] = round((time.perf_counter() - t_llm) * 1000, 2)
    return refined.answer, refined.unverified_citations, refined.synthesis_backend


def _stamp_total(latencies: dict[str, float], start_time: float) -> float:
    """Record total_ms latency and return it.

    Called at every pipeline stage boundary so ``latency_breakdown_ms`` in the
    debug payload reflects work completed so far even on an early return. Each
    call recomputes from ``start_time``, so the extra invocations cost a
    ``perf_counter`` pair and are harmless.
    """
    total_elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
    latencies["total_ms"] = total_elapsed_ms
    return total_elapsed_ms


#: Transcript content stored for a turn whose pipeline raised. A fixed string, and
#: deliberately NOT ``str(exc)``: ``GET /api/v1/sessions/{id}`` returns stored
#: content verbatim, so an exception message would surface SQL, connection and
#: host detail to whoever reads the conversation back. ``request_id`` on the same
#: row is the correlation key into the logs, where the exception already is.
FAILED_TURN_ANSWER = "Jawaban tidak dapat dihasilkan karena kesalahan internal."


def _vector_zero_evidence_class(vector_result: Any) -> str:
    """Classify why VectorRoute produced no evidence.

    Distinguishes the two states that look identical in the user-facing answer
    but demand opposite responses:

    - ``no_candidates`` — the ANN window returned nothing at all (empty
      ``chunks`` table, or every chunk filtered out). The corpus cannot answer.
    - ``below_vector_threshold`` — candidates existed and were scored, but the
      cosine gate rejected them. Either the topic genuinely is absent, or the
      gate constant sits above this embedding model's score range; the
      reported ``top_similarity`` versus ``threshold`` is what tells them
      apart, so the class is only meaningful alongside ``vector_diagnostics``.
    """
    diagnostics = getattr(vector_result, "diagnostics", None) or {}
    if diagnostics.get("candidate_rows"):
        return "below_vector_threshold"
    return "no_candidates"


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
    sql_source: str | None = None,
    vector_diagnostics: dict[str, Any] | None = None,
    zero_evidence_class: str | None = None,
) -> DebugInfo | None:
    """Build DebugInfo when developer_mode is on, else None."""
    if not payload.developer_mode:
        return None
    return DebugInfo(
        sql_executed=sql_executed,
        route_reasoning=decision.reasoning,
        latency_breakdown_ms=latencies,
        entities=dict(decision.extracted_entities),
        synthesis_backend=synthesis_backend,  # type: ignore[arg-type]
        embedding_backend=embedding_backend,  # type: ignore[arg-type]
        scored_chunks=scored_chunks,  # type: ignore[arg-type]
        evidence_set=evidence_set,  # type: ignore[arg-type]
        sql_source=sql_source,  # type: ignore[arg-type]
        vector_diagnostics=vector_diagnostics,
        zero_evidence_class=zero_evidence_class,
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
    conversation_block: str | None = None,
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
    # Split the opaque retrieval number into the three stages an operator
    # actually acts on. `sql_generation_ms` is the one that moves: a template
    # regression shows up there as LLM Text-to-SQL cost, not as "slow SQL".
    latencies["sql_generation_ms"] = sql_result.generation_ms
    latencies["sql_validation_ms"] = sql_result.validation_ms
    latencies["db_query_ms"] = sql_result.db_query_ms

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
                sql_source=sql_result.sql_source,
                zero_evidence_class="empty_result_set",
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
        conversation_block=conversation_block,
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
            sql_source=sql_result.sql_source,
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
    conversation_block: str | None = None,
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
    # The ANN query itself, separated from the embedding that precedes it:
    # measured 25-98 ms for the query against 154 ms warm and 29.9-185.8 s
    # cold for the embedding, so merging them hides which one regressed.
    vector_query_ms = vector_result.diagnostics.get("vector_query_ms")
    if vector_query_ms is not None:
        latencies["vector_query_ms"] = vector_query_ms

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
                vector_diagnostics=dict(vector_result.diagnostics),
                zero_evidence_class=_vector_zero_evidence_class(vector_result),
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
        conversation_block=conversation_block,
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
            vector_diagnostics=dict(vector_result.diagnostics),
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
    conversation_block: str | None = None,
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
                zero_evidence_class="empty_result_set",
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
        conversation_block=conversation_block,
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
    conversation_block: str | None = None,
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
                zero_evidence_class="no_candidates",
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
                zero_evidence_class="empty_result_set",
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
        conversation_block=conversation_block,
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
    """Handle research query request via multi-route retrieval and grounded synthesis.

    Session integration (docs/03 §0.3 invariant 5 — Session Isolation Invariant)
    ==========================================================================
    When ``payload.session_id`` is absent, this function is exactly the
    pre-session code path: one extra branch is evaluated and the pipeline runs
    unchanged. When it is present:

      1. the session is validated BEFORE ``QuestionRouter.classify_route`` and
         before ``get_pool()``, so a bad id costs one indexed lookup on schema
         ``app`` and never reaches the bibliometric corpus (404, Test 11);
      2. conversation context fills only filters the current question left
         unspecified. ``payload.question`` is NEVER rewritten, so routing,
         Text-to-SQL generation, ``EvidenceSet.query`` and citation verification
         all continue to see the text the user typed;
      3. the user turn is committed BEFORE retrieval, so a failed request still
         leaves the question on the record;
      4. the assistant turn and the session counters are committed AFTER
         synthesis;
      5. the summary refresh is best effort and cannot fail this request.

    Conversation context never supplies a value. Every bibliometric metric in
    the response still comes from a retrieval against `public`, and a stale
    figure quoted by an earlier assistant turn is re-queried, not trusted
    (Test 8).
    """
    start_time = time.perf_counter()
    req_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    latencies: dict[str, float] = {}

    logger.info(
        "Processing ask request: question='%s' filters=%s developer_mode=%s session=%s",
        payload.question,
        payload.filters.model_dump() if payload.filters else {},
        payload.developer_mode,
        payload.session_id,
        extra={"request_id": req_id, "endpoint": "/api/v1/ask"},
    )

    # ---- Session pre-flight (before ANY bibliometric work) --------------
    session_service: SessionService | None = None
    conversation_block: str | None = None
    session_filters_applied: list[str] = []
    effective_payload: AskRequest = payload
    effective_filters_obj = payload.filters or FilterParams()

    if payload.session_id is not None:
        t_ctx = time.perf_counter()
        session_service = await SessionService.create()  # 503 when unconfigured
        await session_service.get_session(payload.session_id)  # 404 when unknown
        context: ConversationContext = await session_service.load_context(
            payload.session_id
        )
        session_io_ms = round((time.perf_counter() - t_ctx) * 1000, 2)

        if payload.use_session_context:
            merged_filters, session_filters_applied = session_service.effective_filters(
                effective_filters_obj, context.scope
            )
            effective_payload = payload.model_copy(update={"filters": merged_filters})
            effective_filters_obj = merged_filters
        # Narration is opt-in like filter inheritance, and for the same reason:
        # a caller that sets use_session_context=False is asserting "treat this
        # question standalone", and injecting the prior transcript into the LLM
        # prompt contradicts that. This block was previously rendered
        # unconditionally, so the flag only gated filters while the transcript
        # still reached the model.
        if payload.use_session_context:
            conversation_block = render_conversation_block(context) or None

        logger.info(
            "Session context attached: session=%s messages=%d "
            "scope_keys=%d inherited=%s",
            payload.session_id,
            len(context.recent_messages),
            0 if context.scope.is_empty else len(context.scope.as_filter_kwargs()),
            session_filters_applied or "-",
            extra={"request_id": req_id, "endpoint": "/api/v1/ask"},
        )

        # Commit the user turn before retrieval. Deliberately ahead of the
        # pipeline: an LLM call runs for seconds, so a single wrapping
        # transaction would pin a connection for its whole duration and roll
        # the user's own question away on failure.
        t_user = time.perf_counter()
        await session_service.record_user_message(
            session_id=payload.session_id,
            content=payload.question,
            applied_filters=effective_filters_obj.model_dump(exclude_none=True),
            request_id=req_id,
        )
        await session_service.adopt_title_from_first_question(
            payload.session_id, payload.question
        )
        latencies["session_persist_user_ms"] = round(
            (time.perf_counter() - t_user) * 1000, 2
        )
        latencies["session_context_ms"] = session_io_ms
        _stamp_total(latencies, start_time)

    # ---- Existing pipeline, unmodified ----------------------------------
    # A pipeline failure still has to land on the transcript as status='failed'.
    # Without this, a turn whose answer never generated was indistinguishable from
    # one that never happened, and the 'failed' literal in migration 005 and the
    # CHECK constraint on research_messages.status served a code path that did not
    # exist.
    try:
        response = await _run_ask_pipeline(
            effective_payload, start_time, req_id, latencies, conversation_block
        )
    except Exception:
        if session_service is not None and payload.session_id is not None:
            try:
                # Content is a fixed string, never str(exc): GET /sessions/{id}
                # returns the transcript verbatim, so exception text would leak
                # SQL and connection detail to the user. The request_id on the
                # same row is the correlation key, and the exception is already
                # logged against it.
                await session_service.record_assistant_message(
                    session_id=payload.session_id,
                    content=FAILED_TURN_ANSWER,
                    route=None,
                    request_id=req_id,
                    applied_filters=effective_filters_obj.model_dump(exclude_none=True),
                    status="failed",
                )
            except Exception:  # noqa: BLE001 - deliberate catch-all
                # Never let bookkeeping replace the real error. SessionService
                # swallows its own storage errors today, but relying on that here
                # would make this handler's correctness depend on an
                # implementation detail three layers down, and it would break
                # silently if a future service stopped swallowing.
                logger.warning(
                    "Could not record failed turn for session %s "
                    "(original error propagates)",
                    payload.session_id,
                    exc_info=True,
                    extra={"request_id": req_id, "endpoint": "/api/v1/ask"},
                )
        raise

    # ---- Session post-flight -------------------------------------------
    if session_service is not None and payload.session_id is not None:
        t_assistant = time.perf_counter()
        await session_service.record_assistant_message(
            session_id=payload.session_id,
            content=response.answer,
            route=response.route,
            request_id=req_id,
            applied_filters=effective_filters_obj.model_dump(exclude_none=True),
            status="complete",
        )
        latencies["session_persist_assistant_ms"] = round(
            (time.perf_counter() - t_assistant) * 1000, 2
        )
        # Best effort: a summary failure must not fail a correct answer.
        t_summary = time.perf_counter()
        await session_service.refresh_summary(session_id=payload.session_id)
        latencies["session_summary_ms"] = round(
            (time.perf_counter() - t_summary) * 1000, 2
        )

        _stamp_total(latencies, start_time)
        updates: dict[str, Any] = {"session_id": payload.session_id}
        if response.debug is not None:
            updates["debug"] = response.debug.model_copy(
                update={
                    "session_filters_applied": session_filters_applied,
                    "session_context_used": conversation_block is not None,
                    "latency_breakdown_ms": _with_session_latencies(
                        latencies, response.debug.latency_breakdown_ms
                    ),
                }
            )
        response = response.model_copy(update=updates)

    return response


def _with_session_latencies(
    latencies: dict[str, float], existing: dict[str, float]
) -> dict[str, float]:
    """Merge session I/O timings into the reported latency breakdown.

    Kept separate from the retrieval timings on purpose: the Zero-Hallucination
    invariant's sub-200ms claim is about the retrieval short-circuit, and folding
    three extra session round-trips into ``total_ms`` would make that number no
    longer comparable to the published measurement.
    """
    merged = dict(existing)
    merged.update(latencies)
    return merged


async def _run_ask_pipeline(
    payload: AskRequest,
    start_time: float,
    req_id: str,
    latencies: dict[str, float],
    conversation_block: str | None = None,
) -> AskResponse:
    """The retrieval + evidence + synthesis pipeline. Unchanged by sessions.

    Split out of :func:`ask_question` so the session wrapper can post-process a
    single return value instead of every one of the pipeline's short-circuit
    branches having to remember to echo ``session_id`` and persist a turn. The
    body below is the original implementation, unmodified.

    Takes no ``Request``: nothing here reads it. It was carried in from the
    wrapper purely out of habit, which left a parameter that looked load-bearing
    and invited edits that assumed per-request state was available.
    """
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
                debug=_make_debug(
                    payload=payload,
                    decision=decision,
                    latencies=latencies,
                    zero_evidence_class="entity_not_found",
                ),
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
                debug=_make_debug(
                    payload=payload,
                    decision=decision,
                    latencies=latencies,
                    zero_evidence_class="entity_needs_clarification",
                ),
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
            debug=_make_debug(
                payload=payload,
                decision=decision,
                latencies=latencies,
                zero_evidence_class="unhandled_route",
            ),
        )
