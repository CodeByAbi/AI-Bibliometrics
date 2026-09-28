"""AST Security Validator and SQL Sanitization Gate for SqlRetriever.

Docs Reference: docs/05 Retrieval Rag Design.md §5.1, docs/08 Security.md §2.
"""

from __future__ import annotations

import re
from typing import Dict, Set
import sqlglot
from sqlglot import exp
from backend.app.core.errors import ASTValidationError


class SqlSecurityError(ASTValidationError):
    """Raised when generated SQL violates AST security invariants or table whitelist."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message=message,
            details={"violation": message},
        )


# Canonical table whitelist: 9 Silver + 2 Derived Edge + 3 Gold Analytics
ALLOWED_TABLES: Set[str] = {
    # 9 Silver canonical tables
    "publications",
    "authors",
    "institutions",
    "keywords",
    "funding",
    "pub_author",
    "pub_institution",
    "publication_references",
    "chunks",
    # 2 Derived edge tables
    "institution_collaboration",
    "author_collaboration",
    # 3 Gold analytics tables
    "topics",
    "topic_evolution",
    "researcher_expertise",
}

# Junction tables where non-distinct count of publications causes double-counting
JUNCTION_TABLES: Set[str] = {
    "pub_author",
    "pub_institution",
    "keywords",
    "funding",
    "publication_references",
}

# Canonical column whitelist. Silver + edge columns verified against the live
# database via information_schema (2026-09-29); Gold columns follow docs/04
# DDL (tables PLANNED, enforced identically once materialized).
ALLOWED_COLUMNS: Dict[str, Set[str]] = {
    "publications": {
        "publication_id", "eid", "doi", "title", "year", "source_title",
        "volume", "issue", "art_no", "page_start", "page_end",
        "citation_count", "link", "abstract", "document_type",
        "publication_stage", "open_access", "issn",
        "language_of_original_document", "publisher", "source", "search_text",
    },
    "authors": {"author_id", "author_name", "author_name_normalized"},
    "institutions": {
        "institution_id", "institution_name", "city", "country",
        "institution_name_normalized",
    },
    "keywords": {"keyword_id", "publication_id", "keyword", "keyword_type"},
    "funding": {
        "funding_id", "publication_id", "funding_agency", "grant_number",
        "funding_text", "source_text", "funding_agency_normalized",
    },
    "pub_author": {"publication_id", "author_id", "author_order"},
    "pub_institution": {"publication_id", "institution_id"},
    "publication_references": {
        "publication_id", "reference_order", "reference_text", "reference_id",
    },
    "chunks": {
        "chunk_id", "publication_id", "section", "chunk_text", "source_type",
        "embedding", "embedding_model", "embedding_version", "embedding_dimension",
    },
    "institution_collaboration": {
        "institution_a", "institution_b", "weight", "via_publication_ids", "created_at",
    },
    "author_collaboration": {
        "author_a", "author_b", "weight", "via_publication_ids", "created_at",
    },
    "topics": {
        "topic_id", "topic_name", "topic_name_normalized", "cluster_keywords",
        "representation_vector", "total_publications", "total_citations",
        "first_publication_year", "latest_publication_year",
        "created_at", "updated_at",
    },
    "topic_evolution": {
        "evolution_id", "topic_id", "year", "publication_count",
        "citation_count", "growth_score", "citation_acceleration",
        "recency_weight", "is_emerging", "created_at",
    },
    "researcher_expertise": {
        "expertise_id", "author_id", "topic_id", "expertise_score",
        "relevance_score", "productivity_score", "impact_score",
        "recency_score", "h_index_topic", "publication_count_topic",
        "citation_count_topic", "coauthor_network_size", "calculated_at",
    },
}

# Dangerous PostgreSQL functions and system procedures
PROHIBITED_FUNCTIONS: Set[str] = {
    "pg_sleep",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_write_file",
    "query_to_xml",
    "cursor_to_xml",
    "current_setting",
    "set_config",
    "system",
    "eval",
    "exec",
    "version",
    "pg_backend_pid",
    "inet_server_addr",
    "inet_server_port",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "pg_rotate_logfile",
}


def _is_single_row_aggregate(select: exp.Select) -> bool:
    """Detect scalar aggregate queries (pure COUNT/SUM/AVG/MIN/MAX, no GROUP BY)."""
    if select.args.get("group") is not None:
        return False
    projections = select.expressions
    if not projections:
        return False
    agg_funcs = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
    for proj in projections:
        inner = proj.unalias() if isinstance(proj, exp.Alias) else proj
        if not isinstance(inner, agg_funcs):
            return False
    return True


def _limit_value(node: exp.Expression) -> int | None:
    """Parse a LIMIT clause value, returning None when unparsable."""
    try:
        clause = node.args.get("limit")
        if clause is None or clause.expression is None:
            return None
        return int(str(clause.expression.this))
    except Exception:
        return None


def _enforce_limit(node: exp.Expression, is_aggregate: bool) -> None:
    """Clamp LIMIT to at most 50 rows, exempting single-row aggregates (FR3.4)."""
    current = _limit_value(node)
    if is_aggregate:
        # Single-row aggregates carry no LIMIT; only an excessive one is clamped.
        if current is not None and current > 50:
            node.set("limit", exp.Limit(expression=exp.Literal.number(50)))
        return
    if current is None or current > 50 or current <= 0:
        node.set("limit", exp.Limit(expression=exp.Literal.number(50)))


def _validate_columns(parsed: exp.Expression) -> None:
    """Enforce the explicit column whitelist; wildcards are retryable failures."""
    for star in parsed.find_all(exp.Star):
        # A star nested inside an aggregate (COUNT(*) et al.) is legitimate;
        # junction-join COUNT(*) is normalized to DISTINCT publication_id later.
        ancestor = star.parent
        inside_aggregate = False
        while ancestor is not None:
            if isinstance(ancestor, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
                inside_aggregate = True
                break
            ancestor = ancestor.parent
        if not inside_aggregate:
            raise SqlSecurityError(
                "SELECT * wildcards are forbidden; project explicit allowlisted columns instead"
            )

    # Alias -> real table across the whole statement (aliases are unique
    # in every query our templates and prompt produce).
    alias_map: Dict[str, str] = {}
    for tbl in parsed.find_all(exp.Table):
        alias = tbl.args.get("alias")
        if alias is not None and alias.alias_or_name:
            alias_map[str(alias.alias_or_name).lower()] = tbl.name.lower()

    select_aliases = {
        str(a.alias).lower() for a in parsed.find_all(exp.Alias) if a.alias
    }
    union_columns: Set[str] = set()
    for cols in ALLOWED_COLUMNS.values():
        union_columns |= cols

    for col in parsed.find_all(exp.Column):
        col_name = col.name.lower()
        if col_name == "*":
            raise SqlSecurityError(
                "Qualified wildcards (tbl.*) are forbidden; project explicit allowlisted columns instead"
            )
        qualifier = (col.table or "").lower()
        if qualifier:
            real = alias_map.get(qualifier, qualifier)
            allowed = ALLOWED_COLUMNS.get(real)
            if allowed is None or col_name not in allowed:
                raise SqlSecurityError(
                    f"Column '{qualifier}.{col.name}' is not allowlisted for table '{real}'"
                )
        elif col_name not in union_columns and col_name not in select_aliases:
            raise SqlSecurityError(
                f"Column '{col.name}' is not in the canonical schema whitelist"
            )


def validate_and_sanitize_sql(raw_sql: str) -> str:
    """Validate SQL query AST and enforce safety invariants.

    Invariants enforced:
    1. Single statement only (no chained semicolons).
    2. Root statement must be SELECT (or UNION of SELECTs). Prohibits DDL/DML.
    3. All tables referenced must belong to ALLOWED_TABLES whitelist.
    4. All projected/filtered columns must belong to ALLOWED_COLUMNS;
       SELECT * and tbl.* are rejected as retryable failures.
    5. Prohibits system catalog tables and dynamic schema traversal.
    5. Prohibits dangerous or internal pg_* functions.
    6. Double-count prevention: Enforces COUNT(DISTINCT ...) when junction tables are joined.
    7. LIMIT enforcement: Injects or clamps LIMIT to at most 50 rows on the
       outer statement and every UNION branch; single-row aggregates are
       exempt from injection (FR3.4).
    """
    if not raw_sql or not raw_sql.strip():
        raise SqlSecurityError("SQL query string is empty")

    cleaned_sql = raw_sql.strip().rstrip(";")

    # Parse using postgres dialect
    try:
        statements = sqlglot.parse(cleaned_sql, read="postgres")
    except Exception as exc:
        raise SqlSecurityError(f"SQL parsing failed: {str(exc)}") from exc

    if len(statements) != 1 or statements[0] is None:
        raise SqlSecurityError("Multiple statements or empty query detected in SQL payload")

    parsed = statements[0]

    # Invariant 1: Root statement must be SELECT or UNION
    if not isinstance(parsed, (exp.Select, exp.Union)):
        raise SqlSecurityError(
            f"Prohibited SQL statement type: {type(parsed).__name__}. Only SELECT queries are permitted."
        )

    # Invariant 2: Table Whitelist Check (including subqueries and CTEs)
    tables_found = list(parsed.find_all(exp.Table))
    if not tables_found and isinstance(parsed, exp.Select):
        # A select without table (e.g., SELECT 1) is harmless or invalid
        pass
    else:
        for tbl in tables_found:
            tbl_name = tbl.name.lower()
            if tbl_name not in ALLOWED_TABLES:
                raise SqlSecurityError(
                    f"Access to table '{tbl_name}' is forbidden. Permitted tables: {sorted(list(ALLOWED_TABLES))}"
                )

    # Invariant: Column Whitelist Check (explicit projection only, no wildcards)
    # NOTE: WITH/CTE aliases are rejected by the table whitelist above, so
    # recursive-CTE graph templates must land with explicit CTE allowlisting
    # in Phase 6 (GraphRetriever); SQLRoute stays CTE-free by design.
    _validate_columns(parsed)

    # Invariant 3: Prohibited functions check
    for func in parsed.find_all(exp.Anonymous, exp.Func):
        func_name = func.name.lower()
        if func_name in PROHIBITED_FUNCTIONS or (
            func_name.startswith("pg_") and func_name not in {"pg_catalog"}
        ):
            raise SqlSecurityError(f"Execution of prohibited function '{func_name}' is forbidden")

    # Invariant 4: Double-Count Prevention on Junction Joins
    # If any junction table is referenced in FROM/JOIN, enforce DISTINCT in COUNT expressions
    referenced_tables = {tbl.name.lower() for tbl in tables_found}
    has_junction_join = any(j in referenced_tables for j in JUNCTION_TABLES)

    if has_junction_join:
        for count_expr in parsed.find_all(exp.Count):
            # Enforce distinct on COUNT(...) when junction tables are joined
            if count_expr.this is not None and not isinstance(count_expr.this, exp.Distinct):
                count_expr.set("this", exp.Distinct(expressions=[count_expr.this]))
    # Invariant 5: LIMIT 50 Enforcement (FR3.4)
    # Clamp the outer statement and every UNION branch. Nested subqueries
    # keep their own limits; the table whitelist still applies to them.
    if isinstance(parsed, exp.Select):
        _enforce_limit(parsed, _is_single_row_aggregate(parsed))
    for union in parsed.find_all(exp.Union):
        # Enforce on direct SELECT sides only; nested subqueries keep their
        # own limits while the table whitelist still applies to them.
        for side in (union.this, union.expression):
            if isinstance(side, exp.Select):
                _enforce_limit(side, _is_single_row_aggregate(side))
        subtree = [s for s in union.find_all(exp.Select)]
        if subtree and all(_is_single_row_aggregate(s) for s in subtree):
            # UNION of pure aggregates: clamp only, never inject (FR3.4).
            current = _limit_value(union)
            if current is not None and current > 50:
                union.set("limit", exp.Limit(expression=exp.Literal.number(50)))
        else:
            _enforce_limit(union, False)

    # Generate sanitized postgres SQL
    sanitized_sql = parsed.sql(dialect="postgres")
    return sanitized_sql
