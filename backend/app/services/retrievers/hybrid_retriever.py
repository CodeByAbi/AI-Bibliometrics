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
from backend.app.core.config import get_settings
from backend.app.models.ask import CandidateItem, FilterParams
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
    #: Set when the question is topic-scoped but the topic could not be resolved
    #: to exactly one `topics` row. The caller must return
    #: ``status="needs_clarification"`` with ``topic_candidates`` rather than
    #: answering with corpus-wide rows — an unanswered scope question must never
    #: be answered as though the scope had been understood.
    unresolved_topic_query: str | None = None
    #: The five canonical topics, offered so the user can pick. Only populated
    #: alongside ``unresolved_topic_query``.
    topic_candidates: list[CandidateItem] = Field(default_factory=list)
    #: Best centroid cosine for an unresolved topic. DIAGNOSTIC ONLY — see
    #: ``_TOPIC_CENTROID_NOTE``. It does not gate, select, or classify anything.
    topic_best_similarity: float | None = None

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
    """Gold-analytics retriever: topic trends, topic evolution, researcher expertise.

    _TOPIC_CENTROID_NOTE
    ---------------------
    Why centroid cosine is diagnostic-only and never selects a topic.

    The previous code resolved an unmatched topic by taking the nearest
    ``topics.representation_vector`` at ``>= 0.50``. Measured on this corpus
    (BAAI/bge-m3, deterministic across repeated calls), that selector picks the
    WRONG topic:

    ==========================  ==========================
    probe                        best centroid cosine
    ==========================  ==========================
    exact canonical names        0.4698 - 0.5946
    close paraphrases            0.4698 - 0.5807
    clearly off-topic phrases    0.3403 - 0.5047
    ==========================  ==========================

    The bands overlap almost completely. Two consequences:

    * **It cannot select a topic.** The exact canonical name "Phytochemicals &
      Molecular Docking" scores 0.5807 against *Microbiology & Food
      Biotechnology* — higher than against its own centroid.
    * **It cannot even decide corpus membership.** "quantum computing"
      (0.5047, unambiguously not in a stem-cell/chemistry/AI corpus) scores
      higher than the exact topic "Nanomaterials & Nanotechnology" (0.4698).

    So there is no threshold that separates these populations, and a
    similarity-based gate would classify known-wrong cases in both directions.
    Scope is therefore decided by EXACT/normalized name match only, and anything
    else is reported as unresolved for the user to resolve. The cosine is still
    computed — it is useful in logs and in ``developer_mode`` — but it decides
    nothing.

    A second, independent defect hid this one: the old query used an unqualified
    ``$1::vector`` and ``<=>`` while pgvector lives in the ``extensions`` schema
    and the pool pins ``search_path`` to ``public``, so it raised
    ``UndefinedObjectError: type "vector" does not exist`` on every call. A broad
    ``except Exception`` logged that at DEBUG — invisible at default log level —
    so the whole semantic path was dead code and *every* unresolved topic silently
    fell through to a corpus-wide query. It is now schema-qualified and logs at
    WARNING.
    """
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
                # NOTE: every alternative is plural-tolerant. These were not, and
                # `\bexpert\b` does not match "experts" (the trailing \b needs a
                # non-word char, and "s" is a word char). The canonical question
                # "…and who are the experts?" therefore failed this test, was
                # classified TOPIC_TRENDS instead of COMBINED_ANALYTICS, and the
                # expert half of the question was silently never queried.
                r"\b(pakar|pakar\s+utama|experts?|researchers?|peneliti|ahli|"
                r"authors?|siapa\s+yang|who\s+is|who\s+are|leading|terbaik|"
                r"terkemuka|skor\s*kepakaran|expertise\s*score)\b",
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
        """Extract explicit topic name or keyword candidate from filters or question text.

        Two measured defects fixed here:

        1. The capture class was ``[a-zA-Z0-9\\s\\-]``, which has no ``&``. Every
           canonical topic name contains one ("Mesenchymal Stem Cells &
           Inflammation"), so "How has research on Mesenchymal Stem Cells &
           Inflammation evolved over time?" captured nothing and fell through to
           a corpus-wide query. ``&`` and ``+`` are now in the class.
        2. The terminator list had no temporal words, so "tren topik Coumarin
           setelah 2020" captured the literal string ``"Coumarin setelah 2020"``,
           which matches no topic and then (via the centroid fallback) got
           mapped onto a neighbouring topic. Indonesian and English temporal
           markers now terminate the capture.
        """
        if filters and filters.topic_name:
            return filters.topic_name.strip()
        if filters and filters.keyword:
            return filters.keyword.strip()

        # Regex heuristic in question. Triggers include the English framing verbs
        # ("research on", "evolution of", "about") because the canonical evolution
        # questions use them, and the old trigger set was Indonesian-only.
        match = re.search(
            r"\b(?:topik|bidang|riset|terapi|teknologi|domain|tentang|mengenai|"
            r"on|about|research\s+on|evolution\s+of|evolusi\s+daripada|"
            r"perkembangan\s+topik)\s+"
            r"([a-zA-Z0-9&\+\s\-]+?)"
            r"(?:\s+(?:5\s*tahun|dalam|pada|tahun|di|in|who|siapa|dan|and|"
            r"setelah|sejak|sebelum|antara|after|since|before|between|"
            r"from|over\s+time|evolusi|evolv\w*|berkembang|berkembangan)\b|\?|$)",
            question,
            re.IGNORECASE,
        )
        if match:
            candidate = match.group(1).strip()
            # Strip trailing/leading connectors that survive the non-greedy match.
            candidate = re.sub(r"\s+(?:dan|and|with|dengan)$", "", candidate, flags=re.IGNORECASE).strip()
            # Exclude common query words
            if len(candidate) >= 3 and candidate.lower() not in (
                "yang", "apa", "terbaru", "indonesia", "tahun",
            ):
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
        pool: asyncpg.Pool | None = None,
    ) -> HybridRetrievalResult:
        """Execute parameterized Gold analytics retrieval across topics, trends, and researcher expertise.

        ``pool`` enables the Step 2 + Step 3 fast path: trends and expertise
        run concurrently on two pooled connections instead of sequentially.
        asyncpg forbids concurrent operations on ONE connection, so the pool
        (not a second cursor on ``conn``) is what makes this safe. Callers
        without a pool get the original sequential behaviour unchanged.
        """
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

        # Step 1: Resolve topic scope.
        #
        # Scope is decided by EXACT/normalized name match only. When no name
        # matches, the question is topic-scoped but unresolved, and this
        # retriever reports that instead of answering with corpus-wide rows.
        topic_kw = cls.extract_topic_keyword(question, filters)
        resolved_topic_id: int | None = None
        resolved_topic_name: str | None = None
        unresolved_topic_query: str | None = None
        topic_candidates: list[CandidateItem] = []
        topic_best_similarity: float | None = None

        # A topic-scoped question is one that names or frames a research topic.
        # Only these may be held to the clarification contract; an unscoped
        # "show emerging topics" legitimately wants corpus-wide rows.
        topic_scoped_question = bool(topic_kw) or bool(
            filters and (filters.topic_name or filters.keyword)
        )

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
                # No exact name match. Record the best centroid similarity for
                # diagnostics only — see _TOPIC_CENTROID_NOTE for why it must
                # not decide anything.
                vec_schema = get_settings().vector_schema
                try:
                    vec = await generate_query_embedding(topic_kw)
                    validate_embedding_vector(vec, 1024)
                    vec_str = "[" + ",".join(f"{x:.8f}" for x in vec) + "]"
                    sem_row = await asyncio.wait_for(
                        conn.fetchrow(
                            f"""
                            SELECT t.topic_name,
                                   1 - (t.representation_vector
                                        OPERATOR({vec_schema}.<=>) $1::{vec_schema}.vector) AS sim
                            FROM topics t
                            WHERE t.representation_vector IS NOT NULL
                            ORDER BY t.representation_vector
                                     OPERATOR({vec_schema}.<=>) $1::{vec_schema}.vector ASC
                            LIMIT 1;
                            """,
                            vec_str,
                        ),
                        timeout=STATEMENT_TIMEOUT_S,
                    )
                    if sem_row is not None and sem_row["sim"] is not None:
                        topic_best_similarity = float(sem_row["sim"])
                        logger.info(
                            "Unresolved topic %r: nearest centroid %r at cosine %.4f "
                            "(diagnostic only — not discriminative, see "
                            "_TOPIC_CENTROID_NOTE)",
                            topic_kw,
                            str(sem_row["topic_name"]),
                            topic_best_similarity,
                        )
                except Exception as exc:
                    # WARNING, not DEBUG: this lookup is the only signal an
                    # operator has for why a topic failed to resolve. At DEBUG
                    # the original failure mode was invisible at default level.
                    logger.warning(
                        "Topic centroid similarity lookup failed for %r: %s",
                        topic_kw,
                        exc,
                    )

                unresolved_topic_query = topic_kw

        if topic_scoped_question and unresolved_topic_query:
            # Offer the canonical topics so the user can pick one. This is the
            # point of the change: an unresolvable scope must surface as a
            # question, not be answered as though it had been understood.
            topic_rows = await asyncio.wait_for(
                conn.fetch(
                    """
                    SELECT topic_id, topic_name, total_publications
                    FROM topics
                    ORDER BY total_publications DESC, topic_name ASC
                    LIMIT 10;
                    """
                ),
                timeout=STATEMENT_TIMEOUT_S,
            )
            topic_candidates = [
                CandidateItem(
                    id=str(r["topic_id"]),
                    name=str(r["topic_name"]),
                    type="topic",
                    publication_count=int(r["total_publications"]),
                )
                for r in topic_rows
            ]

        # Check if question specifically asks for "emerging topics" (is_emerging=True)
        is_emerging_filter = True if re.search(r"\b(emerging|berkembang\s*pesat|topik\s*baru)\b", question, re.IGNORECASE) else None

        # Step 2 & 3: Topic Trends + Researcher Expertise.
        #
        # Each loader below is a pure function of (connection, resolved scope):
        # neither result feeds the other's query, so when BOTH are needed they
        # run concurrently on two pooled connections (one RTT instead of two).
        # Single-intent requests and pool-less callers keep the sequential path.
        async def _load_trends(
            read_conn: asyncpg.Connection,
        ) -> list[HybridTopicEvolutionItem]:
            items: list[HybridTopicEvolutionItem] = []
            try:
                rows_trends = await asyncio.wait_for(
                    read_conn.fetch(
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
                items.append(
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
            return items

        async def _load_experts(
            read_conn: asyncpg.Connection,
        ) -> list[HybridExpertItem]:
            items: list[HybridExpertItem] = []
            try:
                rows_exp = await asyncio.wait_for(
                    read_conn.fetch(
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
                items.append(
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
            return items

        # Step 2: Query Topic Trends if intent is TOPIC_TRENDS or COMBINED_ANALYTICS
        topics_list: list[HybridTopicEvolutionItem] = []
        # Step 3: Query Researcher Expertise if intent is EXPERT_RANKING or COMBINED_ANALYTICS
        experts_list: list[HybridExpertItem] = []
        need_trends = intent in ("TOPIC_TRENDS", "COMBINED_ANALYTICS")
        need_experts = intent in ("EXPERT_RANKING", "COMBINED_ANALYTICS")
        if need_trends and need_experts and pool is not None:
            async with pool.acquire() as conn2:
                topics_list, experts_list = await asyncio.gather(
                    _load_trends(conn),
                    _load_experts(conn2),
                )
        else:
            if need_trends:
                topics_list = await _load_trends(conn)
            if need_experts:
                experts_list = await _load_experts(conn)

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
            unresolved_topic_query=unresolved_topic_query,
            topic_candidates=topic_candidates,
            topic_best_similarity=topic_best_similarity,
        )
