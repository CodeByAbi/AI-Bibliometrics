"""SqlRetriever service: Text-to-SQL generation and AST-validated execution.

Docs Reference: docs/05 Retrieval Rag Design.md §5.1, docs/10 Implementation Plan.md §1 (Task 5).
"""

from __future__ import annotations

import re
import time
from typing import Any, ClassVar, Literal

import asyncpg
import httpx
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.config import get_settings
from backend.app.core.errors import DBTimeoutError, LLMTimeoutError
from backend.app.core.http import get_http_client
from backend.app.core.logging import current_request_id, logger
from backend.app.models.ask import FilterParams
from backend.app.services.intent_grammar import (
    AGGREGATE_INTENT_RE as _AGGREGATE_INTENT_RE,
)
from backend.app.services.intent_grammar import (
    INTENT_BY_NAME,
    YearFilter,
    build_year_filter,
    match_intent,
    year_predicate,
)
from backend.app.services.retrievers.sql_security import (
    SqlSecurityError,
    escape_like_pattern,
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
9. NEVER emit $1/$2 placeholders — inline literals only (the deterministic
   template path uses bound params; the LLM path executes with no params).
"""


class SqlRetrievalResult(BaseModel):
    """Result of SQL query execution."""

    model_config = ConfigDict(frozen=True)

    sql_executed: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    filters_ignored: list[str] = Field(
        default_factory=list,
        description="Filter names set by the caller but unused by the executed template",
    )
    #: Which generator produced ``sql_executed`` — ``"deterministic"`` (rule
    #: template, bound params) or ``"llm"`` (Ollama Text-to-SQL). Observable in
    #: developer_mode because the two paths differ by two orders of magnitude
    #: in latency and in whether the AST gate had to reject a first attempt.
    sql_source: Literal["deterministic", "llm"] = "deterministic"
    #: Per-stage wall-clock split (monotonic ``perf_counter``) so a slow
    #: retrieval is attributable to generation, validation, or execution
    #: instead of showing up as one opaque number (NFR4 observability).
    generation_ms: float = 0.0
    validation_ms: float = 0.0
    db_query_ms: float = 0.0
    #: Number of LLM Text-to-SQL invocations spent on this request (0 when the
    #: deterministic template covered the question, 1 on first-try LLM success,
    #: 2 when the AST gate rejected the first attempt and forced a repair).
    llm_calls: int = 0

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
    #: Keywords signalling a computed-number question (FR3.5). Pure ranking
    #: phrasing ("top N ... terbanyak") is excluded: ranked lists over stored
    #: columns need no aggregate function.
    #:
    #: Re-exported from intent_grammar so both grammars stay one source of truth.
    AGGREGATE_INTENT_RE = _AGGREGATE_INTENT_RE

    # --- Deterministic template intent coverage -----------------------------
    #
    # The table now lives in `intent_grammar.INTENT_SPECS`, shared with the
    # router. It used to be a local `INTENT_PATTERNS` dict that drifted away
    # from `QuestionRouter.SQL_PATTERNS` in both directions, which is what let
    # "Top 5 institutions" (template present, route missing -> VectorRoute) and
    # "Berapa publikasi?" (route present, template missing -> 6-21 s Ollama
    # Text-to-SQL) coexist. `intent_grammar.parity_violations()` asserts the two
    # grammars agree, and caught four further silent divergences on introduction.
    @classmethod
    def _intent(cls, intent: str, question: str) -> bool:
        """True when ``question`` expresses ``intent``."""
        return INTENT_BY_NAME[intent].matches(question)

    @classmethod
    def detect_intent(cls, question: str) -> str | None:
        """Canonical name of the deterministic template that will answer this.

        Returns ``None`` when no bounded template covers the question, which is
        the signal to fall back to AST-guarded Text-to-SQL.
        """
        return match_intent(question)

    @classmethod
    def detect_aggregate_intent(cls, question: str) -> bool:
        """Detect whether the question asks for a computed aggregate number."""
        return _AGGREGATE_INTENT_RE.search(question.strip()) is not None

    @classmethod
    def generate_deterministic_sql(
        cls,
        question: str,
        filters: FilterParams | None = None,
        resolved_author_id: str | None = None,
        resolved_institution_id: str | None = None,
        year_filter: YearFilter | None = None,
    ) -> tuple[str | None, list[Any]]:
        """Generate deterministic SQL with bound parameters for canonical questions.

        Returns (sql, params): every user-controlled value travels as a
        bound $n parameter, never interpolated, so filter payloads cannot
        break out of string literals (docs/08 section 2.1).

        Args:
            year_filter: pre-resolved temporal constraint. Callers that already
                hold the router's parse must pass it, otherwise it is derived
                here from ``filters`` then free text. Passing it in is what lets
                the pipeline apply one constraint to all four routes instead of
                each retriever re-parsing the question on its own.
        """
        q = question.strip().lower()

        limit = cls.extract_limit(question)
        params: list[Any] = []

        def _ph(value: Any) -> str:
            params.append(value)
            return f"${len(params)}"

        # Year scoping honors explicit filters first, free text second, and the
        # caller-provided parse first of all.
        #
        # FIX: this used to run a *second* regex (`\b(20\d\d|19\d\d)\b`) that
        # collapsed every operator to equality, so "between 2021 and 2023" —
        # correctly parsed as between by the router — became `p.year = 2021`.
        # That is a silently wrong answer, not a slow one: the prototype corpus
        # holds 21 publications for 2021 and 0 for the whole 2021-2023 window,
        # and the query reported 21. All six operators are now rendered.
        if year_filter is None:
            year_filter = build_year_filter(question, filters)

        year_clauses: list[str] = []
        predicate = year_predicate("p", year_filter, _ph)
        if predicate is not None:
            year_clauses.append(predicate)

        # 1. Top productive authors
        if cls._intent("top_authors", q):
            where_clauses = []
            where_clauses.extend(year_clauses)
            if filters and filters.country:
                where_clauses.append(f"i.country ILIKE {_ph(escape_like_pattern(filters.country))} ESCAPE '\\'")

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
        if cls._intent("most_cited", q):
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
        if cls._intent("count_publications", q):
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
                    where_clauses.append(f"a.author_name ILIKE {_ph(escape_like_pattern(filters.author_name))} ESCAPE '\\'")

            if resolved_institution_id or (filters and filters.institution_name):
                joins += " JOIN pub_institution pi ON pi.publication_id = p.publication_id"
                if not resolved_institution_id and filters and filters.institution_name:
                    joins += " JOIN institutions i ON i.institution_id = pi.institution_id"
                    where_clauses.append(f"i.institution_name ILIKE {_ph(escape_like_pattern(filters.institution_name))} ESCAPE '\\'")

            where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            return f"""
            SELECT COUNT(DISTINCT p.publication_id) AS total_publications
            FROM publications p
            {joins}
            {where_str};
            """, params

        # 3b. Average citations per publication
        if cls._intent("avg_citation_per_publication", q):
            where_str = f"WHERE {' AND '.join(year_clauses)}" if year_clauses else ""
            return f"""
            SELECT COUNT(DISTINCT p.publication_id) AS publications_count,
                   COALESCE(ROUND(AVG(p.citation_count)::numeric, 2), 0) AS avg_citation_count
            FROM publications p
            {where_str};
            """, params

        # 3c. Funding aggregation. Previously this phrasing reached SQLRoute with
        # no template and so fell through to Ollama Text-to-SQL for a question
        # whose answer is a single COUNT over the funding table. Mirrors
        # count_publications in using COUNT(DISTINCT) so a publication with
        # several grant rows is not counted twice.
        if cls._intent("funding_aggregation", q):
            where_clauses = list(year_clauses)
            if filters and filters.institution_name:
                where_clauses.append(
                    f"i.institution_name ILIKE "
                    f"{_ph(escape_like_pattern(filters.institution_name))} ESCAPE '\\'"
                )
            elif resolved_institution_id:
                where_clauses.append(f"pi.institution_id = {_ph(resolved_institution_id)}")

            joins = ""
            if where_clauses != year_clauses or resolved_institution_id:
                joins = (
                    " JOIN funding f ON f.publication_id = p.publication_id"
                    " JOIN pub_institution pi ON pi.publication_id = p.publication_id"
                    " JOIN institutions i ON i.institution_id = pi.institution_id"
                )

            where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            return f"""
            SELECT COUNT(DISTINCT f.funding_id) AS total_grants,
                   COUNT(DISTINCT p.publication_id) AS total_publications
            FROM publications p
            {joins}
            {where_str};
            """, params

        # 4. Top institutions
        if cls._intent("top_institutions", q):
            where_clauses = []
            where_clauses.extend(year_clauses)
            if filters and filters.country:
                where_clauses.append(f"i.country ILIKE {_ph(escape_like_pattern(filters.country))} ESCAPE '\\'")

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
        if cls._intent("list_publications", q):
            where_clauses = []
            joins = ""
            where_clauses.extend(year_clauses)
            if resolved_author_id:
                joins += " JOIN pub_author pa ON pa.publication_id = p.publication_id"
                where_clauses.append(f"pa.author_id = {_ph(resolved_author_id)}")
            elif filters and filters.author_name:
                joins += " JOIN pub_author pa ON pa.publication_id = p.publication_id JOIN authors a ON a.author_id = pa.author_id"
                where_clauses.append(f"a.author_name ILIKE {_ph(escape_like_pattern(filters.author_name))} ESCAPE '\\'")

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
        filters: FilterParams | None = None,
        validation_error: str | None = None,
        timeout_s: float | None = None,
        max_retries: int | None = None,
    ) -> str:
        """Call Ollama LLM to generate Text-to-SQL for arbitrary relational questions.

        Budget (P0-B). Three rules bound the synchronous ``/api/v1/ask`` path:

        1. **One attempt per timeout.** ``retry_on_timeout=False`` makes an
           ``httpx.TimeoutException`` terminal. Before this, a stalled
           generation cost ``timeout + backoff + timeout`` — about 17 s at the
           previous 8 s/2-attempt setting — and the identical retry could not
           possibly succeed, since the same model on the same box needs the
           same wall clock.
        2. **A dedicated, shorter timeout.** Defaults to
           ``TEXT2SQL_TIMEOUT_S`` (6 s) rather than ``OLLAMA_TIMEOUT_S`` (8 s),
           because Text-to-SQL is the only LLM call a request can be blocked on
           without the caller opting in via ``llm_synthesis``.
        3. **A configured retry ceiling.** ``OLLAMA_MAX_RETRIES`` bounds the
           extra attempts, and it is applied to *connect* errors only.

        Raises:
            LLMTimeoutError: the generation exceeded ``timeout_s`` →
                HTTP 504 ``llm_timeout``. Distinct from the 422 below so a slow
                model is never reported as an unsafe question.
            SqlSecurityError: Ollama was unreachable, returned non-200, or
                returned an empty body → HTTP 422 ``sql_generation_failed``.
                Never answers with an unrelated generic query (Phase 3 fix
                P0-3: never hallucinate scope).
        """
        settings = get_settings()
        effective_timeout = (
            settings.text2sql_timeout_s if timeout_s is None else timeout_s
        )
        effective_retries = (
            settings.ollama_max_retries if max_retries is None else max_retries
        )
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
                "explicit allowlisted columns only, no wildcards, no $n placeholders, LIMIT at most 50."
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

        from backend.app.core.retry import with_retry

        async def _post_text2sql():
            # P3 server-*: shared client (TCP keep-alive); timeout stays per-request.
            return await get_http_client().post(
                f"{settings.ollama_host.rstrip('/')}/api/generate",
                json=payload,
                timeout=effective_timeout,
            )

        telemetry: dict[str, Any] = {}
        try:
            resp = await with_retry(
                _post_text2sql,
                max_attempts=1 + max(0, effective_retries),
                operation="ollama-text2sql",
                # A timeout is never retried here — see rule 1 in the docstring.
                retry_on_timeout=False,
                telemetry=telemetry,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            # asyncio.TimeoutError is TimeoutError on 3.11+, so httpx and any
            # outer deadline collapse into one bucket here.
            logger.warning(
                "Text-to-SQL generation timed out after %.1fs "
                "(retry_count=%d, attempts=%d) — returning llm_timeout, "
                "no retry issued",
                effective_timeout,
                max(0, effective_retries) if telemetry.get("retried") else 0,
                telemetry.get("attempts", 1),
                extra={
                    "endpoint": "/api/v1/ask",
                    "route": "SQLRoute",
                    "request_id": current_request_id.get(),
                    "llm_fallback": True,
                    "llm_timeout": True,
                    "retry_count": max(0, telemetry.get("attempts", 1) - 1),
                    "status": "llm_timeout",
                    "latency_ms": telemetry.get("total_ms"),
                },
            )
            raise LLMTimeoutError(
                details={
                    "stage": "text2sql_generation",
                    "timeout_s": effective_timeout,
                    "attempts": telemetry.get("attempts", 1),
                }
            ) from exc
        except SqlSecurityError:
            raise
        except Exception as exc:
            logger.warning(
                "Ollama Text-to-SQL invocation failed after %d attempt(s): %s",
                telemetry.get("attempts", 1),
                exc,
                extra={
                    "endpoint": "/api/v1/ask",
                    "route": "SQLRoute",
                    "request_id": current_request_id.get(),
                    "llm_fallback": True,
                    "llm_timeout": False,
                    "retry_count": max(0, telemetry.get("attempts", 1) - 1),
                    "status": "sql_generation_failed",
                    "latency_ms": telemetry.get("total_ms"),
                },
            )
            raise SqlSecurityError(
                "LLM Text-to-SQL unavailable; cannot answer non-canonical relational question"
            ) from exc

        # Past this point the generator responded, so anything that follows is
        # about the CONTENT of the response, not about waiting for one.
        if resp.status_code != 200:
            logger.warning(
                "Ollama Text-to-SQL call returned HTTP %s",
                resp.status_code,
                extra={
                    "endpoint": "/api/v1/ask",
                    "route": "SQLRoute",
                    "request_id": current_request_id.get(),
                    "llm_fallback": True,
                    "llm_timeout": False,
                    "status": "sql_generation_failed",
                },
            )
            raise SqlSecurityError(
                f"LLM Text-to-SQL unavailable (HTTP {resp.status_code})"
            )

        raw_text = resp.json().get("response", "").strip()
        # Strip any markdown code fence if returned
        raw_text = re.sub(r"^```(?:sql)?\s*", "", raw_text, flags=re.MULTILINE)
        raw_text = re.sub(r"\s*```$", "", raw_text, flags=re.MULTILINE).strip()
        if not raw_text:
            raise SqlSecurityError("LLM Text-to-SQL returned an empty response")
        return raw_text

    @classmethod
    def _reject_bare_placeholders(cls, sql_query: str, params: list[Any]) -> None:
        """Reject LLM output with $n placeholders but no bound values (P1-1).

        Deterministic templates travel with bound params; the LLM path
        executes with params=[] so any $n would fail in asyncpg with a
        raw driver error. Fail fast with a retryable SqlSecurityError
        instead (→ single retry with repair context, else HTTP 422).
        """
        if params:
            return
        if re.search(r"\$\d+", sql_query):
            raise SqlSecurityError(
                "LLM must inline literals; $n placeholders without bound values are forbidden"
            )

    @classmethod
    async def retrieve(
        cls,
        conn: asyncpg.Connection,
        question: str,
        filters: FilterParams | None = None,
        resolved_author_id: str | None = None,
        resolved_institution_id: str | None = None,
        year_filter: YearFilter | None = None,
    ) -> SqlRetrievalResult:
        """Generate, validate, and execute SQL query against database.

        Args:
            year_filter: the router's already-resolved temporal constraint.
                Passing it keeps one parse per request; when omitted the
                deterministic generator derives it from ``filters`` then free
                text, which yields the same value.
        """
        # 1. Try deterministic template generator first (returns bound params).
        #    Timed separately from the LLM fallback so a template regression
        #    shows up as sql_generation_ms growth instead of being hidden
        #    inside one opaque sql_retrieval_ms number.
        t_gen = time.perf_counter()
        sql_query, params = cls.generate_deterministic_sql(
            question,
            filters=filters,
            resolved_author_id=resolved_author_id,
            resolved_institution_id=resolved_institution_id,
            year_filter=year_filter,
        )
        sql_source: Literal["deterministic", "llm"] = "deterministic"
        llm_calls = 0

        # 2. Fall back to LLM Text-to-SQL if not matched deterministically
        if not sql_query:
            sql_source = "llm"
            llm_calls += 1
            sql_query = await cls.generate_llm_sql(question, filters=filters)
            params = []

        generation_ms = round((time.perf_counter() - t_gen) * 1000, 2)

# 3. Validate with the sqlglot AST gate; a single retry carries the
        #    AST error context back to the generator (FR3.3). Persistent
        #    failure raises SqlSecurityError, mapped to HTTP 422 upstream.
        #
        #    The repair round trip is entered ONLY when the first call actually
        #    returned SQL for the validator to reject. A generation that timed
        #    out or never reached Ollama raises LLMTimeoutError /
        #    SqlSecurityError out of step 2 and never gets here — retrying a
        #    generation that produced nothing just pays the same budget twice
        #    and still has nothing to repair.
        intent = cls.detect_aggregate_intent(question)
        t_val = time.perf_counter()
        repair_llm_ms = 0.0
        try:
            cls._reject_bare_placeholders(sql_query, params)
            sanitized_sql = validate_and_sanitize_sql(sql_query, aggregate_intent=intent)
        except SqlSecurityError as first_exc:
            llm_calls += 1
            t_repair = time.perf_counter()
            sql_query = await cls.generate_llm_sql(
                question, filters=filters, validation_error=str(first_exc)
            )
            # Attributed to generation, not validation: it is a second LLM
            # round trip triggered by the gate, and folding it into
            # validation_ms would hide a doubled LLM budget behind a number
            # an operator reads as AST-parse cost.
            repair_llm_ms = (time.perf_counter() - t_repair) * 1000
            params = []
            cls._reject_bare_placeholders(sql_query, params)
            sanitized_sql = validate_and_sanitize_sql(sql_query, aggregate_intent=intent)
        validation_ms = round((time.perf_counter() - t_val) * 1000 - repair_llm_ms, 2)
        generation_ms = round(generation_ms + repair_llm_ms, 2)

        # sql_source is the single most important field for latency triage:
        # "llm" means this request paid a 4.7 GB model load plus CPU decode,
        # measured at 6.0 s warm and 21.9-41.4 s cold on this deployment.
        # llm_calls is the honest attempt count: 0 = deterministic template,
        # 1 = one bounded Text-to-SQL call, 2 = that call plus one AST repair.
        logger.info(
            "SQL generation: source=%s llm_calls=%d generation_ms=%.2f validation_ms=%.2f",
            sql_source,
            llm_calls,
            generation_ms,
            validation_ms,
            extra={
                "endpoint": "/api/v1/ask",
                "route": "SQLRoute",
                "request_id": current_request_id.get(),
                "llm_fallback": sql_source == "llm",
                "llm_timeout": False,
                "retry_count": max(0, llm_calls - 1),
                "status": "retrieved",
            },
        )

        # 4. Execute query on PostgreSQL with bound parameters.
        # Statement timeouts surface as 503 db_timeout, never raw DB errors.
        # Unexpected driver errors are logged with full context server-side
        # and bubble to the generic handler (masked 500 to clients).
        start_t = time.perf_counter()
        try:
            raw_rows = await conn.fetch(sanitized_sql, *params)
        except (TimeoutError, asyncpg.QueryCanceledError) as exc:
            raise DBTimeoutError() from exc
        except Exception as exc:
            logger.error("SQL execution failed: %s | sql=%.200s", exc, sanitized_sql, exc_info=True)
            raise
        elapsed_ms = round((time.perf_counter() - start_t) * 1000, 2)

        # 5. Extract column names and dict rows
        columns: list[str] = []
        rows: list[dict[str, Any]] = []
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
            sql_source=sql_source,
            generation_ms=generation_ms,
            validation_ms=validation_ms,
            db_query_ms=elapsed_ms,
            llm_calls=llm_calls,
        )

    @classmethod
    def unused_filters(
        cls,
        sql_query: str,
        filters: FilterParams | None = None,
        resolved_author_id: str | None = None,
        resolved_institution_id: str | None = None,
    ) -> list[str]:
        """List caller-set filters the executed template did not consume."""
        if not filters:
            return []
        lowered = sql_query.lower()
        ignored: list[str] = []
        # topic_name / document_type / keyword have no Phase 3 SQL template yet.
        # Surfaced honestly instead of silently dropped (FR0.2).
        if filters.topic_name:
            ignored.append("topic_name")
        if filters.document_type:
            ignored.append("document_type")
        if filters.keyword and "keyword" not in lowered:
            ignored.append("keyword")
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
