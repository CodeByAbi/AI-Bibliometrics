"""Unit tests for SqlRetriever generation and SqlAnswerSynthesizer.

Docs Reference: docs/05 Retrieval Rag Design.md §4, §5.1; docs/10 Implementation Plan.md §1 (Task 5).
"""

from __future__ import annotations

import pytest
from backend.app.models.ask import FilterParams
from backend.app.services.retrievers.sql_retriever import (
    SqlRetrievalResult,
    SqlRetriever,
)
from backend.app.services.retrievers.sql_security import SqlSecurityError, escape_like_pattern
from backend.app.services.synthesizer.answer import (
    SqlAnswerSynthesizer,
    format_citation,
)


class _FakeConn:
    """Minimal asyncpg stub capturing fetch calls and returning zero rows."""

    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *params):
        self.calls.append((sql, params))
        return []


class TestSqlRetrieverGenerator:
    """Test deterministic Text-to-SQL rule generator."""

    def test_generate_top_authors_sql(self):
        sql, params = SqlRetriever.generate_deterministic_sql("Siapa 5 penulis paling produktif tahun 2025?")
        assert sql is not None
        assert "FROM authors" in sql
        assert "pub_author" in sql
        assert "p.year = $1" in sql
        assert 2025 in params
        assert "LIMIT 5" in sql

    def test_generate_most_cited_sql(self):
        sql, params = SqlRetriever.generate_deterministic_sql("Tampilkan 10 publikasi dengan sitasi terbanyak")
        assert sql is not None
        assert "FROM publications" in sql
        assert "ORDER BY p.citation_count DESC" in sql
        assert "LIMIT 10" in sql
        assert params == []

    def test_generate_total_publications_sql(self):
        sql, params = SqlRetriever.generate_deterministic_sql(
            "Berapa total publikasi pada tahun 2025?",
            filters=FilterParams(year=2025),
        )
        assert sql is not None
        assert "COUNT(DISTINCT p.publication_id)" in sql
        assert "p.year = $1" in sql
        assert 2025 in params

    def test_generate_top_institutions_sql(self):
        sql, params = SqlRetriever.generate_deterministic_sql("Tampilkan top 5 institusi teratas")
        assert sql is not None
        assert "FROM institutions" in sql
        assert "pub_institution" in sql
        assert "LIMIT 5" in sql
        assert params == []

    def test_filter_values_are_parameterized_not_interpolated(self):
        evil = "' OR '1'='1"
        sql, params = SqlRetriever.generate_deterministic_sql(
            "Berapa total publikasi pada tahun 2025?",
            filters=FilterParams(year=2025, author_name=evil),
        )
        assert sql is not None
        assert evil not in sql
        # LIKE wildcards are escaped and wrapped as a bound %...% pattern
        # so the payload cannot break out of the string literal or act as a wildcard.
        assert escape_like_pattern(evil) in params
        assert "ESCAPE" in sql

    def test_resolved_ids_are_parameterized(self):
        sql, params = SqlRetriever.generate_deterministic_sql(
            "Daftar publikasi pada tahun 2025",
            filters=FilterParams(year=2025),
            resolved_author_id="A1'; DROP TABLE authors; --",
        )
        assert sql is not None
        assert "DROP TABLE" not in sql
        assert "A1'; DROP TABLE authors; --" in params


class TestAggregateIntent:
    """Aggregate-intent detection must fire on computed numbers, not rankings."""

    def test_count_question_is_aggregate(self):
        assert SqlRetriever.detect_aggregate_intent("Berapa total publikasi pada tahun 2025?") is True

    def test_average_question_is_aggregate(self):
        assert SqlRetriever.detect_aggregate_intent("Berapa rata-rata sitasi per tahun?") is True

    def test_ranked_list_is_not_aggregate(self):
        assert SqlRetriever.detect_aggregate_intent("Tampilkan top 5 publikasi dengan sitasi terbanyak") is False

    def test_conceptual_question_is_not_aggregate(self):
        assert SqlRetriever.detect_aggregate_intent("Paper tentang stres oksidatif") is False


class TestExtractLimit:
    """Years must never be mistaken for result limits."""

    def test_bare_number_with_year(self):
        assert SqlRetriever.extract_limit("Siapa 5 penulis paling produktif tahun 2025?") == 5

    def test_year_only_falls_back_to_default(self):
        assert SqlRetriever.extract_limit("Berapa total publikasi pada tahun 2025?") == 10

    def test_explicit_top_phrasing(self):
        assert SqlRetriever.extract_limit("Tampilkan top 7 publikasi terbaik") == 7


