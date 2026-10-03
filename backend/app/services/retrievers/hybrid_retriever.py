"""HybridRetriever: Gold Analytics (Topics, Trends, Researcher Expertise) + Vector & Relational Search.

Docs Reference: docs/05 Retrieval Rag Design.md §5.4; docs/02 SRD.md FR6, FR7;
docs/04 Database Schema.md §7; docs/10 Implementation Plan.md §1 (Task 8.5, Task 10);
docs/11 Roadmap.md (Fase 7).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from backend.app.core.errors import DBTimeoutError
from backend.app.models.ask import FilterParams
from backend.app.services.embedding import (
    generate_query_embedding,
    validate_embedding_vector,
)
from backend.app.services.retrievers.sql_security import escape_like_pattern

logger = logging.getLogger("hybrid_retriever")

DEFAULT_LIMIT = 10
MAX_LIMIT = 50
STATEMENT_TIMEOUT_S = 10.0

HybridIntentType = Literal["TOPIC_TRENDS", "EXPERT_RANKING", "COMBINED_ANALYTICS"]


class HybridTopicEvolutionItem(BaseModel):
    """Normalized topic trend and evolution observation item."""

    topic_id: int
    topic_name: str
    year: int
    publication_count: int
    citation_count: int
    growth_score: float
    citation_acceleration: float
    recency_weight: float
    is_emerging: bool


class HybridExpertItem(BaseModel):
    """Normalized researcher expertise ranking item."""

    author_id: str
    author_name: str
    topic_id: int
    topic_name: str
    expertise_score: float
    relevance_score: float
    productivity_score: float
    impact_score: float
    recency_score: float
    h_index_topic: int
    publication_count_topic: int
    citation_count_topic: int
    coauthor_network_size: int


class HybridPublicationMeta(BaseModel):
    """Metadata for a supporting publication record for citation and provenance."""

    publication_id: str
    title: str
    year: int | None = None
    doi: str | None = None
    eid: str | None = None


class HybridRetrievalResult(BaseModel):
    """Container for HybridRetriever execution output."""

    intent_type: HybridIntentType
    topics: list[HybridTopicEvolutionItem] = Field(default_factory=list)
    experts: list[HybridExpertItem] = Field(default_factory=list)
    publications: dict[str, HybridPublicationMeta] = Field(default_factory=dict)
    sql_executed: str | None = None
    target_topic_id: int | None = None
    target_topic_name: str | None = None
    filters_ignored: list[str] = Field(default_factory=list)
    execution_time_ms: float = 0.0

    @property
    def is_empty(self) -> bool:
        """True if neither topic trends nor researcher expertise records were found."""
        return len(self.topics) == 0 and len(self.experts) == 0


# ---------------------------------------------------------------------------
# Parameterized SQL Templates for Gold Analytics
# ---------------------------------------------------------------------------

# 1. Topic Trends & Evolution Query
SQL_TOPIC_TRENDS = """
SELECT 
    t.topic_id,
    t.topic_name,
    te.year,
    te.publication_count,
    te.citation_count,
    te.growth_score::FLOAT AS growth_score,
    te.citation_acceleration::FLOAT AS citation_acceleration,
    te.recency_weight::FLOAT AS recency_weight,
    te.is_emerging
