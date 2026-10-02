"""Unit tests for GraphRetriever, EvidenceUnifier.from_graph, and GraphAnswerSynthesizer.

Docs Reference: docs/05 Retrieval Rag Design.md §5.3; docs/02 SRD.md FR7;
docs/04 Database Schema.md §6; docs/10 Implementation Plan.md (Task 8-retriever);
docs/11 Roadmap.md (Fase 6).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
import pytest

from backend.app.core.errors import DBTimeoutError
from backend.app.models.ask import FilterParams
from backend.app.services.evidence.unifier import EvidenceUnifier
from backend.app.services.retrievers.graph_retriever import (
    DEFAULT_LIMIT,
    DEFAULT_MAX_HOPS,
    MAX_ALLOWED_HOPS,
    MAX_LIMIT,
    GraphEdgeResult,
    GraphPublicationMeta,
    GraphRetrievalResult,
    GraphRetriever,
)
from backend.app.services.synthesizer.answer import GraphAnswerSynthesizer


class TestGraphRetrieverSafetyClamps:
    """Test limit and hop depth safety clamps (FR7.2)."""

    def test_clamp_limit(self):
        assert GraphRetriever.clamp_limit(None) == DEFAULT_LIMIT
        assert GraphRetriever.clamp_limit(0) == DEFAULT_LIMIT
        assert GraphRetriever.clamp_limit(-5) == DEFAULT_LIMIT
        assert GraphRetriever.clamp_limit(10) == 10
        assert GraphRetriever.clamp_limit(50) == 50
        assert GraphRetriever.clamp_limit(100) == MAX_LIMIT

    def test_clamp_hops(self):
        assert GraphRetriever.clamp_hops(None) == DEFAULT_MAX_HOPS
        assert GraphRetriever.clamp_hops(0) == DEFAULT_MAX_HOPS
        assert GraphRetriever.clamp_hops(-1) == DEFAULT_MAX_HOPS
        assert GraphRetriever.clamp_hops(1) == 1
        assert GraphRetriever.clamp_hops(2) == 2
        assert GraphRetriever.clamp_hops(3) == 3
        assert GraphRetriever.clamp_hops(5) == MAX_ALLOWED_HOPS


class TestGraphRetrieverTemplateDetection:
    """Test deterministic template selection rules (T1 - T4)."""

    def test_detect_template_t1_institution(self):
        t, params = GraphRetriever.detect_template(
            "Institusi mana yang berkolaborasi dengan Universitas Andalas?",
            resolved_institution_id="INST_001",
        )
        assert t == "T1"
        assert params["institution_id"] == "INST_001"

    def test_detect_template_t2_author(self):
        t, params = GraphRetriever.detect_template(
            "Siapa co-author dari Septi Gumiandari?",
            resolved_author_id="AUTH_001",
        )
        assert t == "T2"
        assert params["author_id"] == "AUTH_001"

    def test_detect_template_t3_topic(self):
        t, params = GraphRetriever.detect_template(
            "Institusi mana yang berkolaborasi dalam riset Artificial Intelligence?",
        )
        assert t == "T3"
        assert "Artificial Intelligence" in params["keyword"]

    def test_detect_template_t3_from_filters(self):
        filters = FilterParams(keyword="stem cell")
        t, params = GraphRetriever.detect_template(
            "Tampilkan jaringan kolaborasi",
            filters=filters,
        )
        assert t == "T3"
        assert params["keyword"] == "stem cell"

    def test_detect_template_t4_path_institution(self):
        t, params = GraphRetriever.detect_template(
            "Bagaimana jalur kolaborasi antara ITB dan UI?",
            resolved_institution_id="INST_ITB",
        )
        assert t == "T4_INSTITUTION"
        assert params["source_id"] == "INST_ITB"

    def test_detect_template_t4_path_author(self):
        t, params = GraphRetriever.detect_template(
            "Tampilkan path hubungan co-author Septi Gumiandari",
            resolved_author_id="AUTH_001",
        )
        assert t == "T4_AUTHOR"
        assert params["source_id"] == "AUTH_001"


class TestGraphRetrieverExecution:
    """Test retrieval execution on mock DB connections."""

    @pytest.mark.asyncio
    async def test_retrieve_t1_institution_success(self):
        conn = AsyncMock()
        conn.fetch.side_effect = [
            # T1 edge rows
            [
                {
                    "partner_id": "INST_UI",
                    "partner_name": "Universitas Indonesia",
                    "publication_count": 12,
                    "via_publication_ids": ["PUB001", "PUB002"],
                },
                {
                    "partner_id": "INST_UGM",
                    "partner_name": "Universitas Gadjah Mada",
                    "publication_count": 5,
                    "via_publication_ids": ["PUB003"],
                },
            ],
            # Publication metadata rows
            [
                {
                    "publication_id": "PUB001",
                    "title": "Joint Cancer Research",
                    "year": 2024,
                    "doi": "10.1016/j.jcr.2024.01",
                    "eid": "2-s2.0-123",
                },
                {
                    "publication_id": "PUB002",
                    "title": "AI in Diagnostics",
                    "year": 2023,
                    "doi": None,
                    "eid": "2-s2.0-124",
                },
                {
                    "publication_id": "PUB003",
                    "title": "Biotechnology Advances",
                    "year": 2025,
                    "doi": "10.1016/j.bio.2025.02",
                    "eid": "2-s2.0-125",
                },
            ],
        ]

        result = await GraphRetriever.retrieve(
            conn,
            "Institusi mana yang berkolaborasi dengan ITB?",
            resolved_institution_id="INST_ITB",
            resolved_institution_name="Institut Teknologi Bandung",
        )

        assert not result.is_empty
        assert result.template_type == "T1"
        assert len(result.edges) == 2
        assert result.edges[0].partner_name == "Universitas Indonesia"
        assert result.edges[0].publication_count == 12
        assert len(result.publications) == 3
        assert "PUB001" in result.publications
        assert result.publications["PUB001"].doi == "10.1016/j.jcr.2024.01"

    @pytest.mark.asyncio
    async def test_retrieve_t1_empty_when_no_entity_id(self):
        conn = AsyncMock()
        result = await GraphRetriever.retrieve(
            conn,
            "Siapa saja yang berkolaborasi?",
            resolved_institution_id=None,
        )
        assert result.is_empty
        assert len(result.edges) == 0

    @pytest.mark.asyncio
    async def test_retrieve_t2_author_success(self):
        conn = AsyncMock()
        conn.fetch.side_effect = [
            # T2 edge rows
            [
                {
                    "partner_id": "AUTH_002",
                    "partner_name": "Tony Liwang",
                    "publication_count": 7,
                    "via_publication_ids": ["PUB001"],
                }
            ],
            # Publication metadata rows
            [
                {
                    "publication_id": "PUB001",
                    "title": "Stem Cell Study",
                    "year": 2023,
                    "doi": "10.1016/j.stem.2023",
                    "eid": "2-s2.0-999",
                }
            ],
        ]

        result = await GraphRetriever.retrieve(
            conn,
            "Siapa co-author dari Septi Gumiandari?",
            resolved_author_id="AUTH_001",
            resolved_author_name="Septi Gumiandari",
        )

        assert not result.is_empty
        assert result.template_type == "T2"
        assert len(result.edges) == 1
        assert result.edges[0].partner_name == "Tony Liwang"
        assert result.edges[0].publication_count == 7
        assert "PUB001" in result.publications

    @pytest.mark.asyncio
    async def test_retrieve_t3_topic_composition_success(self):
        conn = AsyncMock()
        conn.fetch.side_effect = [
            # T3 rows
            [
                {
                    "partner_id": "INST_001",
                    "partner_name": "Universitas Indonesia",
                    "publication_count": 10,
                    "via_publication_ids": ["PUB010"],
                }
            ],
            # Metadata rows
            [
                {
                    "publication_id": "PUB010",
                    "title": "Machine Learning in Healthcare",
                    "year": 2024,
                    "doi": "10.1000/mlhc",
                    "eid": None,
                }
            ],
        ]

        result = await GraphRetriever.retrieve(
            conn,
            "Kolaborasi institusi dalam riset Machine Learning",
        )

        assert not result.is_empty
        assert result.template_type == "T3"
        assert len(result.edges) == 1
        assert result.edges[0].partner_name == "Universitas Indonesia"
        assert result.edges[0].extra_metadata.get("topic_keyword") == "Machine Learning"

    @pytest.mark.asyncio
    async def test_retrieve_t4_path_search_success(self):
        conn = AsyncMock()
        conn.fetch.side_effect = [
            # T4 path rows
            [
                {
                    "partner_id": "INST_UGM",
                    "partner_name": "Universitas Gadjah Mada",
                    "path_nodes": ["INST_ITB", "INST_UI", "INST_UGM"],
                    "hop_count": 2,
                    "publication_count": 4,
                    "via_publication_ids": ["PUB020"],
                }
            ],
            # Metadata rows
            [
                {
                    "publication_id": "PUB020",
                    "title": "Multi-center Collaboration",
                    "year": 2025,
                    "doi": "10.1000/mcc.2025",
                    "eid": None,
                }
            ],
        ]

        result = await GraphRetriever.retrieve(
            conn,
            "Bagaimana jalur kolaborasi ITB ke UGM?",
            resolved_institution_id="INST_ITB",
            resolved_institution_name="Institut Teknologi Bandung",
            max_hops=3,
        )

        assert not result.is_empty
        assert result.template_type in ("T4", "T4_INSTITUTION", "T4_AUTHOR")
        assert len(result.edges) == 1
        assert result.edges[0].hop_count == 2
        assert result.edges[0].path_nodes == ["INST_ITB", "INST_UI", "INST_UGM"]

    @pytest.mark.asyncio
    async def test_retrieve_timeout_raises_db_timeout_error(self):
        conn = AsyncMock()
        conn.fetch.side_effect = asyncio.TimeoutError()

        with pytest.raises(DBTimeoutError):
            await GraphRetriever.retrieve(
                conn,
                "Siapa rekan kolaborasi ITB?",
                resolved_institution_id="INST_ITB",
            )

    @pytest.mark.asyncio
    async def test_retrieve_t3_escapes_like_wildcards_with_filters(self):
        """Explicit filters.keyword with LIKE wildcards must be escaped before the ILIKE call."""
        conn = AsyncMock()
        conn.fetch.side_effect = [[], []]
        result = await GraphRetriever.retrieve(
            conn,
            "Kolaborasi dalam riset X",
            filters=FilterParams(keyword="stem_cell_100%"),
        )
        assert result.template_type == "T3"
        # conn.fetch(sql, pattern, limit): pattern is the 2nd positional arg
        call = conn.fetch.call_args_list[0][0]
        pattern_arg = call[1]
        assert "%stem\\_cell\\_100\\%" in pattern_arg, (
            f"expected escaped wildcards in pattern, got: {pattern_arg!r}"
        )

    @pytest.mark.asyncio
    async def test_retrieve_filters_ignored_tracking(self):
        conn = AsyncMock()
        conn.fetch.side_effect = [[], []]
        filters = FilterParams(year=2024, country="indonesia", document_type="Article")

        result = await GraphRetriever.retrieve(
            conn,
            "Siapa rekan kolaborasi ITB?",
            filters=filters,
            resolved_institution_id="INST_ITB",
        )

        assert "year" in result.filters_ignored
        assert "country" in result.filters_ignored
        assert "document_type" in result.filters_ignored


class TestEvidenceUnifierFromGraph:
    """Test EvidenceUnifier.from_graph with rich provenance and publication refs."""

    def test_unify_from_graph_retrieval_result(self):
        graph_result = GraphRetrievalResult(
            template_type="T1",
            edges=[
                GraphEdgeResult(
                    partner_id="INST_UI",
                    partner_name="Universitas Indonesia",
                    publication_count=8,
                    via_publication_ids=["PUB001"],
                )
            ],
            publications={
                "PUB001": GraphPublicationMeta(
                    publication_id="PUB001",
                    title="Cancer Genomics",
                    year=2023,
                    doi="10.1000/cg.2023",
                    eid="2-s2.0-100",
                )
            },
            sql_executed="SQL_TEMPLATE_T1",
            target_entity_name="ITB",
            target_entity_id="INST_ITB",
            filters_ignored=["country"],
        )

        ev_set = EvidenceUnifier.from_graph(
            "Kolaborasi ITB dengan UI",
            graph_result,
        )

        assert not ev_set.is_empty
        assert len(ev_set.evidence_objects) == 1
        ev = ev_set.evidence_objects[0]
        assert "Universitas Indonesia" in ev.claim
        assert ev.value == 8
        assert ev.metric == "publication_count"
        assert len(ev.sources) == 1
        assert ev.sources[0].publication_id == "PUB001"
        assert ev.sources[0].doi == "10.1000/cg.2023"

        assert len(ev_set.sources) == 1
        src = ev_set.sources[0]
        assert src.publication_id == "PUB001"
        assert src.title == "Cancer Genomics"
        assert src.source_type == "graph"
        assert src.relevance_score == 1.0

        assert len(ev_set.items) == 1
        item = ev_set.items[0]
        assert item.source_type == "graph"
        assert item.provenance_ids == ["PUB001"]

        assert ev_set.filters_ignored == ["country"]

    def test_unify_from_graph_empty_result(self):
        graph_result = GraphRetrievalResult(
            template_type="T1",
            edges=[],
            publications={},
            filters_ignored=[],
        )
        ev_set = EvidenceUnifier.from_graph(
            "Siapa mitra kolaborasi?",
            graph_result,
        )
        assert ev_set.is_empty
        assert len(ev_set.evidence_objects) == 0
        assert len(ev_set.sources) == 0
        assert len(ev_set.items) == 0


class TestGraphAnswerSynthesizer:
    """Test grounded narrative generation and citation verification for GraphRoute."""

    def test_synthesize_success_with_verified_citations(self):
        graph_result = GraphRetrievalResult(
            template_type="T1",
            edges=[
                GraphEdgeResult(
                    partner_id="INST_UI",
                    partner_name="Universitas Indonesia",
                    publication_count=10,
                    via_publication_ids=["PUB001"],
                )
            ],
            publications={
                "PUB001": GraphPublicationMeta(
                    publication_id="PUB001",
                    title="AI for Climate Science",
                    year=2024,
                    doi="10.1000/aics.2024",
                )
            },
            target_entity_name="ITB",
        )

        resp = GraphAnswerSynthesizer.synthesize(
            "Siapa saja partner kolaborasi ITB?",
            graph_result,
        )

        assert resp.status == "ok"
        assert "Universitas Indonesia" in resp.answer
        assert "10 publikasi bersama" in resp.answer
        assert "[AI for Climate Science, 2024, 10.1000/aics.2024]" in resp.answer
        assert len(resp.unverified_citations) == 0
        assert len(resp.evidence_objects) == 1
        assert len(resp.sources) == 1

    def test_synthesize_empty_short_circuits_to_not_found(self):
        graph_result = GraphRetrievalResult(
            template_type="T1",
            edges=[],
            publications={},
        )

        resp = GraphAnswerSynthesizer.synthesize(
            "Siapa saja partner kolaborasi ITB?",
            graph_result,
        )

        assert resp.status == "not_found"
        assert "Data tidak ditemukan" in resp.answer
        assert len(resp.evidence_objects) == 0
        assert len(resp.sources) == 0
        assert len(resp.unverified_citations) == 0
