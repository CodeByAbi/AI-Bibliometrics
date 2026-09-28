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


class _EmptyConnStub:
    """Minimal asyncpg.Connection stub returning zero rows for entity lookups."""

    async def fetch(self, *args, **kwargs):
        return []

    async def fetchval(self, *args, **kwargs):
        return 0


class TestEntityResolutionNotFound:
    """Test deterministic not_found for mentioned-but-unknown entities (FR2.4)."""

    @pytest.mark.asyncio
    async def test_unknown_author_returns_not_found(self):
        filters = FilterParams(author_name="Xyzzq Qwerty Tidakada")
        result = await EntityResolutionGate.resolve_entities(
            _EmptyConnStub(), "Berapa total publikasi?", filters
        )
        assert result.status == "not_found"
        assert "Xyzzq Qwerty Tidakada" in (result.clarification_message or "")

    @pytest.mark.asyncio
    async def test_unknown_institution_returns_not_found(self):
        filters = FilterParams(institution_name="Universitas Fiktif Belaka")
        result = await EntityResolutionGate.resolve_entities(
            _EmptyConnStub(), "Berapa total publikasi?", filters
        )
        assert result.status == "not_found"
        assert "Universitas Fiktif Belaka" in (result.clarification_message or "")

    @pytest.mark.asyncio
    async def test_no_entities_returns_ok(self):
        result = await EntityResolutionGate.resolve_entities(
            _EmptyConnStub(), "Berapa total publikasi pada tahun 2025?", None
        )
        assert result.status == "ok"
        assert result.candidates is None


class _ScriptedConnStub:
    """asyncpg stub routing canned rows by query content."""

    def __init__(self, author_exact=None, inst_exact=None):
        self.author_exact = author_exact or []
        self.inst_exact = inst_exact or []

    async def fetch(self, sql, *args, **kwargs):
        if "FROM authors" in sql and "author_name_normalized" in sql:
            return self.author_exact
        if "FROM institutions" in sql and "institution_name_normalized" in sql:
            return self.inst_exact
        return []

    async def fetchval(self, *args, **kwargs):
        return 3


class TestEntityJointResolution:
    """Both entities resolve jointly; a resolved author never masks ambiguity."""

    @pytest.mark.asyncio
    async def test_author_and_institution_resolved_jointly(self):
        conn = _ScriptedConnStub(
            author_exact=[{"author_id": "A1", "author_name": "Septi Gumiandari"}],
            inst_exact=[{"institution_id": "I1", "institution_name": "Universitas Andalas"}],
        )
        filters = FilterParams(author_name="Septi Gumiandari", institution_name="Universitas Andalas")
        result = await EntityResolutionGate.resolve_entities(conn, "Berapa total publikasi?", filters)
        assert result.status == "ok"
        assert result.resolved_author_id == "A1"
        assert result.resolved_author_name == "Septi Gumiandari"
        assert result.resolved_institution_id == "I1"
        assert result.resolved_institution_name == "Universitas Andalas"

    @pytest.mark.asyncio
    async def test_author_ok_institution_ambiguous_surfaces_clarification(self):
        conn = _ScriptedConnStub(
            author_exact=[{"author_id": "A1", "author_name": "Septi Gumiandari"}],
            inst_exact=[
                {"institution_id": "I1", "institution_name": "Universitas X", "country": "indonesia"},
                {"institution_id": "I2", "institution_name": "Universitas Y", "country": "indonesia"},
            ],
        )
        filters = FilterParams(author_name="Septi Gumiandari", institution_name="Universitas")
        result = await EntityResolutionGate.resolve_entities(conn, "Berapa total publikasi?", filters)
        assert result.status == "needs_clarification"
        assert result.candidates is not None
        assert len(result.candidates) == 2
        assert all(c.type == "institution" for c in result.candidates)

    @pytest.mark.asyncio
    async def test_author_ambiguous_takes_priority(self):
        conn = _ScriptedConnStub(
            author_exact=[
                {"author_id": "A1", "author_name": "Ahmad S"},
                {"author_id": "A2", "author_name": "Ahmad Z"},
            ],
            inst_exact=[{"institution_id": "I1", "institution_name": "Universitas Andalas"}],
        )
        filters = FilterParams(author_name="Ahmad", institution_name="Universitas Andalas")
        result = await EntityResolutionGate.resolve_entities(conn, "Berapa total publikasi?", filters)
        assert result.status == "needs_clarification"
        assert all(c.type == "author" for c in result.candidates)
