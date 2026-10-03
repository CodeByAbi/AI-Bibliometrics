"""GraphRetriever: Parameterized PostgreSQL Recursive CTE Graph Retrieval Engine (T1-T4).

Docs Reference: docs/05 Retrieval Rag Design.md §5.3; docs/02 SRD.md FR7;
docs/04 Database Schema.md §6; docs/10 Implementation Plan.md (Task 8-retriever);
docs/11 Roadmap.md (Fase 6).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.errors import DBTimeoutError
from backend.app.models.ask import FilterParams

logger = logging.getLogger("graph_retriever")

# Hard limits & defaults per FR7.2 / docs/02 / docs/05 §5.3
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
DEFAULT_MAX_HOPS = 3
MAX_ALLOWED_HOPS = 3
STATEMENT_TIMEOUT_S = 10.0


class GraphEdgeResult(BaseModel):
    """Normalized single graph collaboration edge result."""

    model_config = ConfigDict(frozen=True)

    partner_id: str
    partner_name: str
    publication_count: int
    via_publication_ids: list[str] = Field(default_factory=list)
    path_nodes: list[str] | None = None
    hop_count: int | None = None
    extra_metadata: dict[str, Any] = Field(default_factory=dict)


class GraphPublicationMeta(BaseModel):
    """Metadata for a supporting publication record referenced in graph provenance."""

    model_config = ConfigDict(frozen=True)

    publication_id: str
    title: str | None = None
    year: int | None = None
    doi: str | None = None
    eid: str | None = None


class GraphRetrievalResult(BaseModel):
    """Container for GraphRetriever execution output."""

    model_config = ConfigDict(frozen=True)

    template_type: str  # "T1", "T2", "T3", "T4"
    edges: list[GraphEdgeResult] = Field(default_factory=list)
    publications: dict[str, GraphPublicationMeta] = Field(default_factory=dict)
    sql_executed: str | None = None
    target_entity_name: str | None = None
    target_entity_id: str | None = None
    filters_ignored: list[str] = Field(default_factory=list)
    execution_time_ms: float = 0.0

    @property
    def is_empty(self) -> bool:
        """True when no collaboration edges were found."""
        return len(self.edges) == 0


# ---------------------------------------------------------------------------
# Parameterized SQL Templates (T1 - T4)
# ---------------------------------------------------------------------------

# T1: Institution Partner Collaborations
SQL_TEMPLATE_T1 = """
SELECT ic.institution_b AS partner_id, i.institution_name AS partner_name, 
       ic.weight AS publication_count, ic.via_publication_ids
FROM institution_collaboration ic
JOIN institutions i ON i.institution_id = ic.institution_b
WHERE ic.institution_a = $1
UNION ALL
SELECT ic.institution_a AS partner_id, i.institution_name AS partner_name, 
       ic.weight AS publication_count, ic.via_publication_ids
FROM institution_collaboration ic
JOIN institutions i ON i.institution_id = ic.institution_a
WHERE ic.institution_b = $1
ORDER BY publication_count DESC
LIMIT $2;
""".strip()

# T2: Author Co-authorship Collaborations
SQL_TEMPLATE_T2 = """
SELECT ac.author_b AS partner_id, a.author_name AS partner_name, 
       ac.weight AS publication_count, ac.via_publication_ids
FROM author_collaboration ac
JOIN authors a ON a.author_id = ac.author_b
WHERE ac.author_a = $1
UNION ALL
SELECT ac.author_a AS partner_id, a.author_name AS partner_name, 
       ac.weight AS publication_count, ac.via_publication_ids
FROM author_collaboration ac
JOIN authors a ON a.author_id = ac.author_a
WHERE ac.author_b = $1
ORDER BY publication_count DESC
LIMIT $2;
""".strip()

# T3: Topic / Keyword to Institution Collaboration Composition
SQL_TEMPLATE_T3 = """
SELECT 
    i.institution_id AS partner_id,
    i.institution_name AS partner_name,
    COUNT(DISTINCT p.publication_id)::INTEGER AS publication_count,
    ARRAY_AGG(DISTINCT p.publication_id::TEXT) AS via_publication_ids
FROM institutions i
JOIN pub_institution pi ON pi.institution_id = i.institution_id
JOIN publications p ON p.publication_id = pi.publication_id
JOIN keywords k ON k.publication_id = p.publication_id
    WHERE k.keyword ILIKE $1 ESCAPE '\'
