"""Unit tests for VectorRetriever and VectorAnswerSynthesizer.

Docs Reference: docs/05 Retrieval Rag Design.md §5.2, docs/10 Implementation Plan.md §1 (Task 6).
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.app.core.config import get_settings
from backend.app.models.ask import FilterParams
from backend.app.services.retrievers.vector_retriever import (
    COSINE_SIMILARITY_THRESHOLD,
    VectorMatchItem,
    VectorRetrievalResult,
    VectorRetriever,
)
from backend.app.services.synthesizer.answer import VectorAnswerSynthesizer


@pytest.mark.asyncio
async def test_vector_retriever_successful_retrieve():
    """Verify VectorRetriever builds CTE SQL and maps returned database rows."""
    fake_rows = [
        {
            "publication_id": "PUB000005",
            "title": "Indo-Wdsimplequad2.0 Benchmark",
            "year": 2024,
            "doi": "10.1016/j.kgqa.2024",
            "eid": "2-s2.0-85000000",
            "citation_count": 12,
            "chunk_id": "PUB000005_CH002",
            "chunk_text": "Indo-Wdsimplequad2.0 Can Serve As The First Indonesian-Language Kgqa Benchmark",
            "similarity_score": 0.785,
            # gate_stats columns: build_query ends with
            # "gate_stats LEFT JOIN LATERAL (scored_chunks)", so every row
            # carries the ANN stats alongside the match columns.
            "top_similarity": 0.785,
            "candidate_rows": 12,
        }
    ]

    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = fake_rows

    dummy_vector = [0.01] * 1024
    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="Indonesian KGQA benchmark dataset",
        threshold=0.65,
        limit=8,
        query_vector=dummy_vector,
    )

    assert result.is_empty is False
    assert result.match_count == 1
    assert result.threshold == 0.65
    assert len(result.matches) == 1

    m = result.matches[0]
    assert m.publication_id == "PUB000005"
    assert m.title == "Indo-Wdsimplequad2.0 Benchmark"
    assert m.year == 2024
    assert m.doi == "10.1016/j.kgqa.2024"
    assert m.similarity_score == 0.785
    assert "scored_chunks" in result.sql_executed


@pytest.mark.asyncio
async def test_vector_retriever_zero_matches_empty_result():
    """Verify VectorRetriever returns empty result when no chunks pass threshold."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    dummy_vector = [0.0] * 1024
    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="Unrelated query",
        threshold=0.65,
        limit=8,
        query_vector=dummy_vector,
    )

    assert result.is_empty is True
    assert result.match_count == 0
    assert len(result.matches) == 0