class TestYearRangeAndGrouping:
    """Year ranges, normalized GROUP BY, and ignored-filter reporting."""

    def test_year_between_filter(self):
        sql, params = SqlRetriever.generate_deterministic_sql(
            "Berapa total publikasi?",
            filters=FilterParams(year_from=2020, year_to=2023),
        )
        assert sql is not None
        assert "BETWEEN" in sql
        assert params[:2] == [2020, 2023]

    def test_year_from_only(self):
        sql, params = SqlRetriever.generate_deterministic_sql(
            "Berapa total publikasi?",
            filters=FilterParams(year_from=2021),
        )
        assert "p.year >=" in sql
        assert 2021 in params

    def test_explicit_year_beats_free_text_year(self):
        sql, params = SqlRetriever.generate_deterministic_sql(
            "Berapa total publikasi pada tahun 2025?",
            filters=FilterParams(year=2022),
        )
        assert params[0] == 2022
        assert 2025 not in params

    def test_rankings_group_by_normalized_keys(self):
        sql, _ = SqlRetriever.generate_deterministic_sql("Siapa 5 penulis paling produktif tahun 2025?")
        assert "GROUP BY a.author_id, a.author_name_normalized" in sql
        sql, _ = SqlRetriever.generate_deterministic_sql("Tampilkan top 5 institusi teratas")
        assert "GROUP BY i.institution_id, i.institution_name_normalized" in sql

    @pytest.mark.asyncio
    async def test_unused_filters_reported(self):
        result = await SqlRetriever.retrieve(
            _FakeConn(),
            "Berapa total publikasi pada tahun 2025?",
            filters=FilterParams(year=2025, topic_name="Stem Cell", country="indonesia"),
        )
        assert "topic_name" in result.filters_ignored
        assert "country" in result.filters_ignored

    @pytest.mark.asyncio
    async def test_consumed_filters_not_reported(self):
        result = await SqlRetriever.retrieve(
            _FakeConn(),
            "Berapa total publikasi pada tahun 2025?",
            filters=FilterParams(year=2025, author_name="Gumiandari"),
        )
        assert result.filters_ignored == []


