"""Unit tests for SqlRetriever generation and SqlAnswerSynthesizer.

Docs Reference: docs/05 Retrieval Rag Design.md §4, §5.1; docs/10 Implementation Plan.md §1 (Task 5).
"""

from __future__ import annotations

import pytest
from backend.app.models.ask import FilterParams
from backend.app.services.retrievers.sql_retriever import SqlRetriever


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
        assert evil in params
        assert "ILIKE '%' || $" in sql

    def test_resolved_ids_are_parameterized(self):
        sql, params = SqlRetriever.generate_deterministic_sql(
            "Daftar publikasi pada tahun 2025",
            filters=FilterParams(year=2025),
            resolved_author_id="A1'; DROP TABLE authors; --",
        )
        assert sql is not None
        assert "DROP TABLE" not in sql
        assert "A1'; DROP TABLE authors; --" in params


class TestExtractLimit:
    """Years must never be mistaken for result limits."""

    def test_bare_number_with_year(self):
        assert SqlRetriever.extract_limit("Siapa 5 penulis paling produktif tahun 2025?") == 5

    def test_year_only_falls_back_to_default(self):
        assert SqlRetriever.extract_limit("Berapa total publikasi pada tahun 2025?") == 10

    def test_explicit_top_phrasing(self):
        assert SqlRetriever.extract_limit("Tampilkan top 7 publikasi terbaik") == 7