@pytest.mark.asyncio
async def test_vector_retriever_with_structured_filters():
    """Verify filters (year, country, resolved_author_id) are bound to SQL query."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    filters = FilterParams(
        year=2023,
        country="indonesia",
        document_type="article",
        topic_name="nlp",  # Ignored filter
    )

    dummy_vector = [0.01] * 1024
    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="NLP research in Indonesia",
        filters=filters,
        resolved_author_id="AUTH_001",
        resolved_institution_id="INST_001",
        query_vector=dummy_vector,
    )

    assert "topic_name" in result.filters_ignored

    # Check that SQL includes filter clauses
    assert mock_conn.fetch.called
    call_args = mock_conn.fetch.call_args[0]
    sql_text = call_args[0]
    params = call_args[1:]

    assert "p.year = $" in sql_text
    assert "p.document_type ILIKE $" in sql_text
    assert "pub_author" in sql_text
    assert "pub_institution" in sql_text
    assert 2023 in params
    assert "AUTH_001" in params
    assert "INST_001" in params


def test_vector_answer_synthesizer_zero_match():
    """Zero matches synthesize to status=not_found with empty evidence objects."""
    empty_result = VectorRetrievalResult(
        matches=[],
        threshold=0.65,
        filters_ignored=[],
        sql_executed="SELECT 1",
    )
    synth = VectorAnswerSynthesizer.synthesize("test question", empty_result)

    assert synth.status == "not_found"
    assert synth.evidence_objects == []
    assert synth.sources == []
    assert "Data tidak ditemukan" in synth.answer


def test_vector_answer_synthesizer_successful_synthesis():
    """Matches synthesize to status=ok, EvidenceObjects, SourceItems, and verified citations."""
    matches = [
        VectorMatchItem(
            publication_id="PUB000005",
            title="Indo-Wdsimplequad2.0 Benchmark",
            year=2024,
            doi="10.1016/j.kgqa.2024",
            eid="2-s2.0-85000000",
            citation_count=12,
            chunk_id="PUB000005_CH002",
            chunk_text="Indo-Wdsimplequad2.0 is a benchmark dataset for QA.",
            similarity_score=0.785,
        )
    ]
    res = VectorRetrievalResult(
        matches=matches,
        threshold=0.65,
        filters_ignored=[],
        sql_executed="SELECT 1",
    )

    synth = VectorAnswerSynthesizer.synthesize("Indonesian QA dataset", res)

    assert synth.status == "ok"
    assert len(synth.sources) == 1
    assert synth.sources[0].source_type == "vector"
    assert synth.sources[0].relevance_score == 0.785

    assert len(synth.evidence_objects) == 1
    ev = synth.evidence_objects[0]
    assert ev.metric == "similarity_score"
    assert ev.value == 0.785
    assert ev.confidence == 0.785
    assert len(ev.sources) == 1
    assert ev.sources[0].publication_id == "PUB000005"
    assert ev.sources[0].doi == "10.1016/j.kgqa.2024"

    assert "[Indo-Wdsimplequad2.0 Benchmark, 2024, 10.1016/j.kgqa.2024]" in synth.answer
    assert len(synth.unverified_citations) == 0


@pytest.mark.asyncio
async def test_vector_retriever_rejects_non_finite_precomputed_vector():
    """Precomputed NaN/Inf vectors must raise before any SQL is executed."""
    import math

    from backend.app.services.embedding import EmbeddingError

    mock_conn = AsyncMock()
    bad_vector = [0.01] * 1023 + [math.nan]

    with pytest.raises(EmbeddingError, match="non-finite"):
        await VectorRetriever.retrieve(
            conn=mock_conn,
            question="test",
            query_vector=bad_vector,
        )
    assert not mock_conn.fetch.called


@pytest.mark.asyncio
async def test_vector_retriever_threshold_and_dedup_sql_shape():
    """Placeholder order is $1 vector / $2 ANN overfetch / $3 threshold /
    $4 limit, and dedup uses DISTINCT ON before the final LIMIT."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    dummy_vector = [0.01] * 1024
    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="test threshold",
        threshold=0.65,
        limit=8,
        query_vector=dummy_vector,
    )

    assert result.is_empty is True
    call_args = mock_conn.fetch.call_args[0]
    sql_text = call_args[0]
    params = call_args[1:]

    assert "DISTINCT ON (ac.publication_id)" in sql_text
    assert "ORDER BY similarity_score DESC" in sql_text
    # The $4 LIMIT is applied INSIDE the LATERAL subquery (the
    # gate_stats LEFT JOIN LATERAL keeps the stats row reachable on a total
    # gate miss), so the statement itself ends with the outer sort.
    assert "LIMIT $4" in sql_text
    assert sql_text.strip().endswith("ORDER BY sc.similarity_score DESC;")
    assert params[2] == 0.65
    assert params[3] == 8


@pytest.mark.asyncio
async def test_vector_retriever_binds_embedding_instead_of_inlining_it():
    """The 1024-d embedding must travel as $1, never be interpolated into the
    SQL text: the operator is referenced twice, so inlining would add ~22KB of
    query text per request and put the vector into debug output."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="bound vector",
        query_vector=[0.01] * 1024,
    )

    assert result.embedding_backend is None
    sql_text = mock_conn.fetch.call_args[0][0]
    params = mock_conn.fetch.call_args[0][1:]
    assert "0.01000000" not in sql_text
    assert sql_text.count("OPERATOR(") == 2
    assert params[0].startswith("[") and params[0].endswith("]")
    assert params[0].count(",") == 1023


@pytest.mark.asyncio
async def test_vector_retriever_ann_cte_leads_with_distance_ordering():
    """HNSW usability invariant: the ANN scan's ORDER BY must be led by the
    distance operator. A `publication_id`-led sort (the pre-fix shape) cannot be
    served by the index and degrades to a sequential scan plus top-N sort."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    await VectorRetriever.retrieve(
        conn=mock_conn,
        question="ann ordering",
        query_vector=[0.01] * 1024,
    )

    sql_text = mock_conn.fetch.call_args[0][0]
    ann_block = sql_text.split("scored_chunks")[0]
    assert "WITH ann_candidates AS (" in ann_block
    # Distance is the leading sort key of the index-driven scan.
    assert "ORDER BY (c.embedding OPERATOR(extensions.<=>) $1::extensions.vector) ASC" in ann_block
    # Deduplication happens after the ANN window, not inside it.
    assert "publication_id," in ann_block
    assert "DISTINCT ON" not in ann_block