class TestBoundedLlmFallback:
    """P0-B: an unsupported SQL question must not enter a retry/backoff stall.

    Before this, ``with_retry(..., max_attempts=2)`` retried on
    ``httpx.TimeoutException``, so a stalled generation cost
    ``timeout + backoff + timeout`` (~17 s at 8 s/2 attempts) and the retry
    could not possibly help.
    """

    @pytest.mark.asyncio
    async def test_timeout_raises_llm_timeout_not_422(self, monkeypatch):
        """A slow generator is 504 llm_timeout, NOT 422 sql_generation_failed.

        The two mean opposite things: 422 says "reword your question", a
        timeout says "the model was too slow". Collapsing them sends an
        operator chasing prompts when the model is the problem.
        """
        import httpx

        from backend.app.core.errors import LLMTimeoutError
        from backend.app.core.http import get_http_client

        class _TimeoutClient:
            calls = 0

            async def post(self, *args, **kwargs):
                type(self).calls += 1
                raise httpx.ReadTimeout("generation stalled")

        monkeypatch.setattr(
            "backend.app.services.retrievers.sql_retriever.get_http_client",
            lambda: _TimeoutClient(),
        )
        with pytest.raises(LLMTimeoutError) as exc_info:
            await SqlRetriever.retrieve(_FakeConn(), "xyzzy unrelated query")

        assert exc_info.value.error_type == "llm_timeout"
        assert exc_info.value.status_code == 504
        # Exactly ONE outbound attempt, not two.
        assert _TimeoutClient.calls == 1

    @pytest.mark.asyncio
    async def test_timeout_does_not_trigger_ast_repair_round_trip(self, monkeypatch):
        """A generation that produced nothing has nothing to repair.

        The FR3.3 repair pass carries the sqlglot error back to the model once.
        It is only meaningful when the first call actually RETURNED SQL for
        the validator to reject. Entering it after a timeout would pay the
        whole generation budget a second time and still have nothing to fix.
        """
        import httpx

        from backend.app.core.errors import LLMTimeoutError
        from backend.app.core.http import get_http_client

        calls = {"n": 0}

        class _TimeoutClient:
            async def post(self, *args, **kwargs):
                calls["n"] += 1
                raise httpx.ReadTimeout("generation stalled")

        monkeypatch.setattr(
            "backend.app.services.retrievers.sql_retriever.get_http_client",
            lambda: _TimeoutClient(),
        )
        with pytest.raises(LLMTimeoutError):
            await SqlRetriever.retrieve(_FakeConn(), "xyzzy unrelated query")
        assert calls["n"] == 1, "timeout must not be followed by a repair retry"

    @pytest.mark.asyncio
    async def test_unreachable_ollama_still_maps_to_422(self, monkeypatch):
        """Connect failure keeps the pre-existing 422 semantic.

        The transport said "I cannot reach the model", not "the model was too
        slow" — the caller is told the question is unanswerable, not slow.
        """
        import httpx

        from backend.app.core.errors import LLMTimeoutError
        from backend.app.core.http import get_http_client

        class _RefusedClient:
            async def post(self, *args, **kwargs):
                raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(
            "backend.app.services.retrievers.sql_retriever.get_http_client",
            lambda: _RefusedClient(),
        )
        with pytest.raises(SqlSecurityError) as exc_info:
            await SqlRetriever.retrieve(_FakeConn(), "xyzzy unrelated query")
        assert not isinstance(exc_info.value, LLMTimeoutError)
        assert exc_info.value.status_code == 422
        assert exc_info.value.error_type == "sql_generation_failed"

    @pytest.mark.asyncio
    async def test_timeout_budget_is_configured_and_bounded(self):
        """The per-attempt budget is the dedicated TEXT2SQL_TIMEOUT_S knob."""
        from backend.app.core.config import get_settings

        settings = get_settings()
        assert settings.text2sql_timeout_s >= 1
        # A single bounded attempt must fit inside the documented request SLA.
        assert settings.text2sql_timeout_s <= 30
        assert settings.ollama_max_retries <= 3

    @pytest.mark.asyncio
    async def test_generated_sql_still_passes_ast_gate_after_the_fix(self, monkeypatch):
        """P0-B must not weaken the security gate on the success path."""
        calls = {"n": 0}

        async def fake_llm(cls, question, filters=None, validation_error=None, **kw):
            calls["n"] += 1
            return "SELECT publication_id, title FROM publications LIMIT 5;"

        monkeypatch.setattr(SqlRetriever, "generate_llm_sql", classmethod(fake_llm))
        conn = _FakeConn()
        result = await SqlRetriever.retrieve(conn, "xyzzy unrelated query")
        assert result.sql_source == "llm"
        assert result.llm_calls == 1
        assert "DROP" not in result.sql_executed
        assert len(conn.calls) == 1

    @pytest.mark.asyncio
    async def test_llm_sql_injection_still_rejected(self, monkeypatch):
        """A hostile generator must not get past the AST gate."""

        async def hostile_llm(cls, question, filters=None, validation_error=None, **kw):
            return "SELECT publication_id FROM publications; DROP TABLE publications;"

        monkeypatch.setattr(
            SqlRetriever, "generate_llm_sql", classmethod(hostile_llm)
        )
        with pytest.raises(SqlSecurityError):
            await SqlRetriever.retrieve(_FakeConn(), "xyzzy unrelated query")

    @pytest.mark.asyncio
    async def test_deterministic_template_never_calls_the_llm(self, monkeypatch):
        """A matched template must stay on the zero-LLM path."""

        async def boom(cls, *args, **kwargs):
            raise AssertionError("deterministic template must not call the LLM")

        monkeypatch.setattr(SqlRetriever, "generate_llm_sql", classmethod(boom))
        result = await SqlRetriever.retrieve(
            _FakeConn(), "Berapa total publikasi pada tahun 2025?"
        )
        assert result.sql_source == "deterministic"
        assert result.llm_calls == 0


