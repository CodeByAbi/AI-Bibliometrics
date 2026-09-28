"""AST Security Validator and SQL Sanitization Gate for SqlRetriever.

Docs Reference: docs/05 Retrieval Rag Design.md §5.1, docs/08 Security.md §2.
"""

from __future__ import annotations

import re
from typing import Set
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


def validate_and_sanitize_sql(raw_sql: str) -> str:
    """Validate SQL query AST and enforce safety invariants.

    Invariants enforced:
    1. Single statement only (no chained semicolons).
    2. Root statement must be SELECT (or UNION of SELECTs). Prohibits DDL/DML.
    3. All tables referenced must belong to ALLOWED_TABLES whitelist.
    4. Prohibits system catalog tables and dynamic schema traversal.
    5. Prohibits dangerous or internal pg_* functions.
    6. Double-count prevention: Enforces COUNT(DISTINCT ...) when junction tables are joined.
    7. LIMIT enforcement: Injects or clamps LIMIT to at most 50 rows.
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
    # Invariant 5: LIMIT 50 Enforcement
    # Apply limit to root select if not present or exceeds 50
    limit_clause = parsed.args.get("limit")
    if limit_clause is None:
        parsed = parsed.limit(50)
    else:
        try:
            limit_val_expr = limit_clause.expression
            if limit_val_expr is not None:
                limit_val = int(str(limit_val_expr.this))
                if limit_val > 50 or limit_val <= 0:
                    parsed = parsed.limit(50)
        except Exception:
            parsed = parsed.limit(50)

    # Generate sanitized postgres SQL
    sanitized_sql = parsed.sql(dialect="postgres")
    return sanitized_sql
