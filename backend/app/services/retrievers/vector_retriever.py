"""VectorRetriever service: semantic similarity search across publication chunks.

Docs Reference: docs/05 Retrieval Rag Design.md §5.2, docs/10 Implementation Plan.md §1 (Task 6), docs/11 Roadmap.md (Fase 4).
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, List, Optional
import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.config import get_settings
from backend.app.core.errors import DBTimeoutError
from backend.app.core.logging import logger
from backend.app.models.ask import FilterParams
from backend.app.services.embedding import EmbeddingError, generate_query_embedding


#: Canonical cosine similarity gate for BAAI/bge-m3 (docs/05 §5.2, FR4.5).
#: Queries below this threshold short-circuit to ``status: not_found``.
COSINE_SIMILARITY_THRESHOLD: float = 0.65

#: Canonical result size: distinct publications returned per query (FR4.4 —
#: deduplication by ``publication_id`` happens BEFORE this limit).
VECTOR_TOP_K: int = 8


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
        # 1. Generate query embedding if not provided
        if query_vector is None:
            query_vector = await generate_query_embedding(question)

        # Defense-in-depth: query_vector may be injected directly (tests,
        # callers). Reject wrong dimensions and NaN/Inf before interpolating
        # into the pgvector literal — generate_query_embedding already
        # validates its own output, this covers the precomputed path.
        expected_dim = get_settings().embedding_dimension
        if len(query_vector) != expected_dim:
            raise EmbeddingError(
                f"Precomputed query embedding dimension mismatch: "
                f"expected {expected_dim}, got {len(query_vector)}.",
                details={"expected": expected_dim, "actual": len(query_vector)},
            )
        for v in query_vector:
            if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                raise EmbeddingError(
                    "Precomputed query embedding contains non-finite value; refusing SQL build."
                )

        # Format pgvector string safely (finite floats only, no user text).
        # NOTE: the vector is interpolated rather than bound as $N because
        # asyncpg has no native pgvector codec here; interpolation is safe
        # de facto since every element is a validated finite float rendered
        # with a fixed numeric format. Threshold/filters/IDs/limit stay
        # parameterized ($N). The `extensions.` schema qualification assumes
        # the `vector` extension lives in the `extensions` schema
        # (Supabase layout); for local installs in `public`, install the
        # extension into `extensions` or adjust the qualifier — never inject
        # user text into this literal.
        vec_literal = "[" + ",".join(f"{v:.8f}" for v in query_vector) + "]"

        # 2. Build parameterized filter conditions
        where_clauses: List[str] = [
            "c.embedding IS NOT NULL",
            f"(1 - (c.embedding OPERATOR(extensions.<=>) '{vec_literal}'::extensions.vector)) >= $1",
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
                where_clauses.append(f"p.document_type ILIKE ${param_idx}")
                params.append(f"%{filters.document_type.strip()}%")
                param_idx += 1
            if filters.country:
                where_clauses.append(
                    f"p.publication_id IN ("
                    f"SELECT pi.publication_id FROM pub_institution pi "
                    f"JOIN institutions i ON i.institution_id = pi.institution_id "
                    f"WHERE i.country ILIKE ${param_idx})"
                )
                params.append(f"%{filters.country.strip()}%")
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
                1 - (c.embedding OPERATOR(extensions.<=>) '{vec_literal}'::extensions.vector) AS similarity_score
            FROM chunks c
            JOIN publications p ON p.publication_id = c.publication_id
            WHERE {where_sql}
            ORDER BY p.publication_id, (c.embedding OPERATOR(extensions.<=>) '{vec_literal}'::extensions.vector) ASC
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
        logger.info(
            "Vector retrieval executed in %.2fms: %d distinct matches found (threshold=%.2f)",
            query_ms,
            len(rows),
            threshold,
        )

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

        # For debug reporting, replace vector literal with a concise token
        debug_sql = sql.replace(vec_literal, "[vector_1024d]")

        return VectorRetrievalResult(
            matches=matches,
            threshold=threshold,
            filters_ignored=filters_ignored,
            sql_executed=debug_sql,
        )