@pytest.mark.asyncio
async def test_vector_retriever_overfetches_ann_candidates():
    """Dedup collapses duplicate chunks per publication, so the ANN window must
    overfetch far beyond the requested publication count."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    await VectorRetriever.retrieve(
        conn=mock_conn,
        question="overfetch",
        limit=8,
        query_vector=[0.01] * 1024,
    )

    params = mock_conn.fetch.call_args[0][1:]
    assert params[1] == 200  # 8 * 25


@pytest.mark.asyncio
async def test_vector_retriever_overfetch_is_clamped():
    """The ANN window is bounded on both ends: a floor for tiny limits and a
    ceiling so a large `limit` cannot request the whole table."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    await VectorRetriever.retrieve(
        conn=mock_conn,
        question="overfetch floor",
        limit=1,
        query_vector=[0.01] * 1024,
    )
    assert mock_conn.fetch.call_args[0][1:][1] == 100

    await VectorRetriever.retrieve(
        conn=mock_conn,
        question="overfetch ceiling",
        limit=50,
        query_vector=[0.01] * 1024,
    )
    assert mock_conn.fetch.call_args[0][1:][1] == 1250


@pytest.mark.asyncio
async def test_vector_retriever_does_not_set_hnsw_ef_search_per_request():
    """No per-request ``set_config`` round trip (W5).

    The retriever used to issue ``SELECT set_config('hnsw.ef_search', ...)``
    before every ANN query: 29-54 ms of measured round trips per VectorRoute
    request, with no effect on any observed plan (at 40 chunks the planner picks
    ``Seq Scan + Sort``). It also leaked across requests, because the pool
    released connections with ``reset="light"`` which does not clear session
    state.

    ``hnsw.ef_search`` is now set once per connection in
    ``db.pool.create_pool`` via ``server_settings``, so the retriever must issue
    exactly one statement — the ANN fetch itself.
    """
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="ef search best effort",
        query_vector=[0.01] * 1024,
    )

    assert mock_conn.execute.await_count == 0, (
        "retriever must not issue per-request set_config; it is a per-connection "
        "server_setting now"
    )
    assert mock_conn.fetch.await_count == 1
    assert result.is_empty is True


def test_pool_sets_hnsw_ef_search_as_a_connection_setting():
    """The GUC must be applied per connection, not per query."""
    import inspect

    from backend.app.db import pool as pool_mod

    src = inspect.getsource(pool_mod.create_pool)
    assert '"hnsw.ef_search"' in src
    assert "settings.hnsw_ef_search" in src

    # The release path must clear session state. asyncpg's create_pool takes
    # `reset` as a CALLABLE (unlike connect(), which takes "light"/"full"), and
    # passing the string raises TypeError from Pool.release -- which presents as
    # every integration test silently losing its database.
    assert "reset=_reset_connection" in src
    reset_src = inspect.getsource(pool_mod._reset_connection)
    assert "conn.reset()" in reset_src


