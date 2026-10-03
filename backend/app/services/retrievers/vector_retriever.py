"""VectorRetriever service: semantic similarity search across publication chunks.

Docs Reference: docs/05 Retrieval Rag Design.md §5.2, docs/10 Implementation Plan.md §1 (Task 6), docs/11 Roadmap.md (Fase 4).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

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

#: ANN overfetch: how many nearest chunks the HNSW scan returns before
#: per-publication dedup collapses them. ``DISTINCT ON (publication_id)`` throws
#: away every duplicate of a publication, so a bare ``LIMIT 8`` over chunks
#: would frequently dedup down to fewer than 8 publications. Overfetching and
#: then reducing is the only shape that keeps the index-driven distance ordering
#: AND returns a full set of distinct publications.
ANN_OVERFETCH_MULTIPLIER: int = 25
MIN_ANN_CANDIDATES: int = 100
MAX_ANN_CANDIDATES: int = 2000

#: ``hnsw.ef_search`` candidate-list size per probe. The pgvector default (40)
#: is sized for a plain ``LIMIT k`` scan; once dedup runs on top of the ANN
#: result the effective top-k shrinks, so the search list is widened well past
#: the candidate count to hold recall.
HNSW_EF_SEARCH: int = 100

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
    year: int | None = None
    doi: str | None = None
    eid: str | None = None
    citation_count: int | None = 0
    chunk_id: str
    chunk_text: str
    similarity_score: float


class VectorRetrievalResult(BaseModel):
    """Result payload of vector similarity retrieval."""

    model_config = ConfigDict(frozen=True)

    matches: list[VectorMatchItem] = Field(default_factory=list)
    threshold: float = COSINE_SIMILARITY_THRESHOLD
    filters_ignored: list[str] = Field(default_factory=list)
    sql_executed: str
    #: Wall-clock ms spent generating the query embedding (None when a
    #: precomputed ``query_vector`` was injected by the caller).
    embedding_ms: float | None = None
    #: Which embedding backend served the query (``"local"`` | ``"ollama"``),
    #: None when a precomputed vector was injected (Phase 4 audit D1).
    embedding_backend: str | None = None

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
    def build_query(
        cls,
        *,
        query_vector: list[float],
        threshold: float,
        limit: int,
        filters: FilterParams | None = None,
        resolved_author_id: str | None = None,
        resolved_institution_id: str | None = None,
    ) -> tuple[str, list[Any], list[str]]:
        """Build the ANN retrieval SQL and its bound parameters.

        Returns ``(sql, params, filters_ignored)``. Exposed separately from
        :meth:`retrieve` so plan-verification tooling (and the live EXPLAIN
        test) can inspect the exact statement the request path executes instead
        of a hand-copied paraphrase that can drift from it.

        Shape: an index-driven ANN window (``ann_candidates``) ordered by the
        cosine operator, then per-publication dedup and the cosine gate
        (``scored_chunks``), then the final top-N. The two stages must stay
        separate — folding dedup into the ANN scan puts a ``publication_id``
        sort in front of the operator, which the HNSW index cannot serve.

        Placeholder order: ``$1`` query vector, ``$2`` ANN overfetch,
        ``$3`` cosine threshold, ``$4`` result limit, ``$5..`` filters.
        """
        # Render the pgvector literal for transport. It is BOUND as $1, never
        # interpolated into the SQL text: asyncpg receives a `vector` codec from
        # `pool._init_connection`, and binding matters because the operator
        # appears in both the projection and the ORDER BY of the ANN scan —
        # inlining a 1024-d literal twice would add ~22KB of query text to every
        # parse/plan round trip. Callers must still pass a validated finite-float
        # sequence (see `validate_embedding_vector`); the codec is a transport
        # detail, not a validation boundary.
        vec_literal = "[" + ",".join(f"{v:.8f}" for v in query_vector) + "]"
        vec_schema = get_settings().vector_schema
        dist_op = f"(c.embedding OPERATOR({vec_schema}.<=>) $1::{vec_schema}.vector)"

        filter_clauses: list[str] = []
        filter_params: list[Any] = []
        filters_ignored: list[str] = []

        if filters:
            if filters.year is not None:
                filter_clauses.append("p.year = ${n}")
                filter_params.append(filters.year)
            if filters.year_from is not None:
                filter_clauses.append("p.year >= ${n}")
                filter_params.append(filters.year_from)
            if filters.year_to is not None:
                filter_clauses.append("p.year <= ${n}")
                filter_params.append(filters.year_to)
            if filters.document_type:
                filter_clauses.append("p.document_type ILIKE ${n} ESCAPE '\\'")
                filter_params.append(
                    f"%{_escape_like_literal(filters.document_type.strip())}%"
                )
            if filters.country:
                filter_clauses.append(
                    "p.publication_id IN ("
                    "SELECT pi.publication_id FROM pub_institution pi "
                    "JOIN institutions i ON i.institution_id = pi.institution_id "
                    "WHERE i.country ILIKE ${n} ESCAPE '\\')"
                )
                filter_params.append(
                    f"%{_escape_like_literal(filters.country.strip())}%"
                )

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
            filter_clauses.append(
                "p.publication_id IN (SELECT publication_id FROM pub_author "
                "WHERE author_id = ${n})"
            )
            filter_params.append(resolved_author_id)

        if resolved_institution_id:
            filter_clauses.append(
                "p.publication_id IN (SELECT publication_id FROM pub_institution "
                "WHERE institution_id = ${n})"
            )
            filter_params.append(resolved_institution_id)

        ann_candidates = min(
            max(int(limit) * ANN_OVERFETCH_MULTIPLIER, MIN_ANN_CANDIDATES),
            MAX_ANN_CANDIDATES,
        )

        numbered_filters: list[str] = [
            clause.format(n=5 + offset) for offset, clause in enumerate(filter_clauses)
        ]
        ann_where = "\n              AND ".join(
            ["c.embedding IS NOT NULL", *numbered_filters]
        )

        sql = f"""
        WITH ann_candidates AS (
            SELECT p.publication_id,
                   p.eid,
                   p.doi,
                   p.title,
                   p.year,
                   p.citation_count,
                   c.chunk_id,
                   c.chunk_text,
                   {dist_op} AS distance
            FROM chunks c
            JOIN publications p ON p.publication_id = c.publication_id
            WHERE {ann_where}
            ORDER BY {dist_op} ASC
            LIMIT $2
        ),
        scored_chunks AS (
            SELECT DISTINCT ON (ac.publication_id)
                ac.publication_id,
                ac.eid,
                ac.doi,
                ac.title,
                ac.year,
                ac.citation_count,
                ac.chunk_id,
                ac.chunk_text,
                1 - ac.distance AS similarity_score
            FROM ann_candidates ac
            WHERE (1 - ac.distance) >= $3
            ORDER BY ac.publication_id, ac.distance ASC
        )
        SELECT *
        FROM scored_chunks
        ORDER BY similarity_score DESC
        LIMIT $4;
        """.strip()

        params: list[Any] = [
            vec_literal,
            ann_candidates,
            float(threshold),
            int(limit),
            *filter_params,
        ]
        return sql, params, filters_ignored

    @classmethod
    async def retrieve(
        cls,
        conn: asyncpg.Connection,
        question: str,
        filters: FilterParams | None = None,
        resolved_author_id: str | None = None,
        resolved_institution_id: str | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        limit: int = DEFAULT_LIMIT,
        query_vector: list[float] | None = None,
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
        embedding_ms: float | None = None
        embedding_backend: str | None = None
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

        sql, params, filters_ignored = cls.build_query(
            query_vector=query_vector,
            threshold=float(threshold),
            limit=int(limit),
            filters=filters,
            resolved_author_id=resolved_author_id,
            resolved_institution_id=resolved_institution_id,
        )

        # 3. Execute query with statement timeout protection
        settings = get_settings()
        timeout_s = settings.db_statement_timeout_ms / 1000.0

        # Widen the HNSW candidate list for this connection. Session-level (not
        # SET LOCAL) because the pool runs in autocommit, where SET LOCAL is a
        # no-op; the value is a workload constant, so persisting it on the
        # pooled connection is harmless and saves a round trip per query.
        # Tolerated as best-effort: a server without pgvector has no such GUC
        # and must fail later on the vector column itself, not here.
        try:
            await conn.execute(
                "SELECT set_config('hnsw.ef_search', $1, false)",
                str(HNSW_EF_SEARCH),
            )
        except Exception as exc:  # noqa: BLE001 - best-effort tuning only
            logger.debug("Could not set hnsw.ef_search: %s", exc)

        t0 = time.perf_counter()
        try:
            rows = await asyncio.wait_for(
                conn.fetch(sql, *params),
                timeout=timeout_s,
            )
        except TimeoutError as exc:
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
        # vector is never logged — it is a bound parameter, so it cannot reach
        # the SQL text that feeds debug reporting in the first place.
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

        return VectorRetrievalResult(
            matches=matches,
            threshold=threshold,
            filters_ignored=filters_ignored,
            sql_executed=sql,
            embedding_ms=embedding_ms,
            embedding_backend=embedding_backend,
        )
