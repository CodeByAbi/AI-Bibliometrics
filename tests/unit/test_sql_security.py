"""Unit tests for AST Security Validator and SQL Sanitization Gate.

Docs Reference: docs/05 Retrieval Rag Design.md §5.1, docs/08 Security.md §2.
"""

from __future__ import annotations

import pytest
from backend.app.services.retrievers.sql_security import (
    ALLOWED_TABLES,
    SqlSecurityError,
    validate_and_sanitize_sql,
)


class TestSqlSecurityGate:
    """Test AST security checks and enforcement."""

    def test_valid_select_query(self):
        sql = "SELECT publication_id, title, year FROM publications WHERE year = 2025 ORDER BY citation_count DESC LIMIT 10;"
        sanitized = validate_and_sanitize_sql(sql)
        assert "SELECT" in sanitized
        assert "FROM publications" in sanitized
        assert "LIMIT 10" in sanitized

    def test_enforce_limit_if_missing(self):
        sql = "SELECT * FROM publications;"
        sanitized = validate_and_sanitize_sql(sql)
        assert "LIMIT 50" in sanitized

    def test_clamp_excessive_limit(self):
        sql = "SELECT * FROM publications LIMIT 1000;"
        sanitized = validate_and_sanitize_sql(sql)
        assert "LIMIT 50" in sanitized

    def test_double_count_prevention_on_junction_join(self):
        """Verify COUNT is automatically upgraded to DISTINCT when junction tables are joined."""
        sql = """
        SELECT a.author_name, COUNT(pa.publication_id) AS publication_count
        FROM authors a
        JOIN pub_author pa ON pa.author_id = a.author_id
        GROUP BY a.author_name;
        """
        sanitized = validate_and_sanitize_sql(sql)
        assert "COUNT(DISTINCT pa.publication_id)" in sanitized or "COUNT(DISTINCT" in sanitized

    @pytest.mark.parametrize(
        "destructive_sql,expected_msg",
        [
            ("DROP TABLE publications;", "Only SELECT queries are permitted"),
            ("DELETE FROM authors WHERE author_id = '123';", "Only SELECT queries are permitted"),
            ("UPDATE publications SET title = 'hacked';", "Only SELECT queries are permitted"),
            ("INSERT INTO keywords (keyword) VALUES ('malicious');", "Only SELECT queries are permitted"),
            ("ALTER TABLE publications ADD COLUMN hack TEXT;", "Only SELECT queries are permitted"),
            ("TRUNCATE TABLE chunks;", "Only SELECT queries are permitted"),
            ("GRANT ALL PRIVILEGES ON DATABASE postgres TO public;", "Only SELECT queries are permitted"),
        ],
    )
    def test_reject_destructive_statements(self, destructive_sql: str, expected_msg: str):
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql(destructive_sql)
        assert expected_msg.lower() in str(exc_info.value.message).lower()

    @pytest.mark.parametrize(
        "unauthorized_table_sql",
        [
            "SELECT * FROM pg_tables;",
            "SELECT * FROM information_schema.columns;",
            "SELECT * FROM pg_catalog.pg_user;",
            "SELECT * FROM secret_passwords;",
            "SELECT * FROM publications WHERE publication_id IN (SELECT id FROM unauthorized_table);",
        ],
    )
    def test_reject_unauthorized_tables(self, unauthorized_table_sql: str):
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql(unauthorized_table_sql)
        assert "is forbidden" in str(exc_info.value.message).lower()

    @pytest.mark.parametrize(
        "prohibited_func_sql",
        [
            "SELECT pg_sleep(5) FROM publications;",
            "SELECT pg_read_file('/etc/passwd') FROM publications;",
            "SELECT query_to_xml('SELECT 1', true, false, '') FROM publications;",
            "SELECT current_setting('search_path');",
        ],
    )
    def test_reject_prohibited_functions(self, prohibited_func_sql: str):
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql(prohibited_func_sql)
        assert "prohibited function" in str(exc_info.value.message).lower()

    def test_reject_multiple_statements(self):
        sql = "SELECT * FROM publications; DROP TABLE authors;"
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql(sql)
        assert "multiple statements" in str(exc_info.value.message).lower()

    def test_reject_empty_sql(self):
        with pytest.raises(SqlSecurityError):
            validate_and_sanitize_sql("")