FROM topics t
JOIN topic_evolution te ON te.topic_id = t.topic_id
WHERE ($1::BIGINT IS NULL OR t.topic_id = $1)
  AND ($2::TEXT IS NULL OR t.topic_name_normalized ILIKE $2 ESCAPE '\')
  AND ($3::BOOLEAN IS NULL OR te.is_emerging = $3)
  AND ($4::SMALLINT IS NULL OR te.year >= $4)
  AND ($5::SMALLINT IS NULL OR te.year <= $5)
ORDER BY te.year DESC, te.growth_score DESC
LIMIT $6;
""".strip()

# 2. Leading Researcher Expertise Query per Topic
SQL_RESEARCHER_EXPERTISE = """
SELECT 
    a.author_id,
    a.author_name,
    t.topic_id,
    t.topic_name,
    re.expertise_score::FLOAT AS expertise_score,
    re.relevance_score::FLOAT AS relevance_score,
    re.productivity_score::FLOAT AS productivity_score,
    re.impact_score::FLOAT AS impact_score,
    re.recency_score::FLOAT AS recency_score,
    re.h_index_topic,
    re.publication_count_topic,
    re.citation_count_topic,
    re.coauthor_network_size
FROM researcher_expertise re
JOIN authors a ON a.author_id = re.author_id
JOIN topics t ON t.topic_id = re.topic_id
WHERE ($1::BIGINT IS NULL OR t.topic_id = $1)
  AND ($2::TEXT IS NULL OR t.topic_name_normalized ILIKE $2 ESCAPE '\')
  AND ($3::VARCHAR(64) IS NULL OR re.author_id = $3)
ORDER BY re.expertise_score DESC
LIMIT $4;
""".strip()

# 4. Publication Metadata for Supporting Citations
SQL_PUBLICATIONS_FOR_AUTHORS_AND_TOPIC = """
SELECT DISTINCT p.publication_id, p.title, p.year, p.doi, p.eid
FROM publications p
JOIN pub_author pa ON pa.publication_id = p.publication_id
WHERE pa.author_id = ANY($1::VARCHAR(64)[])
ORDER BY p.year DESC NULLS LAST
LIMIT 50;
""".strip()


class HybridRetriever:
    """Deterministic, parameterized hybrid retriever combining Gold analytics, vector, and relational constraints."""

    @classmethod
    def clamp_limit(cls, limit: int | None) -> int:
        """Clamp query result limit to [1, 50]."""
        if limit is None or limit <= 0:
            return DEFAULT_LIMIT
        return min(limit, MAX_LIMIT)

    @classmethod
    def detect_intent(
        cls,
        question: str,
        filters: FilterParams | None = None,
    ) -> HybridIntentType:
        """Detect intent sub-type for HybridRoute (Trends, Expertise, or Combined)."""
        ql = question.lower().strip()

        # Check for expert / author ranking intent
        is_expert_query = bool(
            (filters and filters.author_name)
            or re.search(
                r"\b(pakar|expert|peneliti|ahli|author|siapa\s+yang|who\s+is|leading|terbaik|terkemuka|skor\s*kepakaran|expertise\s*score)\b",
                ql,
                re.IGNORECASE,
            )
        )

        # Check for trend / evolution / growth intent
        is_trend_query = bool(
            re.search(
                r"\b(tren|trend|perkembangan|evolusi|evolve|growth|akselerasi|acceleration|emerging|berkembang|tahun\s*terakhir|recent\s*years)\b",
                ql,
                re.IGNORECASE,
            )
        )

        if is_expert_query and is_trend_query:
            return "COMBINED_ANALYTICS"
        if is_expert_query:
            return "EXPERT_RANKING"
        if is_trend_query:
            return "TOPIC_TRENDS"

        # Default to combined policy synthesis
        return "COMBINED_ANALYTICS"

    @classmethod
    def extract_topic_keyword(
        cls,
        question: str,
        filters: FilterParams | None = None,
    ) -> str | None:
        """Extract explicit topic name or keyword candidate from filters or question text."""
        if filters and filters.topic_name:
            return filters.topic_name.strip()
        if filters and filters.keyword:
            return filters.keyword.strip()

        # Regex heuristic in question
        match = re.search(
            r"\b(?:topik|bidang|riset|terapi|teknologi|domain|tentang|mengenai|on|about)\s+([a-zA-Z0-9\s\-]+?)(?:\s+(?:5\s*tahun|dalam|pada|tahun|di|in|who|siapa|dan|and)\b|\?|$)",
            question,
            re.IGNORECASE,
        )
        if match:
            candidate = match.group(1).strip()
            # Exclude common query words
            if len(candidate) >= 3 and candidate.lower() not in ("yang", "apa", "terbaru", "indonesia", "tahun"):
                return candidate
        return None

    @classmethod
    async def retrieve(
        cls,
        conn: asyncpg.Connection,
        question: str,
        filters: FilterParams | None = None,
        resolved_author_id: str | None = None,
        resolved_author_name: str | None = None,
        resolved_institution_id: str | None = None,
        resolved_institution_name: str | None = None,
        limit: int | None = None,
    ) -> HybridRetrievalResult:
        """Execute parameterized Gold analytics retrieval across topics, trends, and researcher expertise."""
        start_time = time.perf_counter()
        clamped_limit = cls.clamp_limit(limit)
        intent = cls.detect_intent(question, filters)

        filters_ignored: list[str] = []
        if filters:
            if filters.country is not None:
                filters_ignored.append("country")
            if filters.document_type is not None:
                filters_ignored.append("document_type")
        if resolved_institution_id or resolved_institution_name:
            # Institution filter not directly represented in topic-level Gold tables
            filters_ignored.append("institution")

        # Extract year filters with operator validation
        year_from = None
        year_to = None
        if filters:
            if filters.year is not None:
                year_from = filters.year
                year_to = filters.year
            else:
                year_from = filters.year_from
                year_to = filters.year_to

        # Step 1: Resolve topic candidate (by name pattern or semantic centroid vector)
        topic_kw = cls.extract_topic_keyword(question, filters)
        resolved_topic_id: int | None = None
        resolved_topic_name: str | None = None

        if topic_kw:
            # Try exact / ILIKE lookup on topics table first
            pattern = escape_like_pattern(topic_kw)
            try:
                topic_row = await asyncio.wait_for(
                    conn.fetchrow(
                        """
                        SELECT topic_id, topic_name
                        FROM topics
                        WHERE topic_name_normalized ILIKE $1 ESCAPE '\'
                           OR topic_name ILIKE $1 ESCAPE '\'
                        ORDER BY total_publications DESC
                        LIMIT 1;
                        """,
                        pattern,
                    ),
                    timeout=STATEMENT_TIMEOUT_S,
                )
            except TimeoutError as exc:
                raise DBTimeoutError("HybridRetriever statement timed out (10s)") from exc

            if topic_row:
                resolved_topic_id = int(topic_row["topic_id"])
                resolved_topic_name = str(topic_row["topic_name"])
            else:
                # Semantic vector fallback over topics centroid
                try:
                    vec = await generate_query_embedding(topic_kw)
                    validate_embedding_vector(vec, 1024)
                    vec_str = "[" + ",".join(f"{x:.8f}" for x in vec) + "]"
                    sem_row = await asyncio.wait_for(
                        conn.fetchrow(
                            """
                            SELECT topic_id, topic_name, 1 - (representation_vector <=> $1::vector) AS sim
                            FROM topics
                            WHERE representation_vector IS NOT NULL
                            ORDER BY representation_vector <=> $1::vector ASC
                            LIMIT 1;
                            """,
                            vec_str,
                        ),
                        timeout=STATEMENT_TIMEOUT_S,
                    )
                    if sem_row and sem_row["sim"] is not None and sem_row["sim"] >= 0.50:
                        resolved_topic_id = int(sem_row["topic_id"])
                        resolved_topic_name = str(sem_row["topic_name"])
                except Exception as exc:
                    logger.debug("Semantic topic lookup fallback skipped or failed: %s", exc)

        # Check if question specifically asks for "emerging topics" (is_emerging=True)
        is_emerging_filter = True if re.search(r"\b(emerging|berkembang\s*pesat|topik\s*baru)\b", question, re.IGNORECASE) else None

        # Step 2: Query Topic Trends if intent is TOPIC_TRENDS or COMBINED_ANALYTICS
        topics_list: list[HybridTopicEvolutionItem] = []
        if intent in ("TOPIC_TRENDS", "COMBINED_ANALYTICS"):
            try:
                rows_trends = await asyncio.wait_for(
                    conn.fetch(
                        SQL_TOPIC_TRENDS,
                        resolved_topic_id,
                        None,  # topic_name pattern already resolved to ID
                        is_emerging_filter,
                        year_from,
                        year_to,
                        clamped_limit,
                    ),
                    timeout=STATEMENT_TIMEOUT_S,
                )
            except TimeoutError as exc:
                raise DBTimeoutError("HybridRetriever statement timed out (10s)") from exc

            for r in rows_trends:
                topics_list.append(
                    HybridTopicEvolutionItem(
                        topic_id=int(r["topic_id"]),
                        topic_name=str(r["topic_name"]),
                        year=int(r["year"]),
                        publication_count=int(r["publication_count"]),
                        citation_count=int(r["citation_count"]),
                        growth_score=float(r["growth_score"]),
                        citation_acceleration=float(r["citation_acceleration"]),
                        recency_weight=float(r["recency_weight"]),
                        is_emerging=bool(r["is_emerging"]),
                    )
                )

        # Step 3: Query Researcher Expertise if intent is EXPERT_RANKING or COMBINED_ANALYTICS
        experts_list: list[HybridExpertItem] = []
        if intent in ("EXPERT_RANKING", "COMBINED_ANALYTICS"):
            try:
                rows_exp = await asyncio.wait_for(
                    conn.fetch(
                        SQL_RESEARCHER_EXPERTISE,
                        resolved_topic_id,
                        None,
                        resolved_author_id,
                        clamped_limit,
                    ),
                    timeout=STATEMENT_TIMEOUT_S,
                )
            except TimeoutError as exc:
                raise DBTimeoutError("HybridRetriever statement timed out (10s)") from exc

            for r in rows_exp:
                experts_list.append(
                    HybridExpertItem(
                        author_id=str(r["author_id"]),
                        author_name=str(r["author_name"]),
                        topic_id=int(r["topic_id"]),
                        topic_name=str(r["topic_name"]),
                        expertise_score=float(r["expertise_score"]),
                        relevance_score=float(r["relevance_score"]),
                        productivity_score=float(r["productivity_score"]),
                        impact_score=float(r["impact_score"]),
                        recency_score=float(r["recency_score"]),
                        h_index_topic=int(r["h_index_topic"]),
                        publication_count_topic=int(r["publication_count_topic"]),
                        citation_count_topic=int(r["citation_count_topic"]),
                        coauthor_network_size=int(r["coauthor_network_size"]),
                    )
                )

        # Step 4: Fetch supporting publication metadata for citations
        author_ids_to_fetch = list(dict.fromkeys(e.author_id for e in experts_list))
        pubs_map: dict[str, HybridPublicationMeta] = {}

        if author_ids_to_fetch:
            try:
                rows_pubs = await asyncio.wait_for(
                    conn.fetch(SQL_PUBLICATIONS_FOR_AUTHORS_AND_TOPIC, author_ids_to_fetch),
                    timeout=STATEMENT_TIMEOUT_S,
                )
                for rp in rows_pubs:
                    pid = str(rp["publication_id"])
                    pubs_map[pid] = HybridPublicationMeta(
                        publication_id=pid,
                        title=str(rp["title"]),
                        year=int(rp["year"]) if rp["year"] is not None else None,
                        doi=str(rp["doi"]) if rp["doi"] else None,
                        eid=str(rp["eid"]) if rp["eid"] else None,
                    )
            except Exception as exc:
                logger.warning("Failed to fetch supporting publications for hybrid route: %s", exc)

        # If zero topics and zero experts found, return clean empty result (triggers not_found short-circuit)
        elapsed = (time.perf_counter() - start_time) * 1000
        sql_summary = f"TEMPLATE: SQL_GOLD_ANALYTICS (intent='{intent}', topic_id={resolved_topic_id}, limit={clamped_limit})"

        return HybridRetrievalResult(
            intent_type=intent,
            topics=topics_list,
            experts=experts_list,
            publications=pubs_map,
            sql_executed=sql_summary,
            target_topic_id=resolved_topic_id,
            target_topic_name=resolved_topic_name,
            filters_ignored=filters_ignored,
            execution_time_ms=elapsed,
        )
