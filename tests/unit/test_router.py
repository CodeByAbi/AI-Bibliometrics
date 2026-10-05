"""Unit tests for QuestionRouter and EntityResolutionGate.

Docs Reference: docs/05 Retrieval Rag Design.md §3, docs/10 Implementation Plan.md §1 (Task 4).
"""

from __future__ import annotations

import pytest
from backend.app.models.ask import FilterParams
from backend.app.services.router import (
    EntityResolutionGate,
    QuestionRouter,
    YearFilter,
    build_extracted_entities,
    build_year_filter,
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

    def test_collaboration_aggregate_prefers_graph(self):
        """Collaboration specificity wins over aggregate wording (Graph before SQL)."""
        decision = QuestionRouter.classify_route("Berapa jumlah kolaborasi institusi pada tahun 2023?")
        assert decision.route == "GraphRoute"
        assert decision.answered_via_fallback is False


class TestEntityResolutionGate:
    """Test entity name extraction and normalization."""

    @pytest.mark.asyncio
    async def test_extract_candidate_names_from_filters(self):
        filters = FilterParams(author_name="Septi Gumiandari", institution_name="Universitas Andalas")
        auth, inst = EntityResolutionGate.extract_candidate_names("Who wrote this?", filters)
        assert auth == "Septi Gumiandari"
        assert inst == "Universitas Andalas"

    @pytest.mark.asyncio
    async def test_extract_candidate_names_from_query(self):
        auth, inst = EntityResolutionGate.extract_candidate_names(
            "Berapa publikasi oleh Septi Gumiandari pada tahun 2025?"
        )
        assert auth is not None
        assert "Septi Gumiandari" in auth

    @pytest.mark.asyncio
    async def test_query_phrasing_is_not_a_name(self):
        auth, inst = EntityResolutionGate.extract_candidate_names(
            "Siapa 5 penulis paling produktif tahun 2025?"
        )
        assert auth is None

    @pytest.mark.asyncio
    async def test_institution_phrasing_is_not_a_name(self):
        _, inst = EntityResolutionGate.extract_candidate_names(
            "Tampilkan institusi paling produktif tahun 2025?"
        )
        assert inst is None

    @pytest.mark.asyncio
    async def test_by_metric_phrasing_is_not_a_name(self):
        """Top-N 'by <metric>' must not extract a phantom author.

        Regression: 'Who were the 5 most productive authors in 2023 by
        publication count?' extracted author_name='publication count',
        matched zero rows, and short-circuited to not_found before SQL
        retrieval ever ran.
        """
        auth, inst = EntityResolutionGate.extract_candidate_names(
            "Who were the 5 most productive authors in 2023 by publication count?"
        )
        assert auth is None
        assert inst is None

    @pytest.mark.asyncio
    async def test_by_citation_count_phrasing_is_not_a_name(self):
        auth, _ = EntityResolutionGate.extract_candidate_names(
            "Show top authors by citation count in 2023?"
        )
        assert auth is None

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

    @pytest.mark.asyncio
    async def test_top_n_by_metric_passes_gate(self):
        """Gate must not short-circuit top-N queries to not_found.

        With no entity mentioned, resolve_entities returns ok so the
        SQLRoute retriever runs instead of the entity-gate not_found path.
        """
        result = await EntityResolutionGate.resolve_entities(
            _EmptyConnStub(),
            "Who were the 5 most productive authors in 2023 by publication count?",
            None,
        )
        assert result.status == "ok"


class _ScriptedConnStub:
    """asyncpg stub routing canned rows by query content."""

    def __init__(self, author_exact=None, inst_exact=None, inst_counts=None, author_counts=None):
        self.author_exact = author_exact or []
        self.inst_exact = inst_exact or []
        # Optional per-id publication counts so tests can drive which
        # institution variant wins an auto-narrowing decision. Defaults to the
        # previous uniform 3-per-row behaviour.
        self.inst_counts = inst_counts
        self.author_counts = author_counts

    async def fetch(self, sql, *args, **kwargs):
        if "FROM authors" in sql and "author_name_normalized" in sql:
            return self.author_exact
        if "FROM institutions" in sql and "institution_name_normalized" in sql:
            return self.inst_exact
        # Batched GROUP BY count queries (ANY($1)) mirror fetchval → 3.
        if "FROM pub_author" in sql:
            if self.author_counts is not None:
                return [
                    {"author_id": r["author_id"], "cnt": self.author_counts.get(r["author_id"], 0)}
                    for r in self.author_exact
                ]
            return [{"author_id": r["author_id"], "cnt": 3} for r in self.author_exact]
        if "FROM pub_institution" in sql:
            if self.inst_counts is not None:
                return [
                    {"institution_id": r["institution_id"], "cnt": self.inst_counts.get(r["institution_id"], 0)}
                    for r in self.inst_exact
                ]
            return [{"institution_id": r["institution_id"], "cnt": 3} for r in self.inst_exact]
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
    async def test_author_ok_institution_ambiguous_auto_narrows_with_disclosure(self):
        """Ambiguitas institusi di-narrow otomatis, tapi tercatat (W4).

        Scopus stores one `institutions` row per department-level affiliation,
        so "Universitas" matching several rows is a family of siblings, not a
        question the user can meaningfully answer. Measured on the prototype
        corpus this produced 8 candidates for "Universitas Andalas" and 2 for
        "Universitas Indonesia", dead-ending nearly every institution question.

        The gate therefore resolves to the highest-publication variant. The
        decision must be auditable, so `narrowing` records the query, the
        winner, and the full candidate set it beat.
        """
        conn = _ScriptedConnStub(
            author_exact=[{"author_id": "A1", "author_name": "Septi Gumiandari"}],
            inst_exact=[
                {"institution_id": "I1", "institution_name": "Universitas X", "country": "indonesia"},
                {"institution_id": "I2", "institution_name": "Universitas Y", "country": "indonesia"},
            ],
            inst_counts={"I1": 1, "I2": 7},
        )
        filters = FilterParams(author_name="Septi Gumiandari", institution_name="Universitas")
        result = await EntityResolutionGate.resolve_entities(conn, "Berapa total publikasi?", filters)

        assert result.status == "ok"
        # Highest publication count wins.
        assert result.resolved_institution_id == "I2"
        assert result.resolved_institution_name == "Universitas Y"
        # A resolved author is never masked by the institution narrowing.
        assert result.resolved_author_id == "A1"
        # The narrowing is disclosed, not silent.
        assert result.narrowing is not None
        narrowing = result.narrowing
        assert narrowing.entity_type == "institution"
        assert narrowing.selected_id == "I2"
        assert narrowing.candidate_count == 2
        assert set(narrowing.candidate_names) == {
            "Universitas X",
            "Universitas Y",
        }

    @pytest.mark.asyncio
    async def test_institution_narrowing_is_deterministic_on_count_tie(self):
        """A publication-count tie must resolve the same way every time."""
        rows = [
            {"institution_id": "I2", "institution_name": "Universitas Zeta", "country": "indonesia"},
            {"institution_id": "I1", "institution_name": "Universitas Alpha", "country": "indonesia"},
        ]
        picks = set()
        for _ in range(5):
            conn = _ScriptedConnStub(
                inst_exact=rows,
                inst_counts={"I1": 4, "I2": 4},
            )
            result = await EntityResolutionGate.resolve_entities(
                conn, "Berapa publikasi?", FilterParams(institution_name="Universitas")
            )
            picks.add(result.resolved_institution_id)
        assert picks == {"I1"}, "tie must break to the lowest name every time"

    @pytest.mark.asyncio
    async def test_author_ambiguous_still_surfaces_clarification(self):
        """Author names are NOT auto-narrowed.

        Person names genuinely collide across institutions and carry no
        department decomposition, so the user is still asked. Only institutions
        auto-narrow.
        """
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

    @pytest.mark.asyncio
    async def test_single_institution_match_has_no_narrowing(self):
        """No narrowing record when the query was already unambiguous."""
        conn = _ScriptedConnStub(
            inst_exact=[{"institution_id": "I1", "institution_name": "Universitas Andalas"}],
        )
        result = await EntityResolutionGate.resolve_entities(
            conn, "Berapa publikasi?", FilterParams(institution_name="Universitas Andalas")
        )
        assert result.status == "ok"
        assert result.narrowing is None


class TestYearFilterContract:
    """FR2.3: typed YearFilter with allowlisted op enum (P0-1)."""

    def test_explicit_year_maps_to_eq(self):
        yf = build_year_filter("Berapa total publikasi?", FilterParams(year=2025))
        assert yf is not None
        assert yf.op == "eq"
        assert yf.year == 2025

    def test_explicit_range_maps_to_between(self):
        yf = build_year_filter("Berapa total publikasi?", FilterParams(year_from=2020, year_to=2023))
        assert yf is not None
        assert yf.op == "between"
        assert yf.year_from == 2020
        assert yf.year_to == 2023

    def test_explicit_from_maps_to_gte(self):
        yf = build_year_filter("Berapa total publikasi?", FilterParams(year_from=2021))
        assert yf is not None
        assert yf.op == "gte"

    def test_free_text_year_maps_to_eq(self):
        yf = build_year_filter("Siapa 5 penulis paling produktif tahun 2025?")
        assert yf is not None
        assert yf.op == "eq"
        assert yf.year == 2025

    def test_free_text_setelah_maps_to_gte(self):
        yf = build_year_filter("Publikasi setelah 2020")
        assert yf is not None
        assert yf.op == "gte"
        assert yf.year == 2020

    def test_free_text_range_maps_to_between(self):
        yf = build_year_filter("Publikasi 2020-2023")
        assert yf is not None
        assert yf.op == "between"
        assert yf.year_from == 2020
        assert yf.year_to == 2023

    def test_no_year_returns_none(self):
        assert build_year_filter("Paper tentang stres oksidatif") is None

    def test_invalid_op_rejected(self):
        with pytest.raises(Exception):
            YearFilter(op="eq")  # type: ignore[arg-type]

    def test_extracted_entities_populated(self):
        entities = build_extracted_entities(
            "Berapa total publikasi?",
            FilterParams(year=2025, keyword="stem cell"),
        )
        assert entities["year_filter"] == {"op": "eq", "year": 2025}
        assert entities["keyword"] == "stem cell"

    def test_classify_route_carries_entities(self):
        decision = QuestionRouter.classify_route(
            "Siapa 5 penulis paling produktif tahun 2025?"
        )
        assert decision.route == "SQLRoute"
        assert decision.extracted_entities.get("year_filter") == {"op": "eq", "year": 2025}


class TestInstitutionIndonesiaFix:
    """P0-2: geographic tokens must not poison entity extraction."""

    @pytest.mark.asyncio
    async def test_universitas_indonesia_extracted(self):
        _, inst = EntityResolutionGate.extract_candidate_names(
            "Berapa publikasi dari institusi Universitas Indonesia pada tahun 2025?"
        )
        assert inst is not None
        assert "Indonesia" in inst

    @pytest.mark.asyncio
    async def test_terminator_in_does_not_cut_indonesia(self):
        _, inst = EntityResolutionGate.extract_candidate_names(
            "Institusi mana yang berkolaborasi dengan Universitas Indonesia?"
        )
        assert inst is not None
        assert "Indonesia" in inst

    @pytest.mark.asyncio
    async def test_bare_di_still_ignored(self):
        _, inst = EntityResolutionGate.extract_candidate_names(
            "Paper di Indonesia?"
        )
        assert inst is None

    @pytest.mark.asyncio
    async def test_institution_type_word_is_not_stripped_from_a_proper_noun(self):
        """"Universitas Indonesia" must keep its type word.

        Regression: the extractor consumed "Universitas" as a type keyword and
        captured only the remainder, so "Berapa publikasi Universitas Indonesia
        tahun 2023" resolved the candidate "Indonesia" — a country. The gate
        then partial-matched '%Indonesia%' and returned 10 unrelated candidates
        (needs_clarification) instead of the two that actually contain the
        phrase.
        """
        _, inst = EntityResolutionGate.extract_candidate_names(
            "Berapa publikasi Universitas Indonesia tahun 2023"
        )
        assert inst == "Universitas Indonesia"

    @pytest.mark.asyncio
    async def test_lowercase_type_label_is_stripped(self):
        """A lowercase generic label is a query word, not part of the name."""
        _, inst = EntityResolutionGate.extract_candidate_names(
            "Berapa publikasi institusi Universitas Andalas tahun 2023"
        )
        assert inst == "Universitas Andalas"

    @pytest.mark.asyncio
    async def test_english_verb_terminates_institution_capture(self):
        """Query grammar must not leak into the institution candidate."""
        _, inst = EntityResolutionGate.extract_candidate_names(
            "How many publications did Universitas Gadjah Mada publish in 2025?"
        )
        assert inst == "Universitas Gadjah Mada"

    @pytest.mark.asyncio
    async def test_english_verb_terminator_does_not_truncate_of_names(self):
        """"of" is never a terminator: it occurs inside real institution names."""
        _, inst = EntityResolutionGate.extract_candidate_names(
            "institutions University of Papua in 2025"
        )
        assert inst is None or "of" in inst

    @pytest.mark.asyncio
    async def test_country_word_alone_is_never_an_institution(self):
        """"Paper di Indonesia" must not send a country into the gate."""
        _, inst = EntityResolutionGate.extract_candidate_names(
            "Paper di Indonesia tahun 2023"
        )
        assert inst is None