class TestSingleRetry:
    """One regeneration carrying AST error context before structured failure (FR3.3)."""

    @pytest.mark.asyncio
    async def test_retry_after_ast_rejection(self, monkeypatch):
        calls = {"n": 0}

        async def fake_llm(cls, question, filters=None, validation_error=None):
            calls["n"] += 1
            if calls["n"] == 1:
                assert validation_error is None
                return "DROP TABLE publications;"
            assert validation_error is not None
            assert "SELECT" in validation_error or "select" in validation_error.lower()
            return "SELECT publication_id, title FROM publications LIMIT 5;"

        monkeypatch.setattr(SqlRetriever, "generate_llm_sql", classmethod(fake_llm))
        conn = _FakeConn()
        result = await SqlRetriever.retrieve(conn, "xyzzy unrelated query")
        assert calls["n"] == 2
        assert "DROP" not in result.sql_executed
        assert "FROM publications" in result.sql_executed
        assert len(conn.calls) == 1

    @pytest.mark.asyncio
    async def test_double_failure_raises_security_error(self, monkeypatch):
        async def always_bad(cls, question, filters=None, validation_error=None):
            return "DELETE FROM authors;"

        monkeypatch.setattr(SqlRetriever, "generate_llm_sql", classmethod(always_bad))
        with pytest.raises(SqlSecurityError):
            await SqlRetriever.retrieve(_FakeConn(), "xyzzy unrelated query")


class TestTimeoutMapping:
    """Statement timeouts surface as structured 503 db_timeout (never raw errors)."""

    @pytest.mark.asyncio
    async def test_statement_timeout_maps_to_db_timeout(self):
        import asyncpg

        from backend.app.core.errors import DBTimeoutError

        class _TimeoutConn:
            async def fetch(self, *args, **kwargs):
                raise asyncpg.QueryCanceledError(
                    "canceling statement due to statement timeout"
                )

        with pytest.raises(DBTimeoutError) as exc_info:
            await SqlRetriever.retrieve(_TimeoutConn(), "Berapa total publikasi pada tahun 2025?")
        assert exc_info.value.error_type == "db_timeout"
        assert exc_info.value.status_code == 503


class TestSqlAnswerSynthesizer:
    """Test SQL result grounding, EvidenceObjects, and zero-match short circuit."""

    def test_zero_match_short_circuit(self):
        empty_result = SqlRetrievalResult(
            sql_executed="SELECT * FROM publications WHERE year = 1900;",
            columns=["publication_id", "title"],
            rows=[],
            row_count=0,
            execution_time_ms=5.0,
        )
        synth = SqlAnswerSynthesizer.synthesize("Publications in 1900", empty_result)
        assert synth.status == "not_found"
        assert "tidak ditemukan" in synth.answer.lower()
        assert len(synth.evidence_objects) == 0
        assert len(synth.sources) == 0

    def test_aggregate_total_publications_synthesis(self):
        res = SqlRetrievalResult(
            sql_executed="SELECT COUNT(DISTINCT publication_id) AS total_publications FROM publications WHERE year = 2025;",
            columns=["total_publications"],
            rows=[{"total_publications": 20}],
            row_count=1,
            execution_time_ms=4.0,
        )
        synth = SqlAnswerSynthesizer.synthesize(
            "Total publikasi 2025",
            res,
            filters=FilterParams(year=2025),
        )
        assert synth.status == "ok"
        assert "20" in synth.answer
        assert len(synth.evidence_objects) == 1
        ev = synth.evidence_objects[0]
        assert ev.metric == "publication_count"
        assert ev.value == 20
        assert ev.confidence == 1.0

    def test_author_rankings_synthesis(self):
        res = SqlRetrievalResult(
            sql_executed="SELECT a.author_name, COUNT(DISTINCT pa.publication_id) AS publication_count FROM authors a ...",
            columns=["author_name", "publication_count"],
            rows=[
                {"author_name": "Septi Gumiandari", "publication_count": 5},
                {"author_name": "Eti Nurhayati", "publication_count": 4},
            ],
            row_count=2,
            execution_time_ms=8.0,
        )
        synth = SqlAnswerSynthesizer.synthesize("Top authors", res)
        assert synth.status == "ok"
        assert "Septi Gumiandari" in synth.answer
        assert len(synth.evidence_objects) == 2
        assert synth.evidence_objects[0].value == 5
        assert synth.evidence_objects[0].confidence == 1.0

    def test_publication_list_synthesis_and_citation_formatting(self):
        res = SqlRetrievalResult(
            sql_executed="SELECT p.publication_id, p.title, p.year, p.doi, p.citation_count FROM publications p ...",
            columns=["publication_id", "title", "year", "doi", "citation_count"],
            rows=[
                {
                    "publication_id": "PUB001",
                    "title": "Stem Cell Therapy",
                    "year": 2025,
                    "doi": "10.1016/j.cell.2025.001",
                    "citation_count": 3,
                },
                {
                    "publication_id": "PUB002",
                    "title": "Traditional Medicine",
                    "year": 2025,
                    "doi": None,
                    "citation_count": 0,
                },
            ],
            row_count=2,
            execution_time_ms=6.0,
        )
        synth = SqlAnswerSynthesizer.synthesize("List publications", res)
        assert synth.status == "ok"
        assert "[Stem Cell Therapy, 2025, 10.1016/j.cell.2025.001]" in synth.answer
        assert "[Traditional Medicine, 2025, no-doi]" in synth.answer
        assert len(synth.sources) == 2
        assert len(synth.evidence_objects) == 2

    def test_format_citation_helper(self):
        assert (
            format_citation("Article Title", 2023, "10.1234/sample.doi")
            == "[Article Title, 2023, 10.1234/sample.doi]"
        )
        assert format_citation("Article Title", 2023, None) == "[Article Title, 2023, no-doi]"
        assert format_citation("Article Title", 2023, "") == "[Article Title, 2023, no-doi]"
        assert format_citation(None, None, None) == "[Untitled, n.d., no-doi]"


