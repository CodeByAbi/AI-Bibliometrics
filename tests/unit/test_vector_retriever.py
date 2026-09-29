"""Unit tests for VectorRetriever and VectorAnswerSynthesizer.

Docs Reference: docs/05 Retrieval Rag Design.md §5.2, docs/10 Implementation Plan.md §1 (Task 6).
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.app.models.ask import FilterParams
from backend.app.services.retrievers.vector_retriever import (
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
    """Threshold is bound as $1 and dedup uses DISTINCT ON before LIMIT 8."""
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

    assert params[0] == 0.65
    assert "DISTINCT ON (p.publication_id)" in sql_text
    assert "ORDER BY similarity_score DESC" in sql_text
    assert sql_text.strip().endswith("LIMIT $2;")


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
