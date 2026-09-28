"""SqlRetriever service: Text-to-SQL generation and AST-validated execution.

Docs Reference: docs/05 Retrieval Rag Design.md §5.1, docs/10 Implementation Plan.md §1 (Task 5).
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx
from pydantic import BaseModel, ConfigDict, Field

import asyncpg
from backend.app.core.config import get_settings
from backend.app.core.logging import logger
from backend.app.models.ask import FilterParams
from backend.app.services.retrievers.sql_security import (
    SqlSecurityError,
    validate_and_sanitize_sql,
)


SCHEMA_PROMPT = """
You are an expert PostgreSQL Text-to-SQL assistant for a scientific bibliometrics database.
Given a user query and optional metadata filters, generate ONLY a single valid PostgreSQL SELECT query.
DO NOT output markdown code blocks (no ```sql), no commentary, and no explanations.

DATABASE SCHEMA:
- publications (publication_id VARCHAR(64) PK, eid VARCHAR(64), doi VARCHAR(255), title TEXT, year SMALLINT, source_title TEXT, volume VARCHAR(32), issue VARCHAR(32), citation_count INT, abstract TEXT, document_type VARCHAR(64), publisher TEXT, link TEXT)
- authors (author_id VARCHAR(64) PK, author_name TEXT, author_name_normalized VARCHAR(255))
- institutions (institution_id VARCHAR(64) PK, institution_name TEXT, city TEXT, country TEXT, institution_name_normalized TEXT)
- keywords (keyword_id BIGINT PK, publication_id VARCHAR(64) FK, keyword TEXT, keyword_type VARCHAR(32))
- funding (funding_id BIGINT PK, publication_id VARCHAR(64) FK, funding_agency TEXT, grant_number TEXT, funding_text TEXT, funding_agency_normalized TEXT)
- pub_author (publication_id VARCHAR(64) FK, author_id VARCHAR(64) FK, author_order SMALLINT)
- pub_institution (publication_id VARCHAR(64) FK, institution_id VARCHAR(64) FK)
- publication_references (reference_id BIGINT PK, publication_id VARCHAR(64) FK, reference_order INT, reference_text TEXT)
- chunks (chunk_id VARCHAR(64) PK, publication_id VARCHAR(64) FK, section TEXT, chunk_text TEXT)

RULES:
1. Always generate a single SELECT statement.
2. For case-insensitive string filtering, use ILIKE '%value%'.
3. When joining junction tables (pub_author, pub_institution, keywords, funding) to count publications, always use COUNT(DISTINCT p.publication_id) or COUNT(DISTINCT pa.publication_id) to prevent double-counting.
4. When filtering by author name, join authors a JOIN pub_author pa ON pa.author_id = a.author_id.
5. When filtering by institution name, join institutions i JOIN pub_institution pi ON pi.institution_id = i.institution_id.
6. Order results logically (e.g. publication_count DESC, citation_count DESC).
7. Non-aggregate queries must have LIMIT <= 50.
8. Output ONLY the raw SQL query.
"""


class SqlRetrievalResult(BaseModel):
    """Result of SQL query execution."""

    model_config = ConfigDict(frozen=True)

    sql_executed: str
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    filters_ignored: List[str] = Field(
        default_factory=list,
        description="Filter names set by the caller but unused by the executed template",
    )

    @property
    def is_empty(self) -> bool:
        return self.row_count == 0


class SqlRetriever:
    """Text-to-SQL generator and AST-guarded execution engine."""

    @classmethod
    def extract_limit(cls, question: str, default: int = 10) -> int:
        """Extract a result limit from free text without mistaking years for limits."""
        q = question.strip().lower()
        # Explicit "top N" phrasing wins when present and in range.
        top_match = re.search(r"\btop\s*(\d+)\b", q)
        if top_match:
            try:
                parsed = int(top_match.group(1))
                if 1 <= parsed <= 50:
                    return parsed
            except ValueError:
                pass
        # Otherwise take the first non-year number in range (e.g. "Siapa 5 penulis ... 2025?").
        for num in re.findall(r"\b(\d+)\b", q):
            try:
                val = int(num)
            except ValueError:
                continue
            if 1900 <= val <= 2026:  # calendar years are filters, not limits
                continue
            if 1 <= val <= 50:
                return val
        return default

    #: Keywords signalling a computed-number question (FR3.5). Pure ranking
    #: phrasing ("top N ... terbanyak") is excluded: ranked lists over stored
    #: columns need no aggregate function.
    AGGREGATE_INTENT_RE = re.compile(
        r"\b(berapa|jumlah|total|hitung|count|how\s+many|rata|rerata|average|mean|"
        r"distribusi|distribution|per\s*tahun|grouped)\b",
        re.IGNORECASE,
    )

    @classmethod
    def detect_aggregate_intent(cls, question: str) -> bool:
        """Detect whether the question asks for a computed aggregate number."""
        return cls.AGGREGATE_INTENT_RE.search(question.strip()) is not None

    @classmethod
    def generate_deterministic_sql(
        cls,
        question: str,
        filters: Optional[FilterParams] = None,
        resolved_author_id: Optional[str] = None,
        resolved_institution_id: Optional[str] = None,
    ) -> Tuple[Optional[str], List[Any]]:
        """Generate deterministic SQL with bound parameters for canonical questions.

        Returns (sql, params): every user-controlled value travels as a
        bound $n parameter, never interpolated, so filter payloads cannot
        break out of string literals (docs/08 section 2.1).
        """
        q = question.strip().lower()

        limit = cls.extract_limit(question)
        params: List[Any] = []

        def _ph(value: Any) -> str:
            params.append(value)
            return f"${len(params)}"

        # Year scoping honors explicit filters first, free-text year second.
        # Operator enums (gt/between/...) are deferred to Fase 7 Hybrid.
        year_clauses: List[str] = []
        f_year = filters.year if filters else None
        f_from = filters.year_from if filters else None
        f_to = filters.year_to if filters else None
        if f_year is not None:
            year_clauses.append(f"p.year = {_ph(f_year)}")
        else:
            if f_from is not None and f_to is not None:
                year_clauses.append(f"p.year BETWEEN {_ph(f_from)} AND {_ph(f_to)}")
            elif f_from is not None:
                year_clauses.append(f"p.year >= {_ph(f_from)}")
            elif f_to is not None:
                year_clauses.append(f"p.year <= {_ph(f_to)}")
            else:
                year_match = re.search(r"\b(20\d\d|19\d\d)\b", q)
                if year_match:
                    year_clauses.append(f"p.year = {_ph(int(year_match.group(1)))}")

        # 1. Top productive authors
        if any(term in q for term in ["penulis paling produktif", "most productive author", "top author", "penulis teratas", "author paling produktif", "most prolific author"]):
            where_clauses = []
            where_clauses.extend(year_clauses)
            if filters and filters.country:
                where_clauses.append(f"i.country ILIKE '%' || {_ph(filters.country)} || '%'")

            where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            # If country filter is present, we need institutions join
            if filters and filters.country:
                return f"""
                SELECT a.author_name, COUNT(DISTINCT pa.publication_id) AS publication_count
                FROM authors a
                JOIN pub_author pa ON pa.author_id = a.author_id
                JOIN publications p ON p.publication_id = pa.publication_id
                JOIN pub_institution pi ON pi.publication_id = p.publication_id
                JOIN institutions i ON i.institution_id = pi.institution_id
                {where_str}
                GROUP BY a.author_id, a.author_name_normalized
                ORDER BY publication_count DESC, a.author_name ASC
                LIMIT {limit};
                """, params
            else:
                return f"""
                SELECT a.author_name, COUNT(DISTINCT pa.publication_id) AS publication_count
                FROM authors a
                JOIN pub_author pa ON pa.author_id = a.author_id
                JOIN publications p ON p.publication_id = pa.publication_id
                {where_str}
                GROUP BY a.author_id, a.author_name_normalized
                ORDER BY publication_count DESC, a.author_name ASC
                LIMIT {limit};
                """, params

        # 2. Most cited publications
        if any(term in q for term in ["sitasi terbanyak", "most cited", "highest citation", "paling banyak disitasi"]):
            where_clauses = []
            where_clauses.extend(year_clauses)
            where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            return f"""
            SELECT p.publication_id, p.title, p.year, p.doi, p.citation_count
            FROM publications p
            {where_str}
            ORDER BY p.citation_count DESC, p.title ASC
            LIMIT {limit};
            """, params

        # 3. Total publications / count
        if any(term in q for term in ["berapa jumlah publikasi", "total publikasi", "how many publications", "count of publications", "total paper", "jumlah paper"]):
            where_clauses = []
            where_clauses.extend(year_clauses)
            if resolved_author_id:
                where_clauses.append(f"pa.author_id = {_ph(resolved_author_id)}")
            if resolved_institution_id:
                where_clauses.append(f"pi.institution_id = {_ph(resolved_institution_id)}")

            joins = ""
            if resolved_author_id or (filters and filters.author_name):
                joins += " JOIN pub_author pa ON pa.publication_id = p.publication_id"
                if not resolved_author_id and filters and filters.author_name:
                    joins += " JOIN authors a ON a.author_id = pa.author_id"
                    where_clauses.append(f"a.author_name ILIKE '%' || {_ph(filters.author_name)} || '%'")

            if resolved_institution_id or (filters and filters.institution_name):
                joins += " JOIN pub_institution pi ON pi.publication_id = p.publication_id"
                if not resolved_institution_id and filters and filters.institution_name:
                    joins += " JOIN institutions i ON i.institution_id = pi.institution_id"
                    where_clauses.append(f"i.institution_name ILIKE '%' || {_ph(filters.institution_name)} || '%'")

            where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            return f"""
            SELECT COUNT(DISTINCT p.publication_id) AS total_publications
            FROM publications p
            {joins}
            {where_str};
            """, params

        # 4. Top institutions
        if any(term in q for term in ["top institusi", "institusi teratas", "top institutions", "most productive institution", "institusi paling produktif"]):
            where_clauses = []
            where_clauses.extend(year_clauses)
            if filters and filters.country:
                where_clauses.append(f"i.country ILIKE '%' || {_ph(filters.country)} || '%'")

            where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            return f"""
            SELECT i.institution_name, COUNT(DISTINCT pi.publication_id) AS publication_count
            FROM institutions i
            JOIN pub_institution pi ON pi.institution_id = i.institution_id
            JOIN publications p ON p.publication_id = pi.publication_id
            {where_str}
            GROUP BY i.institution_id, i.institution_name_normalized
            ORDER BY publication_count DESC, i.institution_name ASC
            LIMIT {limit};
            """, params

        # 5. List publications with filters
        if any(term in q for term in ["daftar publikasi", "list publications", "show publications", "tampilkan publikasi", "artikel pada tahun", "paper in year"]):
            where_clauses = []
            joins = ""
            where_clauses.extend(year_clauses)
            if resolved_author_id:
                joins += " JOIN pub_author pa ON pa.publication_id = p.publication_id"
                where_clauses.append(f"pa.author_id = {_ph(resolved_author_id)}")
            elif filters and filters.author_name:
                joins += " JOIN pub_author pa ON pa.publication_id = p.publication_id JOIN authors a ON a.author_id = pa.author_id"
                where_clauses.append(f"a.author_name ILIKE '%' || {_ph(filters.author_name)} || '%'")

            where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            return f"""
            SELECT p.publication_id, p.title, p.year, p.doi, p.citation_count, p.source_title
            FROM publications p
            {joins}
            {where_str}
            ORDER BY p.citation_count DESC, p.title ASC
            LIMIT {limit};
            """, params

        return None, []

    @classmethod
    async def generate_llm_sql(
        cls,
        question: str,
        filters: Optional[FilterParams] = None,
        validation_error: Optional[str] = None,
    ) -> str:
        """Call Ollama LLM to generate Text-to-SQL for arbitrary relational questions."""
        settings = get_settings()
        filter_context = ""
        if filters:
            f_dict = {k: v for k, v in filters.model_dump().items() if v is not None}
            if f_dict:
                filter_context = f"\nMetadata Filters: {f_dict}"

        repair_context = ""
        if validation_error:
            repair_context = (
                "\nPrevious attempt was rejected by the SQL security gate: "
                f"{validation_error}\nRegenerate a compliant single SELECT query: "
                "explicit allowlisted columns only, no wildcards, LIMIT at most 50."
            )

        user_prompt = f"User Question: {question}{filter_context}{repair_context}\nGenerate SQL query:"

        payload = {
            "model": settings.llm_model,
            "system": SCHEMA_PROMPT,
            "prompt": user_prompt,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 256,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=settings.ollama_timeout_s) as client:
                resp = await client.post(
                    f"{settings.ollama_host.rstrip('/')}/api/generate",
                    json=payload,
                )
                if resp.status_code == 200:
                    raw_text = resp.json().get("response", "").strip()
                    # Strip any markdown code fence if returned
                    raw_text = re.sub(r"^```(?:sql)?\s*", "", raw_text, flags=re.MULTILINE)
                    raw_text = re.sub(r"\s*```$", "", raw_text, flags=re.MULTILINE).strip()
                    return raw_text
                else:
                    logger.warning("Ollama Text-to-SQL call returned HTTP %s", resp.status_code)
        except Exception as exc:
            logger.warning("Ollama Text-to-SQL invocation failed: %s", exc)

        # Fallback default query if Ollama is unreachable
        return "SELECT p.publication_id, p.title, p.year, p.doi, p.citation_count FROM publications p ORDER BY p.citation_count DESC LIMIT 10;"

    @classmethod
    async def retrieve(
        cls,
        conn: asyncpg.Connection,
        question: str,
        filters: Optional[FilterParams] = None,
        resolved_author_id: Optional[str] = None,
        resolved_institution_id: Optional[str] = None,
    ) -> SqlRetrievalResult:
        """Generate, validate, and execute SQL query against database."""
        # 1. Try deterministic template generator first (returns bound params)
        sql_query, params = cls.generate_deterministic_sql(
            question,
            filters=filters,
            resolved_author_id=resolved_author_id,
            resolved_institution_id=resolved_institution_id,
        )

        # 2. Fall back to LLM Text-to-SQL if not matched deterministically
        if not sql_query:
            sql_query = await cls.generate_llm_sql(question, filters=filters)
            params = []

        # 3. Validate with the sqlglot AST gate; a single retry carries the
        # AST error context back to the generator (FR3.3). Persistent
        # failure raises SqlSecurityError, mapped to HTTP 422 upstream.
        intent = cls.detect_aggregate_intent(question)
        try:
            sanitized_sql = validate_and_sanitize_sql(sql_query, aggregate_intent=intent)
        except SqlSecurityError as first_exc:
            sql_query = await cls.generate_llm_sql(
                question, filters=filters, validation_error=str(first_exc)
            )
            params = []
            sanitized_sql = validate_and_sanitize_sql(sql_query, aggregate_intent=intent)

        # 4. Execute query on PostgreSQL with bound parameters
        start_t = time.perf_counter()
        raw_rows = await conn.fetch(sanitized_sql, *params)
        elapsed_ms = round((time.perf_counter() - start_t) * 1000, 2)

        # 5. Extract column names and dict rows
        columns: List[str] = []
        rows: List[Dict[str, Any]] = []
        if raw_rows:
            columns = list(raw_rows[0].keys())
            rows = [dict(r) for r in raw_rows]

        return SqlRetrievalResult(
            sql_executed=sanitized_sql,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_time_ms=elapsed_ms,
            filters_ignored=cls.unused_filters(sql_query, filters, resolved_author_id, resolved_institution_id),
        )

    @classmethod
    def unused_filters(
        cls,
        sql_query: str,
        filters: Optional[FilterParams] = None,
        resolved_author_id: Optional[str] = None,
        resolved_institution_id: Optional[str] = None,
    ) -> List[str]:
        """List caller-set filters the executed template did not consume."""
        if not filters:
            return []
        lowered = sql_query.lower()
        ignored: List[str] = []
        # topic_name / document_type have no Phase 3 SQL template yet.
        if filters.topic_name:
            ignored.append("topic_name")
        if filters.document_type:
            ignored.append("document_type")
        # A name/country filter counts as consumed only via an ILIKE predicate
        # or a resolved canonical binding; bare SELECT/GROUP BY mentions do not count.
        if filters.country and "country ilike" not in lowered:
            ignored.append("country")
        if (
            filters.author_name
            and "author_name ilike" not in lowered
            and not resolved_author_id
        ):
            ignored.append("author_name")
        if (
            filters.institution_name
            and "institution_name ilike" not in lowered
            and not resolved_institution_id
        ):
            ignored.append("institution_name")
        return ignored
