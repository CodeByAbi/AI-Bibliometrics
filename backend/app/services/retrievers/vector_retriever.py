"""VectorRetriever service: semantic similarity search across publication chunks.

Docs Reference: docs/05 Retrieval Rag Design.md §5.2, docs/10 Implementation Plan.md §1 (Task 6), docs/11 Roadmap.md (Fase 4).
"""

from __future__ import annotations

from typing import Any, List, Optional
import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from backend.app.models.ask import FilterParams
from backend.app.services.embedding import generate_query_embedding


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
    threshold: float = 0.65
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

    @classmethod
    async def retrieve(
        cls,
        conn: asyncpg.Connection,
        question: str,
        query_vector: Optional[List[float]] = None,
    ) -> VectorRetrievalResult:
        """Execute semantic search over chunks joined to publications with deduplication.

        Parameters
        ----------
        conn : asyncpg.Connection
            Active PostgreSQL connection.
        question : str
            Natural language user query.
        query_vector : Optional[List[float]]
            Precomputed query embedding vector; if None, generated on-the-fly.
        """
        # 1. Generate query embedding if not provided
        if query_vector is None:
            query_vector = await generate_query_embedding(question)

        # Format pgvector string safely (array of floats only)
        vec_literal = "[" + ",".join(f"{v:.8f}" for v in query_vector) + "]"

        # 2. Construct deterministic deduplicated CTE SQL
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
            WHERE c.embedding IS NOT NULL
              AND (1 - (c.embedding OPERATOR(extensions.<=>) '{vec_literal}'::extensions.vector)) >= 0.65
            ORDER BY p.publication_id, (c.embedding OPERATOR(extensions.<=>) '{vec_literal}'::extensions.vector) ASC
        )
        SELECT *
        FROM scored_chunks
        ORDER BY similarity_score DESC
        LIMIT 8;
        """.strip()

        # 3. Execute query
        rows = await conn.fetch(sql)

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
            threshold=0.65,
            filters_ignored=[],
            sql_executed=debug_sql,
        )