def test_vector_answer_synthesizer_zero_match_under_200ms():
    """Zero-evidence short-circuit must be deterministic and fast (<200ms)."""
    import time

    empty_result = VectorRetrievalResult(
        matches=[],
        threshold=0.65,
        filters_ignored=[],
        sql_executed="SELECT 1",
    )
    t0 = time.perf_counter()
    synth = VectorAnswerSynthesizer.synthesize("test question", empty_result)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert synth.status == "not_found"
    assert elapsed_ms < 200.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("offset", "expect_hit"),
    [(-0.001, False), (0.0, True), (0.001, True)],
    ids=["just_below_gate_is_rejected", "exactly_on_gate_passes", "just_above_passes"],
)
async def test_vector_retriever_threshold_boundary_is_inclusive(offset, expect_hit):
    """Scores at gate-eps / gate / gate+eps behave per the FR4.5 inclusive gate.

    The gate is enforced by PostgreSQL (``WHERE (1 - distance) >= $3``), not in
    Python, so the mock connection reproduces the server's verdict: it keeps a
    row only when its score clears the gate that was actually bound, and returns
    the all-NULL ``gate_stats`` row otherwise. That exercises the wiring the
    retriever owns -- the threshold reaches the server as ``$3``, a boundary row
    survives, and a sub-gate row becomes an honest miss rather than evidence.

    Parametrised against the deployed gate instead of a hardcoded literal so
    re-calibrating the threshold cannot leave this asserting a boundary that no
    longer exists.
    """
    gate = COSINE_SIMILARITY_THRESHOLD
    score = round(gate + offset, 6)

    mock_conn = AsyncMock()

    async def fake_fetch(_sql, *params):
        bound_gate = params[2]
        if score >= bound_gate:
            return [
                {
                    "publication_id": "PUB000001",
                    "title": "Boundary Paper",
                    "year": 2023,
                    "doi": None,
                    "eid": "2-s2.0-85000001",
                    "citation_count": 0,
                    "chunk_id": "PUB000001_CH001",
                    "chunk_text": "Boundary abstract text.",
                    "similarity_score": score,
                    "top_similarity": score,
                    "candidate_rows": 5,
                }
            ]
        # Total gate miss: one all-NULL row carrying only the stats.
        return [
            {
                "publication_id": None, "title": None, "year": None,
                "doi": None, "eid": None, "citation_count": None,
                "chunk_id": None, "chunk_text": None,
                "similarity_score": None,
                "top_similarity": score, "candidate_rows": 5,
            }
        ]

    mock_conn.fetch.side_effect = fake_fetch

    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="boundary query",
        threshold=gate,
        limit=8,
        query_vector=[0.01] * 1024,
    )

    assert (result.match_count == 1) is expect_hit
    if expect_hit:
        assert result.matches[0].similarity_score == pytest.approx(score)
    else:
        # A sub-gate row must never become evidence, and must be reported as a
        # calibration miss (candidates existed) rather than an empty corpus.
        assert result.is_empty is True
        assert result.diagnostics["candidate_rows"] == 5
        assert result.diagnostics["top_similarity"] == pytest.approx(score)

    # The gate must be inclusive and bound as $3 (DB-enforced, not Python).
    call_args = mock_conn.fetch.call_args[0]
    sql_text = call_args[0]
    params = call_args[1:]
    assert ">= $3" in sql_text
    assert params[2] == pytest.approx(gate)


def _patch_settings(monkeypatch, **overrides):
    """Override deployed Settings inside the retriever module.

    ``Settings`` is ``frozen=True``, so attribute assignment is rejected.
    ``model_copy(update=...)`` is the supported way to derive a variant without
    re-running validation, and patching the module's ``get_settings`` keeps the
    override scoped to the code under test.
    """
    stub = get_settings().model_copy(update=overrides)
    monkeypatch.setattr(
        "backend.app.services.retrievers.vector_retriever.get_settings",
        lambda: stub,
    )
    return stub


def test_resolve_operating_point_defaults_to_deployed_settings(monkeypatch):
    """``None`` must defer to Settings so the gate is retunable per deployment."""
    _patch_settings(monkeypatch, vector_cosine_threshold=0.42, vector_top_k=12)

    threshold, limit = VectorRetriever.resolve_operating_point(None, None)

    assert threshold == pytest.approx(0.42)
    assert limit == 12


def test_resolve_operating_point_honours_explicit_overrides():
    """An explicit, in-range override wins over the deployed value."""
    assert VectorRetriever.resolve_operating_point(0.7, 3) == (pytest.approx(0.7), 3)


@pytest.mark.parametrize(
    ("threshold", "limit", "message"),
    [
        (1.5, 8, "threshold must be within"),
        (-0.1, 8, "threshold must be within"),
        (0.48, 0, "limit must be within"),
        (0.48, 51, "limit must be within"),
    ],
)
def test_resolve_operating_point_rejects_out_of_range(threshold, limit, message):
    """Out-of-range overrides fail fast instead of shifting the gate silently."""
    with pytest.raises(ValueError, match=message):
        VectorRetriever.resolve_operating_point(threshold, limit)


