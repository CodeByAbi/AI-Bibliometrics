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

#: Cosine similarity gate for BAAI/bge-m3 (docs/05 §5.2, FR4.5). Queries below
#: this threshold short-circuit to ``status: not_found``.
#:
#: Recalibrated 0.65 -> 0.48 against the labelled benchmark in
#: ``tests/fixtures/retrieval_benchmark_v1.json``; see
#: ``reports/retrieval_calibration.md``. 0.65 was measurably too aggressive:
#: bge-m3 scores natural-language questions around 0.42-0.57, so the old gate
#: answered ``not_found`` for 59.5% of queries the corpus could answer. 0.48 is
#: the measured negative ceiling (the highest score any strict-absence negative
#: reached), which is why it admits evidence without admitting a negative.
#: Mirrored by ``Settings.vector_cosine_threshold``; keep the two in sync.
COSINE_SIMILARITY_THRESHOLD: float = 0.48

#: Canonical result size: distinct publications returned per query (FR4.4 —
#: deduplication by ``publication_id`` happens BEFORE this limit). Mirrored by
#: ``Settings.vector_top_k`` so the value is retunable per deployment.
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
#: The deployed contract is 0.48/8 (see ``resolve_operating_point``);
#: out-of-range overrides fail fast instead of silently violating the gate.
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
    #: Retrieval diagnostics for developer_mode (docs/05 §5.2 observability).
    #: Exists so a zero-evidence answer can be classified instead of collapsed
    #: into "data not found": ``candidate_rows``/``top_similarity``/
    #: ``threshold`` separate "the corpus has nothing on this topic" (low top
    #: similarity) from "the right chunk was retrieved but the cosine gate is
    #: calibrated above this embedding model's score range" (high top
    #: similarity, still dropped).
    diagnostics: dict[str, Any] = Field(default_factory=dict)

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
    def resolve_operating_point(
        cls,
        threshold: float | None = None,
        limit: int | None = None,
    ) -> tuple[float, int]:
        """Resolve the ``(threshold, limit)`` pair actually used for a query.

        ``None`` on either argument means "use the deployed value" from
        ``Settings``, so the operating point can be retuned per deployment
        without a code change. Explicit values are range-checked instead of
        clamped: an out-of-range override is a caller bug, and silently
        clamping it would hide a broken gate behind a working query.
        """
        settings = get_settings()
        effective_threshold = (
            settings.vector_cosine_threshold
            if threshold is None
            else float(threshold)
        )
        effective_limit = settings.vector_top_k if limit is None else int(limit)
        if not (MIN_THRESHOLD <= effective_threshold <= MAX_THRESHOLD):
            raise ValueError(
                f"threshold must be within [{MIN_THRESHOLD}, {MAX_THRESHOLD}], "
                f"got {effective_threshold!r} "
                f"(deployed: {COSINE_SIMILARITY_THRESHOLD})."
            )
        if not (MIN_LIMIT <= effective_limit <= MAX_LIMIT):
            raise ValueError(
                f"limit must be within [{MIN_LIMIT}, {MAX_LIMIT}], "
                f"got {effective_limit!r} (deployed: {VECTOR_TOP_K})."
            )
        return effective_threshold, effective_limit

    @classmethod
    def ann_window(cls, limit: int) -> int:
        """Size of the index-driven ANN window for a requested result count.

        Shared by :meth:`build_query` (which binds it as ``$2``) and by the
        diagnostics block in :meth:`retrieve` (which reports it), so the number
        an operator sees can never drift from the number that was queried.
        """
        return min(
            max(int(limit) * ANN_OVERFETCH_MULTIPLIER, MIN_ANN_CANDIDATES),
            MAX_ANN_CANDIDATES,
        )

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

        ``gate_stats`` aggregates the ANN window a second time so the response
        can report ``top_similarity``/``candidate_rows`` even when the gate
        drops every row. It reads an already materialized CTE, so it adds no
        extra index scan. It drives the FROM clause with a ``LEFT JOIN
        LATERAL`` over the gated rows, so a total miss still returns exactly
        one all-NULL row carrying the stats — without which a zero-evidence
        answer would be indistinguishable from "nothing in the corpus
        resembles this query".

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

        ann_candidates = cls.ann_window(int(limit))

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
        ),
        gate_stats AS (
            SELECT MAX(1 - distance) AS top_similarity,
                   COUNT(*) AS candidate_rows
            FROM ann_candidates
        )
        SELECT sc.*, gs.top_similarity, gs.candidate_rows
        FROM gate_stats gs
        LEFT JOIN LATERAL (
            SELECT * FROM scored_chunks
            ORDER BY similarity_score DESC
            LIMIT $4
        ) sc ON TRUE
        ORDER BY sc.similarity_score DESC;
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
        threshold: float | None = None,
        limit: int | None = None,
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
        threshold : Optional[float]
            Cosine similarity threshold. ``None`` uses the deployed
            ``VECTOR_COSINE_THRESHOLD`` (0.48). Out-of-range values fail fast
            rather than silently shifting the gate.
        limit : Optional[int]
            Maximum distinct publications to return. ``None`` uses
            ``VECTOR_TOP_K`` (8).
        query_vector : Optional[List[float]]
            Precomputed query embedding vector; if None, generated on-the-fly.
        """
        # 0. Resolve the operating point: an explicit override is range-checked,
        #    while ``None`` defers to the deployed ``VECTOR_COSINE_THRESHOLD`` /
        #    ``VECTOR_TOP_K`` so the gate and the result size can be retuned per
        #    deployment without a code change.
        threshold, limit = cls.resolve_operating_point(threshold, limit)

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
        ann_candidates = cls.ann_window(int(limit))

        # 3. Execute query with statement timeout protection
        settings = get_settings()
        timeout_s = settings.db_statement_timeout_ms / 1000.0

        # REMOVED: the per-request `set_config('hnsw.ef_search', ...)` that used
        # to run here.
        #
        # It cost 29-54 ms of measured round trips on every VectorRoute query
        # and had no effect on any observed plan, because at the prototype
        # corpus size (40 chunks) the planner chooses `Seq Scan + Sort` and never
        # touches the HNSW index. It also leaked: the pool releases connections
        # with `reset="light"`, which does not clear session state, so the GUC
        # survived across requests on the same connection.
        #
        # It is now set once per connection in `db.pool.create_pool`
        # (`server_settings["hnsw.ef_search"]`), so it costs one round trip per
        # connection lifetime instead of one per request, and the pool's
        # `reset="full"` guarantees it cannot accumulate session state.

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

        # A total gate miss arrives as one all-NULL row (the LEFT JOIN LATERAL
        # keeps the gate_stats payload). Split it off before building matches so
        # the stats are read once and a NULL row never becomes evidence.
        gate_row = rows[0] if rows else None
        gated_rows = [r for r in rows if r["publication_id"] is not None]
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
            for r in gated_rows
        ]

        # NFR4 observability: threshold gate outcome + score range per query.
        # Scores stay server-side (only aggregates logged); the raw 1024-d
        # vector is never logged — it is a bound parameter, so it cannot reach
        # the SQL text that feeds debug reporting in the first place.
        scores = [m.similarity_score for m in matches]
        top_similarity = max(scores) if scores else None
        ann_candidate_rows: int | None = (
            int(gate_row["candidate_rows"])
            if gate_row is not None and gate_row["candidate_rows"] is not None
            else None
        )
        if top_similarity is None and gate_row is not None:
            reported_top = gate_row["top_similarity"]
            if reported_top is not None:
                top_similarity = float(reported_top)

        diagnostics: dict[str, Any] = {
            "embedding_model": settings.embedding_model,
            "embedding_dimension": expected_dim,
            "embedding_backend": embedding_backend,
            "embedding_ms": None if embedding_ms is None else round(embedding_ms, 2),
            "vector_schema": settings.vector_schema,
            "threshold": float(threshold),
            "top_k": int(limit),
            "ann_candidate_window": ann_candidates,
            "candidate_rows": ann_candidate_rows,
            "rows_after_threshold": len(gated_rows),
            "unique_publications": len(matches),
            "top_similarity": top_similarity,
            "top_similarity_delta": (
                None if top_similarity is None else round(top_similarity - float(threshold), 4)
            ),
            "vector_query_ms": round(query_ms, 2),
            "filters_ignored": list(filters_ignored),
        }

        if matches:
            logger.info(
                "Vector retrieval executed in %.2fms: threshold_gate=hit "
                "matches=%d threshold=%.2f score_min=%.4f score_max=%.4f "
                "candidate_rows=%s",
                query_ms,
                len(matches),
                threshold,
                min(scores),
                max(scores),
                ann_candidate_rows,
                extra={"endpoint": "/api/v1/ask", "route": "VectorRoute"},
            )
        else:
            # A miss with a high top_similarity is a calibration problem, not an
            # absence problem. Say so in the log, where it is greppable.
            logger.info(
                "Vector retrieval executed in %.2fms: threshold_gate=miss "
                "matches=0 threshold=%.2f candidate_rows=%s top_similarity=%s "
                "gate_verdict=%s",
                query_ms,
                threshold,
                ann_candidate_rows,
                "none" if top_similarity is None else f"{top_similarity:.4f}",
                "no_candidates" if top_similarity is None else "below_threshold",
                extra={"endpoint": "/api/v1/ask", "route": "VectorRoute"},
            )

        return VectorRetrievalResult(
            matches=matches,
            threshold=threshold,
            filters_ignored=filters_ignored,
            sql_executed=sql,
            embedding_ms=embedding_ms,
            embedding_backend=embedding_backend,
            diagnostics=diagnostics,
        )
