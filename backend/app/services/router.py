"""Question routing and entity resolution gate services.

Docs Reference: docs/05 Retrieval Rag Design.md §3, docs/10 Implementation Plan.md §1 (Task 4).
"""

from __future__ import annotations

import re
import string
from typing import Any, Dict, List, Literal, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field

import asyncpg
from backend.app.models.ask import CandidateItem, FilterParams


RouteType = Literal["SQLRoute", "VectorRoute", "GraphRoute", "HybridRoute"]


class RouteDecision(BaseModel):
    """Result of question intent routing classification."""

    model_config = ConfigDict(frozen=True)

    route: RouteType
    reasoning: str
    answered_via_fallback: bool = False
    extracted_entities: Dict[str, Any] = Field(default_factory=dict)


class EntityResolutionResult(BaseModel):
    """Result of entity disambiguation and normalization gate."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "needs_clarification"] = "ok"
    candidates: Optional[List[CandidateItem]] = None
    resolved_author_id: Optional[str] = None
    resolved_author_name: Optional[str] = None
    resolved_institution_id: Optional[str] = None
    resolved_institution_name: Optional[str] = None
    clarification_message: Optional[str] = None


# --- Regex Pattern Definitions for 4 Routes (ID & EN) ---

# 1. SQLRoute Patterns (Counting, Aggregation, Ranking, Metadata, List, Exact stats)
SQL_PATTERNS = [
    re.compile(r"\b(berapa|jumlah|total|hitung)\b.*\b(publikasi|paper|artikel|sitasi|author|penulis|institusi|dana|grant)\b", re.IGNORECASE),
    re.compile(r"\b(siapa|daftar|tampilkan|sebutkan)\b.*\b(top\s*\d+|\d+\s*teratas|paling\s*(produktif|banyak|sering|banyak disitasi))\b", re.IGNORECASE),
    re.compile(r"\b(top\s*\d+|\d+\s*teratas|peringkat|ranking)\b", re.IGNORECASE),
    re.compile(r"\b(sitasi\s*terbanyak|most\s*cited|highest\s*citations?)\b", re.IGNORECASE),
    re.compile(r"\b(how\s*many|count\s*of|total\s*(number\s*of)?\s*(publications|papers|articles|citations|authors|grants?))\b", re.IGNORECASE),
    re.compile(r"\b(who\s*is|who\s*are|list|show)\b.*\b(top\s*\d+|\d+\s*most\s*(productive|cited)|most\s*prolific)\b", re.IGNORECASE),
    re.compile(r"\b(most\s*productive|prolific\s*authors?|most\s*active)\b", re.IGNORECASE),
    re.compile(r"\b(publikasi|papers?|articles?)\s*(tahun|pada\s*tahun|in\s*year|published\s*in)\s*\d{4}\b", re.IGNORECASE),
    re.compile(r"\b(funding\s*agency|sumber\s*dana|hibah|grant\s*number)\b", re.IGNORECASE),
    re.compile(r"\b(open\s*access|document\s*type|tipe\s*dokumen|bahasa\s*dokumen)\b", re.IGNORECASE),
]
# 2. GraphRoute Patterns (Collaboration, Co-authorship, Networks, Partnerships)
GRAPH_PATTERNS = [
    re.compile(r"\b(kolaborasi|berkolaborasi|kerjasama|kemitraan)\b", re.IGNORECASE),
    re.compile(r"\b(co-?authors?(hip)?|rekan\s*penulis|teman\s*menulis)\b", re.IGNORECASE),
    re.compile(r"\b(jaringan\s*(riset|penelitian|peneliti|institusi|kolaborasi)|network(ing)?)\b", re.IGNORECASE),
    re.compile(r"\b(collaborat\w*|partner\w*)\b", re.IGNORECASE),
    re.compile(r"\b(who\s*(has\s*)?worked\s*with|joint\s*publications?|who\s*published\s*with)\b", re.IGNORECASE),
    re.compile(r"\b(mitra\s*riset|mitra\s*institusi|institutions?\s*collaborating)\b", re.IGNORECASE),
]

# 3. HybridRoute Patterns (Trends, Topic Evolution, Emerging Topics, Researcher Expertise Scoring)
HYBRID_PATTERNS = [
    re.compile(r"\b(tren|perkembangan|evolusi)\b.*\b(topik|bidang|riset|terapi|teknologi|domain)\b", re.IGNORECASE),
    re.compile(r"\b(topik\s*(berkembang|baru|populer)|emerging\s*topics?|topic\s*evolution)\b", re.IGNORECASE),
    re.compile(r"\b(skor\s*kepakaran|expertise\s*score|pakar\s*(utama|terbaik|terkemuka)|leading\s*experts?)\b", re.IGNORECASE),
    re.compile(r"\b(sintesis\s*kebijakan|policy\s*synthesis|rekomendasi\s*kebijakan|arah\s*riset)\b", re.IGNORECASE),
    re.compile(r"\b(trend(s)?\s*(in|of)|how\s*has\s*.*evolved|growth\s*score|citation\s*acceleration)\b", re.IGNORECASE),
]

# 4. VectorRoute Patterns (Semantic/Conceptual exploration, abstract topics, biological/clinical mechanisms)
VECTOR_PATTERNS = [
    re.compile(r"\b(paper|artikel|jurnal|naskah)\s*(yang|tentang|mengenai|membahas|meneliti)\b", re.IGNORECASE),
    re.compile(r"\b(papers?|articles?|studies|literature)\s*(about|on|discussing|exploring|related\s*to)\b", re.IGNORECASE),
    re.compile(r"\b(konsep|mekanisme|pengaruh|efek|studi|peran|analisis\s*kualitatif)\b", re.IGNORECASE),
    re.compile(r"\b(concept\s*of|mechanism\s*of|role\s*of|effect\s*of|impact\s*of|how\s*does\s*.*work)\b", re.IGNORECASE),
    re.compile(r"\b(penelitian\s*terkait|studi\s*mengenai|literatur\s*tentang)\b", re.IGNORECASE),
]


def normalize_text(text: str) -> str:
    """Normalize string by lowercasing, stripping punctuation, and compressing whitespace."""
    if not text:
        return ""
    # Strip punctuation and lower
    translator = str.maketrans("", "", string.punctuation)
    clean = text.translate(translator).lower()
    return " ".join(clean.split())


class QuestionRouter:
    """Rule-based question intent classifier mapping queries to one of 4 RAG routes."""

    @classmethod
    def classify_route(
        cls,
        question: str,
        filters: Optional[FilterParams] = None,
    ) -> RouteDecision:
        """Classify user query and structured filters into target RAG route."""
        q = question.strip()

        # Step 1: Check GraphRoute triggers (High specificity for collaboration & network queries)
        for pattern in GRAPH_PATTERNS:
            if pattern.search(q):
                return RouteDecision(
                    route="GraphRoute",
                    reasoning=f"Matched GraphRoute pattern '{pattern.pattern}' for collaboration/network intent",
                    answered_via_fallback=False,
                )

        # Step 2: Check HybridRoute triggers (Trends, evolution, expertise scoring)
        for pattern in HYBRID_PATTERNS:
            if pattern.search(q):
                return RouteDecision(
                    route="HybridRoute",
                    reasoning=f"Matched HybridRoute pattern '{pattern.pattern}' for trend/evolution/expertise intent",
                    answered_via_fallback=False,
                )

        # If filters explicitly specify topic_name combined with general query
        if filters and filters.topic_name:
            # If asking about trends/experts on topic
            if any(term in q.lower() for term in ["tren", "trend", "pakar", "expert", "evolusi", "growth"]):
                return RouteDecision(
                    route="HybridRoute",
                    reasoning="Query combines topic_name filter with trend/expertise keywords",
                    answered_via_fallback=False,
                )

        # Step 3: Check SQLRoute triggers (Counting, aggregation, ranking, top-N, explicit stats)
        for pattern in SQL_PATTERNS:
            if pattern.search(q):
                return RouteDecision(
                    route="SQLRoute",
                    reasoning=f"Matched SQLRoute pattern '{pattern.pattern}' for aggregation/ranking/relational intent",
                    answered_via_fallback=False,
                )

        # Check if structured filters strongly imply SQL relational search
        if filters and (
            filters.year is not None
            or filters.year_from is not None
            or filters.year_to is not None
            or filters.author_name
            or filters.institution_name
            or filters.country
            or filters.document_type
        ):
            # If the question asks for lists or counts with filters
            if any(term in q.lower() for term in ["siapa", "who", "berapa", "how many", "daftar", "list", "tampilkan", "show", "publikasi", "papers"]):
                return RouteDecision(
                    route="SQLRoute",
                    reasoning="Query specifies structured metadata filters and listing/counting intent",
                    answered_via_fallback=False,
                )

        # Step 4: Check VectorRoute triggers (Conceptual, abstract exploration)
        for pattern in VECTOR_PATTERNS:
            if pattern.search(q):
                return RouteDecision(
                    route="VectorRoute",
                    reasoning=f"Matched VectorRoute pattern '{pattern.pattern}' for semantic/conceptual intent",
                    answered_via_fallback=False,
                )

        # Step 5: Fallback handling
        # If question has structured filters (e.g. topic_name) -> HybridRoute fallback
        if filters and filters.topic_name:
            return RouteDecision(
                route="HybridRoute",
                reasoning="Fallback to HybridRoute due to present topic_name filter",
                answered_via_fallback=True,
            )

        # Default fallback to VectorRoute with answered_via_fallback = True
        return RouteDecision(
            route="VectorRoute",
            reasoning="No definitive rule pattern matched; defaulting to semantic VectorRoute fallback",
            answered_via_fallback=True,
        )


class EntityResolutionGate:
    """Disambiguation and entity validation gate against live PostgreSQL records."""

    @classmethod
    async def extract_candidate_names(
        cls,
        question: str,
        filters: Optional[FilterParams] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Extract possible author and institution candidate names from query or filters."""
        author_name: Optional[str] = None
        institution_name: Optional[str] = None

        if filters:
            if filters.author_name:
                author_name = filters.author_name.strip()
            if filters.institution_name:
                institution_name = filters.institution_name.strip()

        # If not in filters, try regex heuristics on the query
        if not author_name:
            # e.g., "penulis Dr. Ahmad", "author John Doe", "oleh Septi Gumiandari", "by Septi Gumiandari"
            auth_match = re.search(
                r"\b(?:penulis|author|peneliti|oleh|by)\s+([A-Z][a-zA-Z\.\'\-\s]+?)(?:\s+(?:pada|tahun|di|in|with|pada|yang|\?|$))",
                question,
                re.IGNORECASE,
            )
            if auth_match:
                extracted = auth_match.group(1).strip()
                # Exclude common query words
                if extracted.lower() not in {"top", "most", "paling", "terbanyak", "teratas", "indonesia"}:
                    author_name = extracted

        if not institution_name:
            # e.g., "institusi Universitas Andalas", "di ITB", "at Hasanuddin University"
            inst_match = re.search(
                r"\b(?:institusi|universitas|university|institut|at|di)\s+([A-Z][a-zA-Z\.\'\-\s]+?)(?:\s+(?:pada|tahun|in|with|yang|\?|$))",
                question,
                re.IGNORECASE,
            )
            if inst_match:
                extracted = inst_match.group(1).strip()
                if extracted.lower() not in {"indonesia", "tahun", "scopus", "database"}:
                    institution_name = extracted

        return author_name, institution_name

    @classmethod
    async def resolve_entities(
        cls,
        conn: asyncpg.Connection,
        question: str,
        filters: Optional[FilterParams] = None,
    ) -> EntityResolutionResult:
        """Resolve author and institution entities, checking for ambiguous candidate sets."""
        author_query, inst_query = await cls.extract_candidate_names(question, filters)

        # 1. Author resolution
        if author_query and len(author_query) >= 3:
            norm_name = normalize_text(author_query)

            # Exact match check
            exact_rows = await conn.fetch(
                """
                SELECT author_id, author_name, author_name_normalized
                FROM authors
                WHERE author_name_normalized = $1
                   OR author_name ILIKE $2
                LIMIT 10;
                """,
                norm_name,
                author_query,
            )

            if len(exact_rows) == 1:
                row = exact_rows[0]
                return EntityResolutionResult(
                    status="ok",
                    resolved_author_id=row["author_id"],
                    resolved_author_name=row["author_name"],
                )
            elif len(exact_rows) > 1:
                # Multiple candidates found -> needs clarification
                candidate_items: List[CandidateItem] = []
                for r in exact_rows[:5]:
                    pub_count = await conn.fetchval(
                        "SELECT COUNT(publication_id) FROM pub_author WHERE author_id = $1;",
                        r["author_id"],
                    ) or 0
                    candidate_items.append(
                        CandidateItem(
                            id=r["author_id"],
                            name=r["author_name"],
                            type="author",
                            publication_count=int(pub_count),
                            affiliation=None,
                        )
                    )
                return EntityResolutionResult(
                    status="needs_clarification",
                    candidates=candidate_items,
                    clarification_message=(
                        f"Ditemukan {len(exact_rows)} penulis yang cocok dengan '{author_query}'. "
                        "Silakan pilih penulis yang dimaksud."
                    ),
                )
            else:
                # Partial ILIKE search if not exact
                partial_rows = await conn.fetch(
                    """
                    SELECT a.author_id, a.author_name, COUNT(pa.publication_id) AS pub_count
                    FROM authors a
                    LEFT JOIN pub_author pa ON pa.author_id = a.author_id
                    WHERE a.author_name ILIKE $1
                    GROUP BY a.author_id, a.author_name
                    ORDER BY pub_count DESC
                    LIMIT 10;
                    """,
                    f"%{author_query}%",
                )
                if len(partial_rows) > 1:
                    candidates = [
                        CandidateItem(
                            id=r["author_id"],
                            name=r["author_name"],
                            type="author",
                            publication_count=int(r["pub_count"]),
                            affiliation=None,
                        )
                        for r in partial_rows[:5]
                    ]
                    return EntityResolutionResult(
                        status="needs_clarification",
                        candidates=candidates,
                        clarification_message=(
                            f"Ditemukan {len(partial_rows)} kandidat penulis untuk '{author_query}'. "
                            "Mohon pilih entitas yang tepat."
                        ),
                    )
                elif len(partial_rows) == 1:
                    r = partial_rows[0]
                    return EntityResolutionResult(
                        status="ok",
                        resolved_author_id=r["author_id"],
                        resolved_author_name=r["author_name"],
                    )

        # 2. Institution resolution
        if inst_query and len(inst_query) >= 3:
            norm_inst = normalize_text(inst_query)

            # Exact match check
            exact_insts = await conn.fetch(
                """
                SELECT institution_id, institution_name, country
                FROM institutions
                WHERE institution_name_normalized = $1
                   OR institution_name ILIKE $2
                LIMIT 10;
                """,
                norm_inst,
                inst_query,
            )

            if len(exact_insts) == 1:
                row = exact_insts[0]
                return EntityResolutionResult(
                    status="ok",
                    resolved_institution_id=row["institution_id"],
                    resolved_institution_name=row["institution_name"],
                )
            elif len(exact_insts) > 1:
                candidate_items = []
                for r in exact_insts[:5]:
                    pub_count = await conn.fetchval(
                        "SELECT COUNT(publication_id) FROM pub_institution WHERE institution_id = $1;",
                        r["institution_id"],
                    ) or 0
                    candidate_items.append(
                        CandidateItem(
                            id=r["institution_id"],
                            name=r["institution_name"],
                            type="institution",
                            publication_count=int(pub_count),
                            affiliation=r["country"] or None,
                        )
                    )
                return EntityResolutionResult(
                    status="needs_clarification",
                    candidates=candidate_items,
                    clarification_message=(
                        f"Ditemukan {len(exact_insts)} institusi yang cocok dengan '{inst_query}'. "
                        "Silakan pilih institusi yang dimaksud."
                    ),
                )
            else:
                # Partial search
                partial_insts = await conn.fetch(
                    """
                    SELECT i.institution_id, i.institution_name, i.country, COUNT(pi.publication_id) AS pub_count
                    FROM institutions i
                    LEFT JOIN pub_institution pi ON pi.institution_id = i.institution_id
                    WHERE i.institution_name ILIKE $1
                    GROUP BY i.institution_id, i.institution_name, i.country
                    ORDER BY pub_count DESC
                    LIMIT 10;
                    """,
                    f"%{inst_query}%",
                )
                if len(partial_insts) > 1:
                    candidates = [
                        CandidateItem(
                            id=r["institution_id"],
                            name=r["institution_name"],
                            type="institution",
                            publication_count=int(r["pub_count"]),
                            affiliation=r["country"] or None,
                        )
                        for r in partial_insts[:5]
                    ]
                    return EntityResolutionResult(
                        status="needs_clarification",
                        candidates=candidates,
                        clarification_message=(
                            f"Ditemukan {len(partial_insts)} kandidat institusi untuk '{inst_query}'. "
                            "Mohon pilih institusi yang tepat."
                        ),
                    )
                elif len(partial_insts) == 1:
                    r = partial_insts[0]
                    return EntityResolutionResult(
                        status="ok",
                        resolved_institution_id=r["institution_id"],
                        resolved_institution_name=r["institution_name"],
                    )

        # No disambiguation needed or no entities found
        return EntityResolutionResult(status="ok")
