"""Unit tests for QuestionRouter and EntityResolutionGate.

Docs Reference: docs/05 Retrieval Rag Design.md §3, docs/10 Implementation Plan.md §1 (Task 4).
"""

from __future__ import annotations

import pytest
from backend.app.models.ask import FilterParams
from backend.app.services.router import (
    EntityResolutionGate,
    QuestionRouter,
    normalize_text,
)


class TestQuestionRouter:
    """Test 4-route question classification rules."""

    @pytest.mark.parametrize(
        "query,expected_route",
        [
            # SQLRoute queries (Aggregations, counts, rankings, top-N, explicit stats)
            ("Siapa 5 penulis paling produktif tahun 2023?", "SQLRoute"),
            ("Berapa total publikasi pada tahun 2025?", "SQLRoute"),
            ("Tampilkan top 10 publikasi dengan sitasi terbanyak", "SQLRoute"),
            ("Who are the top 5 most productive authors in 2023?", "SQLRoute"),
            ("How many publications in year 2025?", "SQLRoute"),
            ("Most cited publications in the database", "SQLRoute"),
            ("Hitung jumlah publikasi dari institusi ITB", "SQLRoute"),
            ("Daftar publikasi pada tahun 2025", "SQLRoute"),
            # GraphRoute queries (Collaboration, co-authorship, networks)
            ("Siapa saja yang berkolaborasi dengan Dr. Septi Gumiandari?", "GraphRoute"),
            ("Institusi mana yang berkolaborasi dengan Universitas Andalas?", "GraphRoute"),
            ("Who are the co-authors of author Septi Gumiandari?", "GraphRoute"),
            ("Tampilkan jaringan riset dan kemitraan antar institusi", "GraphRoute"),
            ("Who collaborated with Hasanuddin University?", "GraphRoute"),
            ("Siapa co-author dari Eti Nurhayati?", "GraphRoute"),
            # HybridRoute queries (Topic trends, topic evolution, expertise scoring, policy synthesis)
            ("Bagaimana tren perkembangan terapi stem cell 5 tahun terakhir?", "HybridRoute"),
            ("Siapa pakar utama pada topik Mesenchymal Stem Cell di Indonesia?", "HybridRoute"),
            ("What are the emerging topics and topic evolution in pharmacology?", "HybridRoute"),
            ("Who are the leading experts based on expertise score?", "HybridRoute"),
            ("Berikan sintesis kebijakan riset bioteknologi kesehatan", "HybridRoute"),
            # VectorRoute queries (Semantic exploration, conceptual mechanisms, abstract search)
            ("Paper yang membahas mekanisme inhibisi xanthine oxidase", "VectorRoute"),
            ("Artikel tentang stres oksidatif pada Wharton's jelly", "VectorRoute"),
            ("Studies exploring anti-inflammatory mechanisms of conditioned medium", "VectorRoute"),
            ("Penelitian mengenai etika komunikasi dalam keluarga muslim", "VectorRoute"),
            ("Concept of oxidative stress in stem cell therapy", "VectorRoute"),
        ],
    )
    def test_classify_route_patterns(self, query: str, expected_route: str):
        decision = QuestionRouter.classify_route(query)
        assert decision.route == expected_route
        assert decision.reasoning is not None

    def test_filter_driven_sql_routing(self):
        """Verify structured filters with list/count intent route to SQLRoute."""
        decision = QuestionRouter.classify_route(
            "Tampilkan publikasi",
            filters=FilterParams(year=2025, author_name="Gumiandari"),
        )
        assert decision.route == "SQLRoute"
        assert decision.answered_via_fallback is False

    def test_topic_filter_hybrid_routing(self):
        """Verify topic_name filter with trend intent routes to HybridRoute."""
        decision = QuestionRouter.classify_route(
            "Bagaimana tren publikasi",
            filters=FilterParams(topic_name="Stem Cell"),
        )
        assert decision.route == "HybridRoute"

    def test_unmatched_query_fallback(self):
        """Verify queries with no keyword matches fall back to VectorRoute with fallback flag."""
        decision = QuestionRouter.classify_route("Xanthine oxidase allopurinol")
        assert decision.route == "VectorRoute"
        assert decision.answered_via_fallback is True


class TestEntityResolutionGate:
    """Test entity name extraction and normalization."""

    @pytest.mark.asyncio
    async def test_extract_candidate_names_from_filters(self):
        filters = FilterParams(author_name="Septi Gumiandari", institution_name="Universitas Andalas")
        auth, inst = await EntityResolutionGate.extract_candidate_names("Who wrote this?", filters)
        assert auth == "Septi Gumiandari"
        assert inst == "Universitas Andalas"

    @pytest.mark.asyncio
    async def test_extract_candidate_names_from_query(self):
        auth, inst = await EntityResolutionGate.extract_candidate_names(
            "Berapa publikasi oleh Septi Gumiandari pada tahun 2025?"
        )
        assert auth is not None
        assert "Septi Gumiandari" in auth

    def test_normalize_text(self):
        assert normalize_text("Gumiandari, Septi!") == "gumiandari septi"
        assert normalize_text("  Universitas   Andalas. ") == "universitas andalas"
        assert normalize_text("") == ""