@pytest.mark.asyncio
async def test_vector_retriever_top_k_is_configurable(monkeypatch):
    """Top-K comes from Settings, and the ANN window follows the requested size."""
    _patch_settings(monkeypatch, vector_top_k=5)

    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="top k query",
        query_vector=[0.01] * 1024,
    )

    assert result.diagnostics["top_k"] == 5
    # $2 is the ANN window, $4 the result limit; both must track top_k.
    params = mock_conn.fetch.call_args[0][1:]
    assert params[3] == 5
    assert params[1] == VectorRetriever.ann_window(5)


@pytest.mark.asyncio
async def test_vector_retriever_keyword_filter_ignored_honestly():
    """VectorRoute has no keyword predicate in the Phase 4 slice: `keyword`
    must surface in `filters_ignored`, never silently dropped or interpolated."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    filters = FilterParams(keyword="stem cell")
    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="stem cell papers",
        filters=filters,
        query_vector=[0.01] * 1024,
    )

    assert "keyword" in result.filters_ignored
    sql_text = mock_conn.fetch.call_args[0][0]
    assert "keyword" not in sql_text.lower()


def test_vector_schema_default_is_extensions():
    """Default pgvector schema qualifier preserves the Supabase layout (audit M1)."""
    from backend.app.core.config import Settings

    assert Settings().vector_schema == "extensions"


def test_vector_schema_rejects_non_identifier():
    """VECTOR_SCHEMA must be a plain SQL identifier — injection fails fast (audit M1)."""
    from pydantic import ValidationError

    from backend.app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(vector_schema="extensions; DROP TABLE chunks;--")


@pytest.mark.asyncio
async def test_vector_retriever_sql_uses_configured_vector_schema(monkeypatch):
    """Generated SQL must qualify the pgvector operator with the configured schema."""
    from backend.app.core.config import Settings

    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []
    monkeypatch.setattr(
        "backend.app.services.retrievers.vector_retriever.get_settings",
        lambda: Settings(vector_schema="public"),
    )

    await VectorRetriever.retrieve(
        conn=mock_conn,
        question="schema override query",
        query_vector=[0.01] * 1024,
    )

    sql_text = mock_conn.fetch.call_args[0][0]
    assert "OPERATOR(public.<=>)" in sql_text
    assert "::public.vector" in sql_text
    assert "extensions." not in sql_text


@pytest.mark.asyncio
async def test_vector_retriever_escapes_like_wildcards():
    """ILIKE filters escape %, _ and backslash with ESCAPE '\\' (audit D4)."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    filters = FilterParams(document_type="100%_article\\x", country="indonesia_100%")

    await VectorRetriever.retrieve(
        conn=mock_conn,
        question="wildcard filter query",
        filters=filters,
        query_vector=[0.01] * 1024,
    )

    sql_text = mock_conn.fetch.call_args[0][0]
    params = mock_conn.fetch.call_args[0][1:]
    assert sql_text.count("ESCAPE '\\'") == 2
    assert "%100\\%\\_article\\\\x%" in params
    assert "%indonesia\\_100\\%%" in params


@pytest.mark.asyncio
async def test_vector_retriever_rejects_out_of_range_threshold_and_limit():
    """Out-of-range threshold/limit overrides fail fast before any SQL runs."""
    mock_conn = AsyncMock()

    with pytest.raises(ValueError, match="threshold must be within"):
        await VectorRetriever.retrieve(
            conn=mock_conn,
            question="bad threshold",
            threshold=1.5,
            query_vector=[0.01] * 1024,
        )

    with pytest.raises(ValueError, match="limit must be within"):
        await VectorRetriever.retrieve(
            conn=mock_conn,
            question="bad limit",
            limit=0,
            query_vector=[0.01] * 1024,
        )

    assert not mock_conn.fetch.called


