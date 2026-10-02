"""VectorRetriever service: semantic similarity search across publication chunks.

Docs Reference: docs/05 Retrieval Rag Design.md §5.2, docs/10 Implementation Plan.md §1 (Task 6), docs/11 Roadmap.md (Fase 4).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, List, Optional
import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.config import get_settings
from backend.app.core.errors import DBTimeoutError
from backend.app.core.logging import logger
from backend.app.models.ask import FilterParams
from backend.app.services.embedding import (
    generate_query_embedding_with_backend,
    validate_embedding_vector,
)


#: Canonical cosine similarity gate for BAAI/bge-m3 (docs/05 §5.2, FR4.5).
#: Queries below this threshold short-circuit to ``status: not_found``.
COSINE_SIMILARITY_THRESHOLD: float = 0.65

#: Canonical result size: distinct publications returned per query (FR4.4 —
#: deduplication by ``publication_id`` happens BEFORE this limit).
VECTOR_TOP_K: int = 8

#: Hard bounds for the ``threshold``/``limit`` overrides on ``retrieve``.
#: The canonical contract is 0.65/8; out-of-range overrides fail fast
#: instead of silently violating the gate (Phase 4 audit D4).
MIN_THRESHOLD: float = 0.0
MAX_THRESHOLD: float = 1.0
MIN_LIMIT: int = 1
MAX_LIMIT: int = 50


def _escape_like_literal(value: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` so ILIKE filters match literally.

    Mirrors the Graph T3 escape discipline (``ESCAPE '\\'``); every ILIKE
    predicate built here must carry the ``ESCAPE`` clause (Phase 4 audit D4).
    """
    return (
        value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


class VectorMatchItem(BaseModel):
    """A single publication matched via vector chunk similarity."""

    model_config = ConfigDict(frozen=True)

    publication_id: str
    title: str
    year: Optional[int] = None
    doi: Optional[str] = None
    eid: Optional[str] = None
    citation_count: Optional[int] = 0
    chunk_id: str
    chunk_text: str
    similarity_score: float


class VectorRetrievalResult(BaseModel):
    """Result payload of vector similarity retrieval."""

    model_config = ConfigDict(frozen=True)

    matches: List[VectorMatchItem] = Field(default_factory=list)
    threshold: float = COSINE_SIMILARITY_THRESHOLD
    filters_ignored: List[str] = Field(default_factory=list)
    sql_executed: str
    #: Wall-clock ms spent generating the query embedding (None when a
    #: precomputed ``query_vector`` was injected by the caller).
    embedding_ms: Optional[float] = None
    #: Which embedding backend served the query (``"local"`` | ``"ollama"``),
    #: None when a precomputed vector was injected (Phase 4 audit D1).
    embedding_backend: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        """True if no chunk matches exceeded the similarity threshold."""
        return len(self.matches) == 0

    @property
    def match_count(self) -> int:
        """Total number of deduplicated publications matched."""
        return len(self.matches)


class VectorRetriever:
    """Semantic vector search retriever over publication chunks in PostgreSQL/pgvector."""

    DEFAULT_THRESHOLD: float = COSINE_SIMILARITY_THRESHOLD
    DEFAULT_LIMIT: int = VECTOR_TOP_K

    @classmethod
    async def retrieve(
        cls,
        conn: asyncpg.Connection,
        question: str,
        filters: Optional[FilterParams] = None,
        resolved_author_id: Optional[str] = None,
        resolved_institution_id: Optional[str] = None,
        threshold: float = DEFAULT_THRESHOLD,
        limit: int = DEFAULT_LIMIT,
        query_vector: Optional[List[float]] = None,
    ) -> VectorRetrievalResult:
        """Execute semantic search over chunks joined to publications with deduplication.

        Parameters
        ----------
        conn : asyncpg.Connection
            Active PostgreSQL connection.
        question : str
            Natural language user query.
        filters : Optional[FilterParams]
            Metadata filters (year, country, doc_type, etc.).
        resolved_author_id : Optional[str]
            Canonical author_id if resolved by EntityResolutionGate.
        resolved_institution_id : Optional[str]
            Canonical institution_id if resolved by EntityResolutionGate.
        threshold : float
            Cosine similarity threshold (canonical: >= 0.65).
        limit : int
            Maximum distinct publications to return (canonical: 8).
        query_vector : Optional[List[float]]
            Precomputed query embedding vector; if None, generated on-the-fly.
        """
        # 0. Clamp the canonical contract: out-of-range overrides fail fast
        # instead of silently shifting the >= 0.65 gate or the 8-pub limit.
        if not (MIN_THRESHOLD <= float(threshold) <= MAX_THRESHOLD):
            raise ValueError(
                f"threshold must be within [{MIN_THRESHOLD}, {MAX_THRESHOLD}], "
                f"got {threshold!r} (canonical: {COSINE_SIMILARITY_THRESHOLD})."
            )
        if not (MIN_LIMIT <= int(limit) <= MAX_LIMIT):
            raise ValueError(
                f"limit must be within [{MIN_LIMIT}, {MAX_LIMIT}], "
                f"got {limit!r} (canonical: {VECTOR_TOP_K})."
            )

        # 1. Generate query embedding if not provided (timed + attributed
        # for NFR4 observability: embedding cost vs PG cost stay separable).
        embedding_ms: Optional[float] = None
        embedding_backend: Optional[str] = None
        if query_vector is None:
            t_emb = time.perf_counter()
            query_vector, embedding_backend = await generate_query_embedding_with_backend(question)
            embedding_ms = (time.perf_counter() - t_emb) * 1000.0

        # Defense-in-depth: query_vector may be injected directly (tests,
        # callers). Single shared validator — generate_query_embedding already
        # validates its own output, this covers the precomputed path.
        expected_dim = get_settings().embedding_dimension
        assert query_vector is not None  # generation above either returns or raises
        validate_embedding_vector(query_vector, expected_dim)

        # Format pgvector string safely (finite floats only, no user text).
        # NOTE: the vector is interpolated rather than bound as $N because
        # asyncpg has no native pgvector codec here; interpolation is safe
        # de facto since every element is a validated finite float rendered
        # with a fixed numeric format. Threshold/filters/IDs/limit stay
        # parameterized ($N). The schema qualifier comes from the validated
        # ``VECTOR_SCHEMA`` setting (default ``extensions`` = Supabase layout;
        # vanilla local installs use ``public``) — ``Settings`` rejects
        # anything that is not a plain SQL identifier, so no user text can
        # reach this literal through the qualifier either.
        vec_literal = "[" + ",".join(f"{v:.8f}" for v in query_vector) + "]"
        vec_schema = get_settings().vector_schema
        dist_op = f"(c.embedding OPERATOR({vec_schema}.<=>) '{vec_literal}'::{vec_schema}.vector)"

        # 2. Build parameterized filter conditions
        where_clauses: List[str] = [
            "c.embedding IS NOT NULL",
            f"(1 - {dist_op}) >= $1",
        ]
        params: List[Any] = [threshold]
        param_idx = 2
        filters_ignored: List[str] = []

        if filters:
            if filters.year is not None:
                where_clauses.append(f"p.year = ${param_idx}")
                params.append(filters.year)
                param_idx += 1
            if filters.year_from is not None:
                where_clauses.append(f"p.year >= ${param_idx}")
                params.append(filters.year_from)
                param_idx += 1
            if filters.year_to is not None:
                where_clauses.append(f"p.year <= ${param_idx}")
                params.append(filters.year_to)
                param_idx += 1
            if filters.document_type:
                where_clauses.append(f"p.document_type ILIKE ${param_idx} ESCAPE '\\'")
                params.append(f"%{_escape_like_literal(filters.document_type.strip())}%")
                param_idx += 1
            if filters.country:
                where_clauses.append(
                    f"p.publication_id IN ("
                    f"SELECT pi.publication_id FROM pub_institution pi "
                    f"JOIN institutions i ON i.institution_id = pi.institution_id "
                    f"WHERE i.country ILIKE ${param_idx} ESCAPE '\\')"
                )
                params.append(f"%{_escape_like_literal(filters.country.strip())}%")
                param_idx += 1

            # Track ignored filters
            if filters.topic_name:
                filters_ignored.append("topic_name")
            if filters.keyword:
                filters_ignored.append("keyword")
            if filters.author_name and not resolved_author_id:
                filters_ignored.append("author_name")
            if filters.institution_name and not resolved_institution_id:
                filters_ignored.append("institution_name")

        if resolved_author_id:
            where_clauses.append(
                f"p.publication_id IN (SELECT publication_id FROM pub_author WHERE author_id = ${param_idx})"
            )
            params.append(resolved_author_id)
            param_idx += 1

        if resolved_institution_id:
            where_clauses.append(
                f"p.publication_id IN (SELECT publication_id FROM pub_institution WHERE institution_id = ${param_idx})"
            )
            params.append(resolved_institution_id)
            param_idx += 1

        where_sql = "\n              AND ".join(where_clauses)
        params.append(limit)
        limit_param_idx = param_idx

        # 3. Construct deterministic Deduplicated CTE SQL
        sql = f"""
        WITH scored_chunks AS (
            SELECT DISTINCT ON (p.publication_id)
                p.publication_id,
                p.eid,
                p.doi,
                p.title,
                p.year,
                p.citation_count,
                c.chunk_id,
                c.chunk_text,
                1 - {dist_op} AS similarity_score
            FROM chunks c
            JOIN publications p ON p.publication_id = c.publication_id
            WHERE {where_sql}
            ORDER BY p.publication_id, {dist_op} ASC
        )
        SELECT *
        FROM scored_chunks
        ORDER BY similarity_score DESC
        LIMIT ${limit_param_idx};
        """.strip()

        # 4. Execute query with statement timeout protection
        settings = get_settings()
        timeout_s = settings.db_statement_timeout_ms / 1000.0

        t0 = time.perf_counter()
        try:
            rows = await asyncio.wait_for(
                conn.fetch(sql, *params),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError as exc:
            logger.error(
                "Vector retrieval query timed out after %.2fs",
                timeout_s,
                extra={"endpoint": "/api/v1/ask", "route": "VectorRoute"},
            )
            raise DBTimeoutError(
                f"Vector retrieval query timed out after {settings.db_statement_timeout_ms}ms.",
            ) from exc

        query_ms = (time.perf_counter() - t0) * 1000.0

        matches = [
            VectorMatchItem(
                publication_id=str(r["publication_id"]),
                title=str(r["title"] or "Untitled"),
                year=int(r["year"]) if r["year"] is not None else None,
                doi=str(r["doi"]).strip() if r["doi"] and str(r["doi"]).strip() else None,
                eid=str(r["eid"]).strip() if r["eid"] and str(r["eid"]).strip() else None,
                citation_count=int(r["citation_count"]) if r["citation_count"] is not None else 0,
                chunk_id=str(r["chunk_id"]),
                chunk_text=str(r["chunk_text"] or ""),
                similarity_score=float(r["similarity_score"]),
            )
            for r in rows
        ]

        # NFR4 observability: threshold gate outcome + score range per query.
        # Scores stay server-side (only aggregates logged); the raw 1024-d
        # vector is never logged (see [vector_1024d] redaction below).
        if matches:
            scores = [m.similarity_score for m in matches]
            logger.info(
                "Vector retrieval executed in %.2fms: threshold_gate=hit "
                "matches=%d threshold=%.2f score_min=%.4f score_max=%.4f",
                query_ms,
                len(matches),
                threshold,
                min(scores),
                max(scores),
                extra={"endpoint": "/api/v1/ask", "route": "VectorRoute"},
            )
        else:
            logger.info(
                "Vector retrieval executed in %.2fms: threshold_gate=miss "
                "matches=0 threshold=%.2f",
                query_ms,
                threshold,
                extra={"endpoint": "/api/v1/ask", "route": "VectorRoute"},
            )

        # For debug reporting, replace vector literal with a concise token
        debug_sql = sql.replace(vec_literal, "[vector_1024d]")

        return VectorRetrievalResult(
            matches=matches,
            threshold=threshold,
            filters_ignored=filters_ignored,
            sql_executed=debug_sql,
            embedding_ms=embedding_ms,
            embedding_backend=embedding_backend,
        )