class TestLlmFailureHardening:
    """P0-3/P1-1: LLM failures and placeholders must 422, never hallucinate scope."""

    @pytest.mark.asyncio
    async def test_llm_unavailable_raises_not_generic(self, monkeypatch):
        async def boom(cls, question, filters=None, validation_error=None):
            raise SqlSecurityError("LLM Text-to-SQL unavailable")

        monkeypatch.setattr(SqlRetriever, "generate_llm_sql", classmethod(boom))
        with pytest.raises(SqlSecurityError):
            await SqlRetriever.retrieve(_FakeConn(), "xyzzy unrelated query")

    def test_bare_placeholders_rejected_without_params(self):
        with pytest.raises(SqlSecurityError):
            SqlRetriever._reject_bare_placeholders(
                "SELECT publication_id FROM publications WHERE year = $1;", []
            )

    def test_placeholders_allowed_with_params(self):
        SqlRetriever._reject_bare_placeholders(
            "SELECT publication_id FROM publications WHERE year = $1;", [2025]
        )

    @pytest.mark.asyncio
    async def test_llm_placeholders_trigger_retry_then_422(self, monkeypatch):
        async def placeholder_llm(cls, question, filters=None, validation_error=None):
            return "SELECT publication_id, title FROM publications WHERE year = $1 LIMIT 5;"

        monkeypatch.setattr(SqlRetriever, "generate_llm_sql", classmethod(placeholder_llm))
        with pytest.raises(SqlSecurityError):
            await SqlRetriever.retrieve(_FakeConn(), "xyzzy unrelated query")

    @pytest.mark.asyncio
    async def test_keyword_filter_reported_ignored(self):
        result = await SqlRetriever.retrieve(
            _FakeConn(),
            "Berapa total publikasi pada tahun 2025?",
            filters=FilterParams(year=2025, keyword="stem cell"),
        )
        assert "keyword" in result.filters_ignored


class TestSynthesizerPeriodAndGeneric:
    """P1-5 period ranges + P1-3 generic Case E evidence (AC-RAG-2)."""

    def test_period_range_format(self):
        res = SqlRetrievalResult(
            sql_executed="SELECT COUNT(DISTINCT p.publication_id) AS total_publications FROM publications p;",
            columns=["total_publications"],
            rows=[{"total_publications": 7}],
            row_count=1,
            execution_time_ms=3.0,
        )
        synth = SqlAnswerSynthesizer.synthesize(
            "Total publikasi",
            res,
            filters=FilterParams(year_from=2020, year_to=2023),
        )
        assert synth.status == "ok"
        assert synth.evidence_objects[0].period == "2020-2023"

    def test_generic_case_emits_row_count_evidence(self):
        res = SqlRetrievalResult(
            sql_executed="SELECT publisher, source_title FROM publications LIMIT 2;",
            columns=["publisher", "source_title"],
            rows=[
                {"publisher": "elsevier", "source_title": "cell"},
                {"publisher": "springer", "source_title": "nature"},
            ],
            row_count=2,
            execution_time_ms=3.0,
        )
        synth = SqlAnswerSynthesizer.synthesize("Generic query", res)
        assert synth.status == "ok"
        assert len(synth.evidence_objects) == 1
        assert synth.evidence_objects[0].value == 2