@pytest.mark.asyncio
async def test_vector_retriever_precomputed_vector_has_no_embedding_provenance():
    """Injected vectors skip generation: embedding_ms/backend stay None."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="precomputed provenance",
        query_vector=[0.01] * 1024,
    )

    assert result.embedding_ms is None
    assert result.embedding_backend is None


@pytest.mark.asyncio
async def test_vector_retriever_generated_embedding_reports_provenance(monkeypatch):
    """Generated path records embedding_ms and the serving backend (audit D1)."""
    from unittest.mock import AsyncMock as AM

    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []
    fake_gen = AM(return_value=([0.01] * 1024, "ollama"))
    monkeypatch.setattr(
        "backend.app.services.retrievers.vector_retriever.generate_query_embedding_with_backend",
        fake_gen,
    )

    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="generated provenance",
    )

    assert result.embedding_backend == "ollama"
    assert result.embedding_ms is not None
    assert result.embedding_ms >= 0.0


def test_debug_info_accepts_embedding_backend():
    """DebugInfo carries the optional embedding_backend without breaking old constructions."""
    from backend.app.models.ask import DebugInfo

    legacy = DebugInfo(latency_breakdown_ms={"total_ms": 1.0})
    assert legacy.embedding_backend is None

    enriched = DebugInfo(
        latency_breakdown_ms={"total_ms": 1.0}, embedding_backend="local"
    )
    assert enriched.embedding_backend == "local"


def _gate_miss_row(candidate_rows: int, top_similarity: float | None) -> dict:
    """The single all-NULL row a total gate miss actually returns.

    ``build_query`` ends with ``gate_stats LEFT JOIN LATERAL (scored_chunks)``,
    so when nothing clears the cosine gate there is no result set at all — there
    is ONE stats row whose publication columns are all NULL. Stubbing
    ``fetch`` with ``[]`` (as the older tests here do) cannot reproduce that
    shape, which is why the NULL-row crash went unnoticed.
    """
    return {
        "publication_id": None,
        "eid": None,
        "doi": None,
        "title": None,
        "year": None,
        "citation_count": None,
        "chunk_id": None,
        "chunk_text": None,
        "similarity_score": None,
        "top_similarity": top_similarity,
        "candidate_rows": candidate_rows,
    }


@pytest.mark.asyncio
async def test_vector_retriever_gate_miss_row_is_not_evidence():
    """A total gate miss returns one NULL stats row; it must not become evidence.

    Regression: the match list comprehension iterated ``rows`` instead of the
    ``gated_rows`` filter computed immediately above it, so the NULL stats row
    reached ``float(r["similarity_score"])`` and raised
    ``TypeError: float() argument must be ... not 'NoneType'``. Every
    VectorRoute query whose cosine gate rejected everything therefore answered
    HTTP 500 instead of a deterministic ``not_found``.
    """
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [_gate_miss_row(40, 0.6314)]

    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="Papers on oxidative stress in Wharton's jelly mesenchymal stem cells?",
        query_vector=[0.01] * 1024,
    )

    assert result.is_empty is True
    assert result.match_count == 0
    assert len(result.matches) == 0
    # Stats from the NULL row survive so the caller can tell a gate miss from
    # an empty corpus.
    assert result.diagnostics["candidate_rows"] == 40
    assert result.diagnostics["rows_after_threshold"] == 0
    assert result.diagnostics["unique_publications"] == 0
    assert result.diagnostics["top_similarity"] == pytest.approx(0.6314)


@pytest.mark.asyncio
async def test_vector_retriever_gate_miss_with_zero_candidates_reports_no_top_similarity():
    """Empty ANN window: NULL row carries candidate_rows=0 and no score."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [_gate_miss_row(0, None)]

    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="anything",
        query_vector=[0.01] * 1024,
    )

    assert result.is_empty is True
    assert result.diagnostics["candidate_rows"] == 0
    assert result.diagnostics["top_similarity"] is None


@pytest.mark.asyncio
async def test_vector_retriever_gate_miss_keeps_real_rows_alongside_stats_row():
    """The NULL stats row must be dropped without discarding genuine matches."""
    good = {
        "publication_id": "PUB_1",
        "eid": "2-s2.0-1",
        "doi": "10.1/x",
        "title": "Real hit",
        "year": 2025,
        "citation_count": 3,
        "chunk_id": 7,
        "chunk_text": "text",
        "similarity_score": 0.81,
        "top_similarity": 0.81,
        "candidate_rows": 40,
    }
    mock_conn = AsyncMock()
    # LEFT JOIN LATERAL puts the stats row LAST when matches exist.
    mock_conn.fetch.return_value = [good, _gate_miss_row(40, 0.81)]

    result = await VectorRetriever.retrieve(
        conn=mock_conn,
        question="anything",
        query_vector=[0.01] * 1024,
    )

    assert result.match_count == 1
    assert result.matches[0].publication_id == "PUB_1"
    assert result.matches[0].similarity_score == pytest.approx(0.81)
    assert result.diagnostics["unique_publications"] == 1