GROUP BY i.institution_id, i.institution_name
ORDER BY publication_count DESC
LIMIT $2;
""".strip()

# T4 (Institution): Bounded Recursive CTE Path Search with cycle prevention
SQL_TEMPLATE_T4_INSTITUTION = """
WITH RECURSIVE collab_path AS (
    SELECT 
        CASE WHEN ic.institution_a = $1 THEN ic.institution_b ELSE ic.institution_a END AS current_node,
        ARRAY[CASE WHEN ic.institution_a = $1 THEN ic.institution_a ELSE ic.institution_b END,
              CASE WHEN ic.institution_a = $1 THEN ic.institution_b ELSE ic.institution_a END]::VARCHAR(64)[] AS path_nodes,
        1 AS hop_count,
        ic.weight,
        ic.via_publication_ids
    FROM institution_collaboration ic
    WHERE ic.institution_a = $1 OR ic.institution_b = $1

    UNION ALL

    SELECT 
        CASE WHEN ic.institution_a = cp.current_node THEN ic.institution_b ELSE ic.institution_a END AS current_node,
        cp.path_nodes || (CASE WHEN ic.institution_a = cp.current_node THEN ic.institution_b ELSE ic.institution_a END)::VARCHAR(64),
        cp.hop_count + 1,
        ic.weight,
        ic.via_publication_ids
    FROM institution_collaboration ic
    JOIN collab_path cp ON (ic.institution_a = cp.current_node OR ic.institution_b = cp.current_node)
    WHERE cp.hop_count < $2
      AND NOT (CASE WHEN ic.institution_a = cp.current_node THEN ic.institution_b ELSE ic.institution_a END = ANY(cp.path_nodes))
)
SELECT 
    cp.current_node AS partner_id,
    i.institution_name AS partner_name,
    cp.path_nodes::TEXT[] AS path_nodes,
    cp.hop_count::INTEGER AS hop_count,
    cp.weight::INTEGER AS publication_count,
    cp.via_publication_ids
FROM collab_path cp
JOIN institutions i ON i.institution_id = cp.current_node
WHERE ($3::TEXT IS NULL OR cp.current_node = $3)
ORDER BY cp.hop_count ASC, cp.weight DESC
LIMIT $4;
""".strip()

# T4 (Author): Bounded Recursive CTE Path Search with cycle prevention
SQL_TEMPLATE_T4_AUTHOR = """
WITH RECURSIVE collab_path AS (
    SELECT 
        CASE WHEN ac.author_a = $1 THEN ac.author_b ELSE ac.author_a END AS current_node,
        ARRAY[CASE WHEN ac.author_a = $1 THEN ac.author_a ELSE ac.author_b END,
              CASE WHEN ac.author_a = $1 THEN ac.author_b ELSE ac.author_a END]::VARCHAR(64)[] AS path_nodes,
        1 AS hop_count,
        ac.weight,
        ac.via_publication_ids
    FROM author_collaboration ac
    WHERE ac.author_a = $1 OR ac.author_b = $1

    UNION ALL

    SELECT 
        CASE WHEN ac.author_a = cp.current_node THEN ac.author_b ELSE ac.author_a END AS current_node,
        cp.path_nodes || (CASE WHEN ac.author_a = cp.current_node THEN ac.author_b ELSE ac.author_a END)::VARCHAR(64),
        cp.hop_count + 1,
        ac.weight,
        ac.via_publication_ids
    FROM author_collaboration ac
    JOIN collab_path cp ON (ac.author_a = cp.current_node OR ac.author_b = cp.current_node)
    WHERE cp.hop_count < $2
      AND NOT (CASE WHEN ac.author_a = cp.current_node THEN ac.author_b ELSE ac.author_a END = ANY(cp.path_nodes))
)
SELECT 
    cp.current_node AS partner_id,
    a.author_name AS partner_name,
    cp.path_nodes::TEXT[] AS path_nodes,
    cp.hop_count::INTEGER AS hop_count,
    cp.weight::INTEGER AS publication_count,
    cp.via_publication_ids
