"""Unit tests for HybridRetriever, EvidenceUnifier.from_hybrid, and HybridAnswerSynthesizer.

Docs Reference: docs/05 Retrieval Rag Design.md §5.4; docs/02 SRD.md FR6, FR7;
docs/04 Database Schema.md §7; docs/10 Implementation Plan.md (Task 8.5, Task 9-full);
docs/11 Roadmap.md (Fase 7).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.app.core.errors import DBTimeoutError
from backend.app.models.ask import FilterParams
from backend.app.services.evidence.models import EvidenceSet
from backend.app.services.evidence.unifier import EvidenceUnifier
from backend.app.services.retrievers.hybrid_retriever import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    HybridExpertItem,
    HybridPublicationMeta,
    HybridRetrievalResult,
    HybridRetriever,
    HybridTopicEvolutionItem,
)
from backend.app.services.synthesizer.answer import (
    AnswerSynthesizer,
    HybridAnswerSynthesizer,
    UnifiedAnswerSynthesizer,
)


class TestHybridRetrieverSafetyClamps:
    """Test limit safety clamps (FR6.2, FR7.2)."""

    def test_clamp_limit_default(self):
        assert HybridRetriever.clamp_limit(None) == DEFAULT_LIMIT
        assert HybridRetriever.clamp_limit(0) == DEFAULT_LIMIT
        assert HybridRetriever.clamp_limit(-10) == DEFAULT_LIMIT

    def test_clamp_limit_custom(self):
        assert HybridRetriever.clamp_limit(5) == 5
        assert HybridRetriever.clamp_limit(20) == 20
        assert HybridRetriever.clamp_limit(50) == 50

    def test_clamp_limit_max_cap(self):
        assert HybridRetriever.clamp_limit(100) == MAX_LIMIT
        assert HybridRetriever.clamp_limit(999) == MAX_LIMIT


class TestHybridRetrieverIntentDetection:
    """Test deterministic intent sub-classification for HybridRoute."""

    def test_detect_intent_expert_ranking(self):
        assert HybridRetriever.detect_intent("Siapa pakar utama pada topik Mesenchymal Stem Cell?") == "EXPERT_RANKING"
        assert HybridRetriever.detect_intent("Who is the leading author in nanotech?") == "EXPERT_RANKING"
        assert HybridRetriever.detect_intent("Daftar peneliti terbaik dengan skor kepakaran tertinggi") == "EXPERT_RANKING"
        assert HybridRetriever.detect_intent("penelitian", filters=FilterParams(author_name="Budi")) == "EXPERT_RANKING"

    def test_detect_intent_topic_trends(self):
        assert HybridRetriever.detect_intent("Bagaimana tren perkembangan terapi stem cell 5 tahun terakhir?") == "TOPIC_TRENDS"
        assert HybridRetriever.detect_intent("What is the topic evolution and growth score of AI?") == "TOPIC_TRENDS"
        assert HybridRetriever.detect_intent("Topik apa saja yang sedang berkembang pesat (emerging)?") == "TOPIC_TRENDS"

    def test_detect_intent_combined_analytics(self):
        assert HybridRetriever.detect_intent("Siapa pakar dan bagaimana tren perkembangan topik riset ini?") == "COMBINED_ANALYTICS"
        assert HybridRetriever.detect_intent("Analisis kebijakan riset nasional") == "COMBINED_ANALYTICS"


class TestHybridRetrieverExtractTopicKeyword:
    """Test extraction of topic name candidate from filters or question text."""

    def test_extract_from_topic_name_filter(self):
        filters = FilterParams(topic_name="Mesenchymal Stem Cell")
        assert HybridRetriever.extract_topic_keyword("pertanyaan riset", filters) == "Mesenchymal Stem Cell"

    def test_extract_from_keyword_filter(self):
        filters = FilterParams(keyword="molecular docking")
        assert HybridRetriever.extract_topic_keyword("pertanyaan riset", filters) == "molecular docking"

    def test_extract_from_question_regex(self):
        kw = HybridRetriever.extract_topic_keyword("Bagaimana tren pada topik Mesenchymal Stem Cell dalam 5 tahun terakhir?")
        assert kw is not None
        assert "mesenchymal stem cell" in kw.lower()

    def test_extract_fallback_none(self):
        assert HybridRetriever.extract_topic_keyword("Siapa saja?") is None


class TestHybridRetrieverExecution:
    """Test retrieval execution against mocked database connection."""

    @pytest.mark.asyncio
    async def test_retrieve_empty_result(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_conn.fetch.return_value = []

        result = await HybridRetriever.retrieve(
            conn=mock_conn,
            question="Pertanyaan topik antah berantah yang tidak ada?",
        )
        assert result.is_empty is True
        assert len(result.topics) == 0
        assert len(result.experts) == 0

    @pytest.mark.asyncio
    async def test_retrieve_trends_ok(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"topic_id": 1, "topic_name": "Stem Cells"}
        mock_conn.fetch.return_value = [
            {
                "topic_id": 1,
                "topic_name": "Stem Cells",
                "year": 2024,
                "publication_count": 10,
                "citation_count": 25,
                "growth_score": 0.35,
                "citation_acceleration": 0.15,
                "recency_weight": 0.9,
                "is_emerging": True,
            }
        ]

        result = await HybridRetriever.retrieve(
            conn=mock_conn,
            question="Bagaimana tren perkembangan topik Stem Cells?",
        )
        assert result.is_empty is False
        assert result.intent_type == "TOPIC_TRENDS"
        assert len(result.topics) == 1
        assert result.topics[0].topic_name == "Stem Cells"
        assert result.topics[0].growth_score == 0.35
        assert result.topics[0].is_emerging is True

    @pytest.mark.asyncio
    async def test_retrieve_experts_ok(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"topic_id": 2, "topic_name": "AI Benchmark"}
        
        # 1st fetch: expert rows; 2nd fetch: publications rows
        mock_conn.fetch.side_effect = [
            [
                {
                    "author_id": "AUTH_001",
                    "author_name": "Dr. Sutanto",
                    "topic_id": 2,
                    "topic_name": "AI Benchmark",
                    "expertise_score": 85.5,
                    "relevance_score": 90.0,
                    "productivity_score": 80.0,
                    "impact_score": 85.0,
                    "recency_score": 90.0,
                    "h_index_topic": 5,
                    "publication_count_topic": 8,
                    "citation_count_topic": 45,
                    "coauthor_network_size": 12,
                }
            ],
            [
                {
                    "publication_id": "PUB_AI_1",
                    "title": "Benchmarking AI Datasets in Indonesia",
                    "year": 2024,
                    "doi": "10.1016/j.ai.2024.01",
                    "eid": "2-s2.0-ai1",
                }
            ],
        ]

        result = await HybridRetriever.retrieve(
            conn=mock_conn,
            question="Siapa pakar utama pada topik AI Benchmark?",
        )
        assert result.is_empty is False
        assert result.intent_type == "EXPERT_RANKING"
        assert len(result.experts) == 1
        assert result.experts[0].author_name == "Dr. Sutanto"
        assert result.experts[0].expertise_score == 85.5
        assert "PUB_AI_1" in result.publications
        assert result.publications["PUB_AI_1"].doi == "10.1016/j.ai.2024.01"

    @pytest.mark.asyncio
    async def test_retrieve_timeout_raises_db_timeout_error(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = asyncio.TimeoutError()

        with pytest.raises(DBTimeoutError):
            await HybridRetriever.retrieve(
                conn=mock_conn,
                question="Bagaimana tren topik stem cell?",
                filters=FilterParams(topic_name="Stem Cell"),
            )


class TestEvidenceUnifierFromHybrid:
    """Test EvidenceUnifier.from_hybrid normalization and ranking."""

    def test_from_hybrid_empty(self):
        res = HybridRetrievalResult(
            intent_type="TOPIC_TRENDS",
            topics=[],
            experts=[],
            publications={},
            filters_ignored=["country"],
            sql_executed="TEMPLATE: SQL_GOLD_ANALYTICS",
        )
        ev_set = EvidenceUnifier.from_hybrid("Pertanyaan?", res)
        assert ev_set.is_empty is True
        assert len(ev_set.evidence_objects) == 0
        assert len(ev_set.sources) == 0
        assert len(ev_set.items) == 0
        assert ev_set.filters_ignored == ["country"]

    def test_from_hybrid_topics(self):
        topic_item = HybridTopicEvolutionItem(
            topic_id=1,
            topic_name="Mesenchymal Stem Cells",
            year=2024,
            publication_count=5,
            citation_count=15,
            growth_score=0.45,
            citation_acceleration=0.20,
            recency_weight=1.0,
            is_emerging=True,
        )
        res = HybridRetrievalResult(
            intent_type="TOPIC_TRENDS",
            topics=[topic_item],
            experts=[],
            publications={},
        )
        ev_set = EvidenceUnifier.from_hybrid("Tren stem cell?", res)
        assert ev_set.is_empty is False
        assert len(ev_set.evidence_objects) == 1
        ev = ev_set.evidence_objects[0]
        assert ev.metric == "growth_score"
        assert ev.value == 0.45
        assert ev.confidence == 1.0
        assert "Mesenchymal Stem Cells" in ev.claim
        assert "2024" in ev.period

    def test_from_hybrid_experts_with_publications(self):
        expert_item = HybridExpertItem(
            author_id="AUTH_1",
            author_name="Prof. Budi",
            topic_id=1,
            topic_name="Nanomaterials",
            expertise_score=92.4,
            relevance_score=95.0,
            productivity_score=90.0,
            impact_score=92.0,
            recency_score=90.0,
            h_index_topic=7,
            publication_count_topic=10,
            citation_count_topic=60,
            coauthor_network_size=15,
        )
        pub_meta = HybridPublicationMeta(
            publication_id="PUB_NANO_1",
            title="Synthesis of Silver Nanoparticles",
            year=2024,
            doi="10.1000/nano.2024",
            eid="2-s2.0-nano1",
        )
        res = HybridRetrievalResult(
            intent_type="EXPERT_RANKING",
            topics=[],
            experts=[expert_item],
            publications={"PUB_NANO_1": pub_meta},
        )
        ev_set = EvidenceUnifier.from_hybrid("Siapa pakar nano?", res)
        assert ev_set.is_empty is False
        assert len(ev_set.evidence_objects) == 1
        assert len(ev_set.sources) == 1
        assert ev_set.sources[0].doi == "10.1000/nano.2024"
        assert len(ev_set.evidence_objects[0].sources) == 1
        assert ev_set.evidence_objects[0].sources[0].publication_id == "PUB_NANO_1"

    def test_from_hybrid_determinism(self):
        """Verify calling from_hybrid multiple times with same input yields deterministic order."""
        t1 = HybridTopicEvolutionItem(
            topic_id=1, topic_name="T1", year=2024, publication_count=5, citation_count=10,
            growth_score=0.5, citation_acceleration=0.1, recency_weight=1.0, is_emerging=True,
        )
        t2 = HybridTopicEvolutionItem(
            topic_id=2, topic_name="T2", year=2024, publication_count=8, citation_count=20,
            growth_score=0.8, citation_acceleration=0.2, recency_weight=1.0, is_emerging=True,
        )
        res = HybridRetrievalResult(intent_type="TOPIC_TRENDS", topics=[t1, t2])
        ev1 = EvidenceUnifier.from_hybrid("q", res)
        ev2 = EvidenceUnifier.from_hybrid("q", res)
        assert [o.value for o in ev1.evidence_objects] == [o.value for o in ev2.evidence_objects]


class TestHybridAnswerSynthesizer:
    """Test HybridAnswerSynthesizer grounded generation and citation verification."""

    def test_synthesize_empty_result(self):
        empty_res = HybridRetrievalResult(intent_type="TOPIC_TRENDS")
        synth = HybridAnswerSynthesizer.synthesize("Tren topik?", empty_res)
        assert synth.status == "not_found"
        assert "Data tidak ditemukan" in synth.answer
        assert len(synth.evidence_objects) == 0
        assert len(synth.sources) == 0

    def test_synthesize_topic_trends_ok(self):
        topic_item = HybridTopicEvolutionItem(
            topic_id=1,
            topic_name="Mesenchymal Stem Cells",
            year=2024,
            publication_count=5,
            citation_count=15,
            growth_score=0.45,
            citation_acceleration=0.20,
            recency_weight=1.0,
            is_emerging=True,
        )
        res = HybridRetrievalResult(
            intent_type="TOPIC_TRENDS",
            topics=[topic_item],
            target_topic_name="Mesenchymal Stem Cells",
        )
        synth = HybridAnswerSynthesizer.synthesize("Tren stem cell?", res)
        assert synth.status == "ok"
        assert "tren perkembangan topik riset" in synth.answer
        assert len(synth.evidence_objects) == 1
        assert synth.evidence_objects[0].metric == "growth_score"
        assert len(synth.unverified_citations) == 0

    def test_synthesize_experts_with_citations_ok(self):
        expert_item = HybridExpertItem(
            author_id="AUTH_1",
            author_name="Prof. Budi",
            topic_id=1,
            topic_name="Nanomaterials",
            expertise_score=92.4,
            relevance_score=95.0,
            productivity_score=90.0,
            impact_score=92.0,
            recency_score=90.0,
            h_index_topic=7,
            publication_count_topic=10,
            citation_count_topic=60,
            coauthor_network_size=15,
        )
        pub_meta = HybridPublicationMeta(
            publication_id="PUB_NANO_1",
            title="Synthesis of Silver Nanoparticles",
            year=2024,
            doi="10.1000/nano.2024",
            eid="2-s2.0-nano1",
        )
        res = HybridRetrievalResult(
            intent_type="EXPERT_RANKING",
            topics=[],
            experts=[expert_item],
            publications={"PUB_NANO_1": pub_meta},
            target_topic_name="Nanomaterials",
        )
        synth = HybridAnswerSynthesizer.synthesize("Pakar nano?", res)
        assert synth.status == "ok"
        assert "Prof. Budi" in synth.answer
        assert "[Synthesis of Silver Nanoparticles, 2024, 10.1000/nano.2024]" in synth.answer
        assert len(synth.unverified_citations) == 0


class TestUnifiedAnswerSynthesizer:
    """Test Unified AnswerSynthesizer multi-route dispatcher."""

    def test_unified_synthesize_empty(self):
        empty_set = EvidenceSet(query="test", evidence_objects=[], sources=[], items=[], sql_executed=None)
        res = AnswerSynthesizer.synthesize("test", empty_set, route="HybridRoute")
        assert res.status == "not_found"
        assert "Data tidak ditemukan" in res.answer

    def test_unified_synthesize_hybrid_route(self):
        from backend.app.models.ask import EvidenceObject
        ev = EvidenceObject(
            claim="Topik Stem Cell memiliki laju pertumbuhan 0.40",
            metric="growth_score",
            value=0.40,
            period="2024",
            confidence=1.0,
        )
        ev_set = EvidenceSet(query="test", evidence_objects=[ev], sources=[], items=[], sql_executed=None)
        res = AnswerSynthesizer.synthesize("test", ev_set, route="HybridRoute")
        assert res.status == "ok"
        assert "Gold layer" in res.answer
        assert "0.40" in res.answer

    def test_unified_alias(self):
        assert UnifiedAnswerSynthesizer is AnswerSynthesizer