FROM collab_path cp
JOIN authors a ON a.author_id = cp.current_node
WHERE ($3::TEXT IS NULL OR cp.current_node = $3)
ORDER BY cp.hop_count ASC, cp.weight DESC
LIMIT $4;
""".strip()

# Publication Metadata Query for Provenance enrichment
SQL_PUBLICATIONS_METADATA = """
SELECT publication_id, title, year, doi, eid
FROM publications
WHERE publication_id = ANY($1::TEXT[]);
""".strip()


class GraphRetriever:
    """Deterministic, parameterized knowledge-graph retriever for collaboration networks."""

    @classmethod
    def clamp_limit(cls, limit: int | None) -> int:
        """Clamp query result limit to [1, 50] (FR7.2)."""
        if limit is None or limit <= 0:
            return DEFAULT_LIMIT
        return min(limit, MAX_LIMIT)

    @classmethod
    def clamp_hops(cls, hops: int | None) -> int:
        """Clamp traversal depth to [1, 3] (FR7.2)."""
        if hops is None or hops <= 0:
            return DEFAULT_MAX_HOPS
        return min(hops, MAX_ALLOWED_HOPS)

    @classmethod
    def detect_template(
        cls,
        question: str,
        filters: FilterParams | None = None,
        resolved_author_id: str | None = None,
        resolved_institution_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Determine which template (T1-T4) to execute based on entities and intent."""
        ql = question.lower().strip()

        # Check for path search / multi-hop intent (T4)
        is_path_query = bool(
            re.search(
                r"\b(jalur|path|lintas|hubungan\s*antara|between\s+.*\s+and|hops?|derajat|degree|jaringan\s*(?:ke|dengan))\b",
                ql,
                re.IGNORECASE,
            )
        )

        # 1. Path search (T4)
        if is_path_query:
            if resolved_author_id or (filters and filters.author_name):
                return "T4_AUTHOR", {
                    "source_id": resolved_author_id,
                    "target_id": None,
                }
            if resolved_institution_id or (filters and filters.institution_name):
                return "T4_INSTITUTION", {
                    "source_id": resolved_institution_id,
                    "target_id": None,
                }

        # 2. Author co-authorship (T2)
        if resolved_author_id or (filters and filters.author_name):
            return "T2", {
                "author_id": resolved_author_id,
            }

        # Check author regex in query text if not resolved yet
        if re.search(r"\b(co-?authors?|rekan\s*penulis|teman\s*menulis)\b", ql):
            return "T2", {
                "author_id": resolved_author_id,
            }

        # 3. Institution collaboration (T1)
        if resolved_institution_id or (filters and filters.institution_name):
            return "T1", {
                "institution_id": resolved_institution_id,
            }

        # 4. Topic / Keyword composition (T3)
        keyword_candidate = None
        if filters and filters.keyword:
            keyword_candidate = filters.keyword
        elif filters and filters.topic_name:
            keyword_candidate = filters.topic_name
        else:
            # Extract topic keyword from question e.g. "dalam riset AI", "tentang stem cell", "topik machine learning"
            kw_match = re.search(
                r"\b(?:dalam\s*riset|dalam\s*bidang|tentang|mengenai|topik|keyword)\s+([a-zA-Z0-9\s\-]+?)(?:\s+(?:pada|tahun|di|in|yang)\b|\?|$)",
                question,
                re.IGNORECASE,
            )
            if kw_match:
                candidate = kw_match.group(1).strip()
                if candidate and len(candidate) >= 2:
                    keyword_candidate = candidate

        if keyword_candidate:
            return "T3", {
                "keyword": keyword_candidate,
            }

        # Fallback to T1 if general collaboration query
        return "T1", {
            "institution_id": resolved_institution_id,
        }

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
        max_hops: int | None = None,
    ) -> GraphRetrievalResult:
        """Execute parameterized graph collaboration traversal with safety guardrails."""
        start_time = time.perf_counter()
        clamped_limit = cls.clamp_limit(limit)
        clamped_hops = cls.clamp_hops(max_hops)

        template_name, params = cls.detect_template(
            question,
            filters=filters,
            resolved_author_id=resolved_author_id,
            resolved_institution_id=resolved_institution_id,
        )

        filters_ignored: list[str] = []
        if filters:
            if filters.year is not None or filters.year_from is not None or filters.year_to is not None:
                filters_ignored.append("year")
            if filters.country is not None:
                filters_ignored.append("country")
            if filters.document_type is not None:
                filters_ignored.append("document_type")

        # -------------------------------------------------------------------
        # Template T1: Institution Collaborations
        # -------------------------------------------------------------------
        if template_name == "T1":
            inst_id = params.get("institution_id") or resolved_institution_id
            if not inst_id:
                # No institution ID resolved -> zero evidence
                elapsed = (time.perf_counter() - start_time) * 1000
                return GraphRetrievalResult(
                    template_type="T1",
                    edges=[],
                    publications={},
                    sql_executed=None,
                    target_entity_name=resolved_institution_name,
                    target_entity_id=None,
                    filters_ignored=filters_ignored,
                    execution_time_ms=elapsed,
                )

            sql = SQL_TEMPLATE_T1
            try:
                rows = await asyncio.wait_for(
                    conn.fetch(sql, inst_id, clamped_limit),
                    timeout=STATEMENT_TIMEOUT_S,
                )
            except TimeoutError as exc:
                raise DBTimeoutError("GraphRetriever statement timed out (10s)") from exc

            edges: list[GraphEdgeResult] = []
            all_pub_ids: list[str] = []
            for r in rows:
                via_pubs = list(r["via_publication_ids"] or [])
                all_pub_ids.extend(via_pubs)
                edges.append(
                    GraphEdgeResult(
                        partner_id=str(r["partner_id"]),
                        partner_name=str(r["partner_name"]),
                        publication_count=int(r["publication_count"]),
                        via_publication_ids=via_pubs,
                    )
                )

            pubs_map = await cls._fetch_publication_metadata(conn, all_pub_ids)
            elapsed = (time.perf_counter() - start_time) * 1000

            return GraphRetrievalResult(
                template_type="T1",
                edges=edges,
                publications=pubs_map,
                sql_executed=f"TEMPLATE: SQL_TEMPLATE_T1 (inst_id='{inst_id}', limit={clamped_limit})",
                target_entity_name=resolved_institution_name,
                target_entity_id=inst_id,
                filters_ignored=filters_ignored,
                execution_time_ms=elapsed,
            )

        # -------------------------------------------------------------------
        # Template T2: Author Co-authorship
        # -------------------------------------------------------------------
        if template_name == "T2":
            auth_id = params.get("author_id") or resolved_author_id
            if not auth_id:
                # No author ID resolved -> zero evidence
                elapsed = (time.perf_counter() - start_time) * 1000
                return GraphRetrievalResult(
                    template_type="T2",
                    edges=[],
                    publications={},
                    sql_executed=None,
                    target_entity_name=resolved_author_name,
                    target_entity_id=None,
                    filters_ignored=filters_ignored,
                    execution_time_ms=elapsed,
                )

            sql = SQL_TEMPLATE_T2
            try:
                rows = await asyncio.wait_for(
                    conn.fetch(sql, auth_id, clamped_limit),
                    timeout=STATEMENT_TIMEOUT_S,
                )
            except TimeoutError as exc:
                raise DBTimeoutError("GraphRetriever statement timed out (10s)") from exc

            edges = []
            all_pub_ids = []
            for r in rows:
                via_pubs = list(r["via_publication_ids"] or [])
                all_pub_ids.extend(via_pubs)
                edges.append(
                    GraphEdgeResult(
                        partner_id=str(r["partner_id"]),
                        partner_name=str(r["partner_name"]),
                        publication_count=int(r["publication_count"]),
                        via_publication_ids=via_pubs,
                    )
                )

            pubs_map = await cls._fetch_publication_metadata(conn, all_pub_ids)
            elapsed = (time.perf_counter() - start_time) * 1000

            return GraphRetrievalResult(
                template_type="T2",
                edges=edges,
                publications=pubs_map,
                sql_executed=f"TEMPLATE: SQL_TEMPLATE_T2 (auth_id='{auth_id}', limit={clamped_limit})",
                target_entity_name=resolved_author_name,
                target_entity_id=auth_id,
                filters_ignored=filters_ignored,
                execution_time_ms=elapsed,
            )

        # -------------------------------------------------------------------
        # Template T3: Topic / Keyword Institution Composition
        # -------------------------------------------------------------------
        if template_name == "T3":
            keyword = params.get("keyword", "")
            if not keyword:
                elapsed = (time.perf_counter() - start_time) * 1000
                return GraphRetrievalResult(
                    template_type="T3",
                    edges=[],
                    publications={},
                    sql_executed=None,
                    target_entity_name=None,
                    target_entity_id=None,
                    filters_ignored=filters_ignored,
                    execution_time_ms=elapsed,
                )

            sql = SQL_TEMPLATE_T3
            # T3: LIKE is case-insensitive; escape user wildcard characters so
            # '%' / '_' / '\' in the keyword are treated literally, not as
            # SQL LIKE wildcards (defense against over-matching).
            escaped_keyword = keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped_keyword}%"
            try:
                rows = await asyncio.wait_for(
                    conn.fetch(sql, pattern, clamped_limit),
                    timeout=STATEMENT_TIMEOUT_S,
                )
            except TimeoutError as exc:
                raise DBTimeoutError("GraphRetriever statement timed out (10s)") from exc

            edges = []
            all_pub_ids = []
            for r in rows:
                via_pubs = list(r["via_publication_ids"] or [])
                all_pub_ids.extend(via_pubs)
                edges.append(
                    GraphEdgeResult(
                        partner_id=str(r["partner_id"]),
                        partner_name=str(r["partner_name"]),
                        publication_count=int(r["publication_count"]),
                        via_publication_ids=via_pubs,
                        extra_metadata={"topic_keyword": keyword},
                    )
                )

            pubs_map = await cls._fetch_publication_metadata(conn, all_pub_ids)
            elapsed = (time.perf_counter() - start_time) * 1000

            return GraphRetrievalResult(
                template_type="T3",
                edges=edges,
                publications=pubs_map,
                sql_executed=f"TEMPLATE: SQL_TEMPLATE_T3 (keyword='{keyword}', limit={clamped_limit})",
                target_entity_name=keyword,
                target_entity_id=None,
                filters_ignored=filters_ignored,
                execution_time_ms=elapsed,
            )

        # -------------------------------------------------------------------
        # Template T4: Bounded Recursive CTE Path Search
        # -------------------------------------------------------------------
        is_author_path = (template_name == "T4_AUTHOR")
        source_id = params.get("source_id") or (resolved_author_id if is_author_path else resolved_institution_id)
        target_id = params.get("target_id")

        if not source_id:
            elapsed = (time.perf_counter() - start_time) * 1000
            return GraphRetrievalResult(
                template_type=template_name,
                edges=[],
                publications={},
                sql_executed=None,
                target_entity_name=resolved_author_name if is_author_path else resolved_institution_name,
                target_entity_id=None,
                filters_ignored=filters_ignored,
                execution_time_ms=elapsed,
            )

        sql = SQL_TEMPLATE_T4_AUTHOR if is_author_path else SQL_TEMPLATE_T4_INSTITUTION
        try:
            rows = await asyncio.wait_for(
                conn.fetch(sql, source_id, clamped_hops, target_id, clamped_limit),
                timeout=STATEMENT_TIMEOUT_S,
            )
        except TimeoutError as exc:
            raise DBTimeoutError("GraphRetriever statement timed out (10s)") from exc

        edges = []
        all_pub_ids = []
        for r in rows:
            via_pubs = list(r["via_publication_ids"] or [])
            all_pub_ids.extend(via_pubs)
            path_nodes = list(r["path_nodes"] or [])
            hop_count = int(r["hop_count"]) if r["hop_count"] is not None else 1
            edges.append(
                GraphEdgeResult(
                    partner_id=str(r["partner_id"]),
                    partner_name=str(r["partner_name"]),
                    publication_count=int(r["publication_count"]),
                    via_publication_ids=via_pubs,
                    path_nodes=path_nodes,
                    hop_count=hop_count,
                    extra_metadata={"path": path_nodes, "hops": hop_count},
                )
            )

        pubs_map = await cls._fetch_publication_metadata(conn, all_pub_ids)
        elapsed = (time.perf_counter() - start_time) * 1000

        target_name = resolved_author_name if is_author_path else resolved_institution_name
        return GraphRetrievalResult(
            template_type=template_name,
            edges=edges,
            publications=pubs_map,
                sql_executed=f"TEMPLATE: SQL_TEMPLATE_T4 ({'author' if is_author_path else 'institution'} source='{source_id}', max_hops={clamped_hops}, limit={clamped_limit})",
            target_entity_name=target_name,
            target_entity_id=source_id,
            filters_ignored=filters_ignored,
            execution_time_ms=elapsed,
        )

    @classmethod
    async def _fetch_publication_metadata(
        cls,
        conn: asyncpg.Connection,
        publication_ids: Sequence[str],
    ) -> dict[str, GraphPublicationMeta]:
        """Fetch publication details for supporting provenance IDs."""
        if not publication_ids:
            return {}

        # Deduplicate and cap at top 50 publications for performance
        unique_ids = list(dict.fromkeys(publication_ids))[:50]

        try:
            rows = await asyncio.wait_for(
                conn.fetch(SQL_PUBLICATIONS_METADATA, unique_ids),
                timeout=STATEMENT_TIMEOUT_S,
            )
        except Exception as exc:
            logger.warning("Failed to fetch publication metadata for graph edges: %s", exc)
            return {}

        meta_map: dict[str, GraphPublicationMeta] = {}
        for r in rows:
            pid = str(r["publication_id"])
            meta_map[pid] = GraphPublicationMeta(
                publication_id=pid,
                title=r["title"],
                year=r["year"],
                doi=r["doi"],
                eid=r["eid"],
            )

        if len(publication_ids) > 50:
            logger.warning(
                "_fetch_publication_metadata: truncated to first 50 of %d unique IDs",
                len(publication_ids),
            )

        return meta_map
