"""EvidenceUnifier: Normalization and multi-source deduplication into canonical EvidenceSet.

Docs Reference: docs/05 Retrieval Rag Design.md §4, §5; docs/10 Implementation Plan.md §1 (Task 7); docs/11 Roadmap.md (Fase 5).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Union

from backend.app.models.ask import (
    EvidenceObject,
    EvidenceSourceRef,
    FilterParams,
    SourceItem,
)
from backend.app.services.evidence.formatting import (
    format_citation,
)
from backend.app.services.evidence.formatting import (
    format_period as _format_period,
)
from backend.app.services.evidence.models import EvidenceItem, EvidenceSet
from backend.app.services.evidence.ranker import EvidenceRanker
from backend.app.services.retrievers.graph_retriever import (
    GraphEdgeResult,
    GraphPublicationMeta,
    GraphRetrievalResult,
)
from backend.app.services.retrievers.hybrid_retriever import (
    HybridRetrievalResult,
)
from backend.app.services.retrievers.sql_retriever import SqlRetrievalResult
from backend.app.services.retrievers.vector_retriever import VectorRetrievalResult

__all__ = ["EvidenceUnifier", "format_citation"]


class EvidenceUnifier:
    """Normalizes heterogeneous retrieval outputs (SQL, Vector, Graph, Analytics) into a canonical EvidenceSet."""

    _logger = logging.getLogger("evidence.unifier")

    @classmethod
    def from_sql(
        cls,
        question: str,
        result: SqlRetrievalResult,
        filters: Optional[FilterParams] = None,
    ) -> EvidenceSet:
        """Normalize SqlRetrievalResult into a canonical EvidenceSet."""
        if result.is_empty:
            return EvidenceSet(
                query=question,
                evidence_objects=[],
                sources=[],
                items=[],
                filters_ignored=list(result.filters_ignored),
                sql_executed=result.sql_executed,
            )

        rows = result.rows
        cols = set(result.columns)
        period_str = _format_period(filters)

        evidence_objects: List[EvidenceObject] = []
        sources: List[SourceItem] = []
        items: List[EvidenceItem] = []

        # Case A: Single aggregate scalar (e.g., total_publications / count)
        if len(rows) == 1 and ("total_publications" in cols or "count" in cols):
            val = rows[0].get("total_publications", rows[0].get("count", 0))

            # A scalar aggregate of 0 is not evidence (W8).
            #
            # The row exists, so `result.is_empty` is False and the request
            # returned `status="ok"` with a confident, well-formed claim reading
            # "total publikasi tercatat sebanyak 0". But a COUNT of zero means
            # the retrieval found nothing — which is precisely the condition the
            # zero-evidence invariant (<200 ms, `status="not_found"`) exists to
            # short-circuit. Treating it as a finding gave a not-found answer the
            # appearance of a measured result.
            #
            # This is also what masked a real defect: "between 2021 and 2023"
            # silently became `p.year = 2021`, reported 21 publications, and the
            # wrong year predicate was never questioned because 21 is a
            # perfectly respectable-looking answer. Fixing the year parser
            # removes the cause; this removes the class of masking.
            numeric_val = val if isinstance(val, (int, float)) else None
            if numeric_val is not None and float(numeric_val) == 0.0:
                return EvidenceSet(
                    query=question,
                    evidence_objects=[],
                    sources=[],
                    items=[],
                    filters_ignored=list(result.filters_ignored),
                    sql_executed=result.sql_executed,
                    zero_evidence_class="zero_aggregate",
                )

            claim_text = f"Berdasarkan data database, total publikasi tercatat sebanyak {val}."
            if filters and filters.year:
                claim_text = f"Berdasarkan data database, total publikasi pada tahun {filters.year} adalah {val}."

            ev = EvidenceObject(
                claim=claim_text,
                metric="publication_count",
                value=int(val) if isinstance(val, (int, float)) else str(val),
                period=period_str,
                sources=[],
                confidence=EvidenceRanker.calculate_sql_confidence(),
            )
            evidence_objects.append(ev)
            items.append(
                EvidenceItem(
                    source_id="sql_scalar_count",
                    source_type="sql",
                    content=claim_text,
                    score=1.0,
                    confidence=1.0,
                    metadata={"raw_row": rows[0]},
                )
            )

        # Case B: Author rankings (author_name + publication_count/count)
        elif "author_name" in cols and ("publication_count" in cols or "count" in cols):
            for idx, r in enumerate(rows, 1):
                name = r.get("author_name", "Unknown")
                count_val = r.get("publication_count", r.get("count", 0))
                claim_text = f"Penulis {name} memiliki {count_val} publikasi dalam database ({period_str})"

                evidence_objects.append(
                    EvidenceObject(
                        claim=claim_text,
                        metric="publication_count",
                        value=int(count_val) if isinstance(count_val, (int, float)) else str(count_val),
                        period=period_str,
                        sources=[],
                        confidence=EvidenceRanker.calculate_sql_confidence(),
                    )
                )
                items.append(
                    EvidenceItem(
                        source_id=f"sql_author_{idx}",
                        source_type="sql",
                        content=f"Author: {name}, Publications: {count_val}",
                        score=1.0,
                        confidence=1.0,
                        metadata={"author_name": name, "publication_count": count_val},
                    )
                )

        # Case C: Institution rankings (institution_name + publication_count/count)
        elif "institution_name" in cols and ("publication_count" in cols or "count" in cols):
            for idx, r in enumerate(rows, 1):
                name = r.get("institution_name", "Unknown")
                count_val = r.get("publication_count", r.get("count", 0))
                claim_text = f"Institusi {name} memiliki {count_val} publikasi dalam database ({period_str})"

                evidence_objects.append(
                    EvidenceObject(
                        claim=claim_text,
                        metric="publication_count",
                        value=int(count_val) if isinstance(count_val, (int, float)) else str(count_val),
                        period=period_str,
                        sources=[],
                        confidence=EvidenceRanker.calculate_sql_confidence(),
                    )
                )
                items.append(
                    EvidenceItem(
                        source_id=f"sql_institution_{idx}",
                        source_type="sql",
                        content=f"Institution: {name}, Publications: {count_val}",
                        score=1.0,
                        confidence=1.0,
                        metadata={"institution_name": name, "publication_count": count_val},
                    )
                )

        # Case D: Publication list (publication_id, title, year, citation_count, doi)
        elif "title" in cols:
            for idx, r in enumerate(rows, 1):
                pub_id = str(r.get("publication_id", f"pub_{idx}"))
                title = r.get("title", "Untitled")
                year = r.get("year")
                doi = r.get("doi")
                citations = r.get("citation_count", 0)

                src_ref = EvidenceSourceRef(
                    publication_id=pub_id,
                    doi=doi,
                    eid=r.get("eid"),
                    title=title,
                    year=int(year) if year is not None else None,
                )

                if citations is not None:
                    evidence_objects.append(
                        EvidenceObject(
                            claim=f"Publikasi '{title}' memiliki {citations} sitasi dalam database",
                            metric="citation_count",
                            value=int(citations) if isinstance(citations, (int, float)) else str(citations),
                            period=str(year) if year is not None else period_str,
                            sources=[src_ref],
                            confidence=EvidenceRanker.calculate_sql_confidence(),
                        )
                    )

                sources.append(
                    SourceItem(
                        publication_id=pub_id,
                        title=title,
                        year=int(year) if year is not None else None,
                        doi=doi,
                        source_type="sql",
                        relevance_score=1.0,
                        provenance=f"sql_row:{idx}",
                    )
                )

                items.append(
                    EvidenceItem(
                        source_id=pub_id,
                        source_type="sql",
                        content=f"Publication: '{title}' ({year or 'n.d.'}), citations: {citations}",
                        score=1.0,
                        confidence=1.0,
                        publication_id=pub_id,
                        title=title,
                        year=int(year) if year is not None else None,
                        doi=doi,
                        eid=r.get("eid"),
                        provenance_ids=[f"sql_row:{idx}"],
                        metadata={"citation_count": citations},
                    )
                )

        # Case E: Generic table output
        else:
            evidence_objects.append(
                EvidenceObject(
                    claim=f"Kueri database mengembalikan {len(rows)} baris ({period_str})",
                    metric="publication_count",
                    value=len(rows),
                    period=period_str,
                    sources=[],
                    confidence=EvidenceRanker.calculate_sql_confidence(),
                )
            )
            for idx, r in enumerate(rows, 1):
                row_str = ", ".join(f"{k}: {v}" for k, v in r.items() if v is not None)
                items.append(
                    EvidenceItem(
                        source_id=f"sql_generic_{idx}",
                        source_type="sql",
                        content=row_str,
                        score=1.0,
                        confidence=1.0,
                        metadata={"row_index": idx},
                    )
                )

        ranked_ev = EvidenceRanker.rank_evidence_objects(evidence_objects)
        ranked_src = EvidenceRanker.rank_sources(sources)
        ranked_items = EvidenceRanker.rank_items(items)

        return EvidenceSet(
            query=question,
            evidence_objects=ranked_ev,
            sources=ranked_src,
            items=ranked_items,
            filters_ignored=list(result.filters_ignored),
            sql_executed=result.sql_executed,
        )

    @classmethod
    def from_vector(
        cls,
        question: str,
        result: VectorRetrievalResult,
        filters: Optional[FilterParams] = None,
    ) -> EvidenceSet:
        """Normalize VectorRetrievalResult into a canonical EvidenceSet."""
        if result.is_empty:
            return EvidenceSet(
                query=question,
                evidence_objects=[],
                sources=[],
                items=[],
                filters_ignored=list(result.filters_ignored),
                sql_executed=result.sql_executed,
            )

        matches = result.matches
        period_str = _format_period(filters)

        evidence_objects: List[EvidenceObject] = []
        sources: List[SourceItem] = []
        items: List[EvidenceItem] = []

        for m in matches:
            src_ref = EvidenceSourceRef(
                publication_id=m.publication_id,
                doi=m.doi,
                eid=m.eid,
                title=m.title,
                year=m.year,
            )

            conf = EvidenceRanker.calculate_vector_confidence(m.similarity_score)

            evidence_objects.append(
                EvidenceObject(
                    claim=f"Publikasi '{m.title}' teridentifikasi relevan dengan topik kueri (skor kemiripan kosinus: {m.similarity_score:.2f})",
                    metric="similarity_score",
                    value=round(m.similarity_score, 4),
                    period=str(m.year) if m.year is not None else period_str,
                    sources=[src_ref],
                    confidence=conf,
                )
            )

            sources.append(
                SourceItem(
                    publication_id=m.publication_id,
                    title=m.title,
                    year=m.year,
                    doi=m.doi,
                    source_type="vector",
                    relevance_score=round(m.similarity_score, 4),
                    provenance=f"chunk_id:{m.chunk_id}",
                )
            )

            items.append(
                EvidenceItem(
                    source_id=m.chunk_id,
                    source_type="vector",
                    content=m.chunk_text,
                    score=m.similarity_score,
                    confidence=conf,
                    publication_id=m.publication_id,
                    title=m.title,
                    year=m.year,
                    doi=m.doi,
                    eid=m.eid,
                    provenance_ids=[f"chunk_id:{m.chunk_id}"],
                    metadata={"citation_count": m.citation_count},
                )
            )

        ranked_ev = EvidenceRanker.rank_evidence_objects(evidence_objects)
        ranked_src = EvidenceRanker.rank_sources(sources)
        ranked_items = EvidenceRanker.rank_items(items)

        return EvidenceSet(
            query=question,
            evidence_objects=ranked_ev,
            sources=ranked_src,
            items=ranked_items,
            filters_ignored=list(result.filters_ignored),
            sql_executed=result.sql_executed,
        )

    @classmethod
    def from_graph(
        cls,
        question: str,
        result_or_edges: Optional[Union[GraphRetrievalResult, Sequence[Dict[str, Any]], Sequence[GraphEdgeResult]]] = None,
        filters: Optional[FilterParams] = None,
        sql_executed: Optional[str] = None,
        publications: Optional[Dict[str, Any]] = None,
        *,
        edges: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> EvidenceSet:
        """Normalize Graph collaboration edge results into a canonical EvidenceSet.

        Builds EvidenceObjects, EvidenceItems, and SourceItems with rich provenance
        and publication references.
        """
        target = result_or_edges if result_or_edges is not None else edges
        if isinstance(target, GraphRetrievalResult):
            raw_edges: Sequence[Any] = target.edges
            pubs_map: Dict[str, Any] = target.publications
            sql_exec: Optional[str] = target.sql_executed or sql_executed
            filters_ign: List[str] = list(target.filters_ignored)
        elif isinstance(target, (list, tuple)):
            raw_edges = target
            pubs_map = publications or {}
            sql_exec = sql_executed
            filters_ign = []
        else:
            raw_edges = []
            pubs_map = {}
            sql_exec = sql_executed
            filters_ign = []

        if not raw_edges:
            return EvidenceSet(
                query=question,
                evidence_objects=[],
                sources=[],
                items=[],
                filters_ignored=filters_ign,
                sql_executed=sql_exec,
            )

        period_str = _format_period(filters)
        evidence_objects: List[EvidenceObject] = []
        sources_dict: Dict[str, SourceItem] = {}
        items: List[EvidenceItem] = []
        skipped_edges: List[str] = []

        for idx, edge in enumerate(raw_edges, 1):
            if isinstance(edge, dict):
                partner_name = str(edge.get("partner_name") or edge.get("partner_id") or "Unknown")
                partner_id = str(edge.get("partner_id") or f"edge_{idx}")
                count_val = edge.get("publication_count", edge.get("weight", 0))
                via_pubs = edge.get("via_publication_ids") or []
                hop_count = edge.get("hop_count")
                extra_meta = dict(edge)
            else:
                partner_name = str(edge.partner_name or edge.partner_id or "Unknown")
                partner_id = str(edge.partner_id or f"edge_{idx}")
                count_val = edge.publication_count
                via_pubs = edge.via_publication_ids or []
                hop_count = edge.hop_count
                extra_meta = dict(edge.extra_metadata) if hasattr(edge, "extra_metadata") else {}

            if isinstance(via_pubs, str):
                via_pubs = [via_pubs]

            topic_kw = extra_meta.get("topic_keyword")
            if hop_count and hop_count > 1:
                claim_text = (
                    f"Jalur kolaborasi dengan {partner_name} terhubung sejauh {hop_count} hop dengan {count_val} publikasi bersama ({period_str})"
                )
            elif topic_kw:
                claim_text = (
                    f"Kolaborasi institusi {partner_name} pada topik '{topic_kw}' tercatat sebanyak {count_val} publikasi ({period_str})"
                )
            else:
                claim_text = (
                    f"Kolaborasi dengan {partner_name} tercatat sebanyak {count_val} publikasi bersama ({period_str})"
                )

            # Build supporting publication source refs for this edge
            edge_sources: List[EvidenceSourceRef] = []
            for pid in via_pubs:
                pid_str = str(pid)
                if pid_str in pubs_map:
                    pm = pubs_map[pid_str]
                    title = pm.title if hasattr(pm, "title") else pm.get("title")
                    year = pm.year if hasattr(pm, "year") else pm.get("year")
                    doi = pm.doi if hasattr(pm, "doi") else pm.get("doi")
                    eid = pm.eid if hasattr(pm, "eid") else pm.get("eid")
                    edge_sources.append(
                        EvidenceSourceRef(
                            publication_id=pid_str,
                            title=title,
                            year=year,
                            doi=doi,
                            eid=eid,
                        )
                    )
                    if pid_str not in sources_dict:
                        sources_dict[pid_str] = SourceItem(
                            publication_id=pid_str,
                            title=title or f"Publication {pid_str}",
                            year=year,
                            doi=doi,
                            source_type="graph",
                            relevance_score=1.0,
                            provenance=f"via kolaborasi dengan {partner_name}",
                        )

            # Fail-closed: an edge with no resolvable publication provenance
            # produces no verifiable citation (format_citation_tag returns ""
            # for empty sources). Drop the edge rather than emit an
            # un-citable claim, keeping the zero-hallucination invariant intact.
            if not edge_sources:
                skipped_edges.append(f"{partner_id} ({partner_name})")
                continue

            confidence = EvidenceRanker.calculate_graph_confidence()
            evidence_objects.append(
                EvidenceObject(
                    claim=claim_text,
                    metric="publication_count",
                    value=int(count_val) if isinstance(count_val, (int, float)) else str(count_val),
                    period=period_str,
                    sources=edge_sources,
                    confidence=confidence,
                )
            )

            items.append(
                EvidenceItem(
                    source_id=f"graph_edge_{partner_id}",
                    source_type="graph",
                    content=f"Partner: {partner_name}, Weight: {count_val}",
                    score=float(count_val) if isinstance(count_val, (int, float)) else 1.0,
                    confidence=confidence,
                    provenance_ids=[str(pid) for pid in via_pubs],
                    metadata=extra_meta,
                )
            )

        if skipped_edges:
            cls._logger.info(
                "from_graph: skipped %d edge(s) with unresolvable publication provenance: %s",
                len(skipped_edges),
                ", ".join(skipped_edges[:10]),
            )

        ranked_ev = EvidenceRanker.rank_evidence_objects(evidence_objects)
        ranked_src = EvidenceRanker.rank_sources(list(sources_dict.values()))
        ranked_items = EvidenceRanker.rank_items(items)

        return EvidenceSet(
            query=question,
            evidence_objects=ranked_ev,
            sources=ranked_src,
            items=ranked_items,
            filters_ignored=filters_ign,
            sql_executed=sql_exec,
        )
    @classmethod
    def from_hybrid(
        cls,
        question: str,
        result: HybridRetrievalResult,
        filters: Optional[FilterParams] = None,
    ) -> EvidenceSet:
        """Normalize HybridRetrievalResult (topics, evolution, expertise, publications) into EvidenceSet."""
        if result.is_empty:
            return EvidenceSet(
                query=question,
                evidence_objects=[],
                sources=[],
                items=[],
                filters_ignored=list(result.filters_ignored),
                sql_executed=result.sql_executed,
            )

        period_str = _format_period(filters)
        evidence_objects: List[EvidenceObject] = []
        sources: List[SourceItem] = []
        items: List[EvidenceItem] = []

        # 1. Normalize supporting publication metadata into SourceItems and EvidenceSourceRefs
        pub_refs: Dict[str, EvidenceSourceRef] = {}
        for pid, pub in sorted(result.publications.items(), key=lambda kv: kv[0]):
            ref = EvidenceSourceRef(
                publication_id=pub.publication_id,
                doi=pub.doi,
                eid=pub.eid,
                title=pub.title,
                year=pub.year,
            )
            pub_refs[pid] = ref
            sources.append(
                SourceItem(
                    publication_id=pub.publication_id,
                    title=pub.title,
                    year=pub.year,
                    doi=pub.doi,
                    source_type="analytics",
                    relevance_score=1.0,
                    provenance=f"publication_id:{pub.publication_id}",
                )
            )

        # 2. Normalize topic evolution records
        for item in result.topics:
            emerging_tag = " (topik berkembang pesat)" if item.is_emerging else ""
            claim_text = (
                f"Topik '{item.topic_name}' tahun {item.year}: {item.publication_count} publikasi, "
                f"{item.citation_count} sitasi, pertumbuhan YoY {item.growth_score:.4f}, "
                f"akselerasi sitasi {item.citation_acceleration:.4f}{emerging_tag} ({period_str})"
            )

            evidence_objects.append(
                EvidenceObject(
                    claim=claim_text,
                    metric="growth_score",
                    value=round(item.growth_score, 4),
                    period=str(item.year),
                    sources=[],
                    confidence=EvidenceRanker.calculate_analytics_confidence(),
                )
            )

            items.append(
                EvidenceItem(
                    source_id=f"topic_evolution_{item.topic_id}_{item.year}",
                    source_type="analytics",
                    content=(
                        f"Topik: {item.topic_name}, Tahun: {item.year}, Publikasi: {item.publication_count}, "
                        f"Sitasi: {item.citation_count}, GrowthScore: {item.growth_score:.4f}, "
                        f"CitationAcceleration: {item.citation_acceleration:.4f}, Emerging: {item.is_emerging}"
                    ),
                    score=1.0,
                    confidence=EvidenceRanker.calculate_analytics_confidence(),
                    metadata={
                        "topic_id": item.topic_id,
                        "topic_name": item.topic_name,
                        "year": item.year,
                        "publication_count": item.publication_count,
                        "citation_count": item.citation_count,
                        "growth_score": item.growth_score,
                        "citation_acceleration": item.citation_acceleration,
                        "recency_weight": item.recency_weight,
                        "is_emerging": item.is_emerging,
                    },
                )
            )

        # 3. Normalize researcher expertise records
        all_pub_refs = list(pub_refs.values())
        for item in result.experts:
            linked_sources = all_pub_refs[:2] if all_pub_refs else []

            claim_text = (
                f"Peneliti {item.author_name} teridentifikasi sebagai pakar pada topik '{item.topic_name}' "
                f"dengan skor kepakaran {item.expertise_score:.4f} (relevansi: {item.relevance_score:.2f}, "
                f"produktivitas: {item.productivity_score:.2f}, dampak sitasi: {item.impact_score:.2f}, "
                f"kebaruan: {item.recency_score:.2f}, h-index topik: {item.h_index_topic}, "
                f"kolaborator: {item.coauthor_network_size}) ({period_str})"
            )

            evidence_objects.append(
                EvidenceObject(
                    claim=claim_text,
                    metric="expertise_score",
                    value=round(item.expertise_score, 4),
                    period=period_str,
                    sources=linked_sources,
                    confidence=EvidenceRanker.calculate_analytics_confidence(),
                )
            )

            items.append(
                EvidenceItem(
                    source_id=f"researcher_expertise_{item.author_id}_{item.topic_id}",
                    source_type="analytics",
                    content=(
                        f"Pakar: {item.author_name} (ID: {item.author_id}), Topik: {item.topic_name}, "
                        f"Skor Kepakaran: {item.expertise_score:.4f}, Relevansi: {item.relevance_score:.2f}, "
                        f"Produktivitas: {item.productivity_score:.2f}, Dampak: {item.impact_score:.2f}, "
                        f"Kebaruan: {item.recency_score:.2f}, H-Index Topik: {item.h_index_topic}, "
                        f"Publikasi Topik: {item.publication_count_topic}, Sitasi Topik: {item.citation_count_topic}, "
                        f"Jejaring: {item.coauthor_network_size}"
                    ),
                    score=1.0,
                    confidence=EvidenceRanker.calculate_analytics_confidence(),
                    metadata={
                        "author_id": item.author_id,
                        "author_name": item.author_name,
                        "topic_id": item.topic_id,
                        "topic_name": item.topic_name,
                        "expertise_score": item.expertise_score,
                        "relevance_score": item.relevance_score,
                        "productivity_score": item.productivity_score,
                        "impact_score": item.impact_score,
                        "recency_score": item.recency_score,
                        "h_index_topic": item.h_index_topic,
                        "publication_count_topic": item.publication_count_topic,
                        "citation_count_topic": item.citation_count_topic,
                        "coauthor_network_size": item.coauthor_network_size,
                    },
                )
            )

        ranked_ev = EvidenceRanker.rank_evidence_objects(evidence_objects)
        ranked_src = EvidenceRanker.rank_sources(sources)
        ranked_items = EvidenceRanker.rank_items(items)

        return EvidenceSet(
            query=question,
            evidence_objects=ranked_ev,
            sources=ranked_src,
            items=ranked_items,
            filters_ignored=list(result.filters_ignored),
            sql_executed=result.sql_executed,
        )

    @classmethod
    def from_analytics(
        cls,
        question: str,
        rows: List[Dict[str, Any]],
        filters: Optional[FilterParams] = None,
        sql_executed: Optional[str] = None,
    ) -> EvidenceSet:
        """Normalize Gold analytics rows (topic evolution, researcher expertise) into EvidenceSet."""
        if not rows:
            return EvidenceSet(
                query=question,
                evidence_objects=[],
                sources=[],
                items=[],
                filters_ignored=[],
                sql_executed=sql_executed,
            )

        period_str = _format_period(filters)
        evidence_objects: List[EvidenceObject] = []
        sources: List[SourceItem] = []
        items: List[EvidenceItem] = []

        for idx, r in enumerate(rows, 1):
            if "expertise_score" in r and "author_name" in r:
                name = r.get("author_name", "Unknown")
                score_val = r.get("expertise_score", 0.0)
                topic = r.get("topic_name", "topik terkait")
                claim_text = f"Peneliti {name} memiliki skor kepakaran {score_val} pada {topic} ({period_str})"

                evidence_objects.append(
                    EvidenceObject(
                        claim=claim_text,
                        metric="expertise_score",
                        value=float(score_val) if isinstance(score_val, (int, float)) else str(score_val),
                        period=period_str,
                        sources=[],
                        confidence=EvidenceRanker.calculate_analytics_confidence(),
                    )
                )
            elif "growth_score" in r and "topic_name" in r:
                topic = r.get("topic_name", "Unknown")
                growth = r.get("growth_score", 0.0)
                claim_text = f"Topik {topic} memiliki pertumbuhan (growth_score) sebesar {growth} ({period_str})"

                evidence_objects.append(
                    EvidenceObject(
                        claim=claim_text,
                        metric="growth_score",
                        value=float(growth) if isinstance(growth, (int, float)) else str(growth),
                        period=period_str,
                        sources=[],
                        confidence=EvidenceRanker.calculate_analytics_confidence(),
                    )
                )

            items.append(
                EvidenceItem(
                    source_id=f"analytics_row_{idx}",
                    source_type="analytics",
                    content=", ".join(f"{k}: {v}" for k, v in r.items() if v is not None),
                    score=1.0,
                    confidence=EvidenceRanker.calculate_analytics_confidence(),
                    metadata=dict(r),
                )
            )

        ranked_ev = EvidenceRanker.rank_evidence_objects(evidence_objects)
        ranked_src = EvidenceRanker.rank_sources(sources)
        ranked_items = EvidenceRanker.rank_items(items)

        return EvidenceSet(
            query=question,
            evidence_objects=ranked_ev,
            sources=ranked_src,
            items=ranked_items,
            filters_ignored=[],
            sql_executed=sql_executed,
        )

    @classmethod
    def unify(
        cls,
        evidence_sets: Sequence[EvidenceSet],
        query: Optional[str] = None,
    ) -> EvidenceSet:
        """Merge, deduplicate, and deterministically rank multiple EvidenceSets across heterogenous sources."""
        if not evidence_sets:
            return EvidenceSet(
                query=query or "",
                evidence_objects=[],
                sources=[],
                items=[],
                filters_ignored=[],
                sql_executed=None,
            )

        target_query = query or evidence_sets[0].query

        # 1. Deduplicate Sources by publication_id
        merged_sources_map: Dict[str, SourceItem] = {}
        for ev_set in evidence_sets:
            for src in ev_set.sources:
                pub_id = src.publication_id
                if pub_id not in merged_sources_map:
                    merged_sources_map[pub_id] = src
                else:
                    existing = merged_sources_map[pub_id]
                    # Select maximum relevance_score
                    best_relevance = existing.relevance_score
                    if src.relevance_score is not None:
                        if best_relevance is None or src.relevance_score > best_relevance:
                            best_relevance = src.relevance_score

                    # Merge provenance
                    merged_prov = existing.provenance
                    if src.provenance and src.provenance != existing.provenance:
                        if merged_prov:
                            merged_prov = f"{merged_prov}, {src.provenance}"
                        else:
                            merged_prov = src.provenance

                    # Keep richest metadata
                    merged_sources_map[pub_id] = SourceItem(
                        publication_id=pub_id,
                        title=existing.title or src.title,
                        year=existing.year or src.year,
                        doi=existing.doi or src.doi,
                        source_type=existing.source_type,
                        relevance_score=best_relevance,
                        provenance=merged_prov,
                    )

        # 2. Deduplicate EvidenceObjects by (metric, claim, value, period)
        merged_ev_map: Dict[tuple, EvidenceObject] = {}
        for ev_set in evidence_sets:
            for ev in ev_set.evidence_objects:
                key = (ev.metric, ev.claim, str(ev.value), ev.period)
                if key not in merged_ev_map:
                    merged_ev_map[key] = ev
                else:
                    existing = merged_ev_map[key]
                    # Merge supporting sources
                    src_by_pub: Dict[str, EvidenceSourceRef] = {s.publication_id: s for s in existing.sources}
                    for s in ev.sources:
                        if s.publication_id not in src_by_pub:
                            src_by_pub[s.publication_id] = s
                    merged_ev_map[key] = EvidenceObject(
                        claim=existing.claim,
                        metric=existing.metric,
                        value=existing.value,
                        period=existing.period,
                        sources=list(src_by_pub.values()),
                        confidence=max(existing.confidence, ev.confidence),
                    )

        # 3. Deduplicate EvidenceItems by (source_type, source_id)
        merged_items_map: Dict[tuple, EvidenceItem] = {}
        for ev_set in evidence_sets:
            for it in ev_set.items:
                key = (it.source_type, it.source_id)
                if key not in merged_items_map:
                    merged_items_map[key] = it
                else:
                    existing = merged_items_map[key]
                    merged_items_map[key] = EvidenceItem(
                        source_id=existing.source_id,
                        source_type=existing.source_type,
                        content=existing.content if len(existing.content) >= len(it.content) else it.content,
                        score=max(existing.score, it.score),
                        confidence=max(existing.confidence, it.confidence),
                        publication_id=existing.publication_id or it.publication_id,
                        title=existing.title or it.title,
                        year=existing.year or it.year,
                        doi=existing.doi or it.doi,
                        eid=existing.eid or it.eid,
                        provenance_ids=list(dict.fromkeys(existing.provenance_ids + it.provenance_ids)),
                        metadata={**it.metadata, **existing.metadata},
                    )

        # 4. Merge filters_ignored
        merged_filters_ignored: List[str] = []
        for ev_set in evidence_sets:
            for f in ev_set.filters_ignored:
                if f not in merged_filters_ignored:
                    merged_filters_ignored.append(f)

        # 5. Merge sql_executed
        sql_stmts = [
            ev_set.sql_executed
            for ev_set in evidence_sets
            if ev_set.sql_executed and ev_set.sql_executed.strip()
        ]
        merged_sql = "; ".join(dict.fromkeys(sql_stmts)) if sql_stmts else None

        # 6. Deterministically rank all components
        ranked_ev = EvidenceRanker.rank_evidence_objects(list(merged_ev_map.values()))
        ranked_src = EvidenceRanker.rank_sources(list(merged_sources_map.values()))
        ranked_items = EvidenceRanker.rank_items(list(merged_items_map.values()))

        return EvidenceSet(
            query=target_query,
            evidence_objects=ranked_ev,
            sources=ranked_src,
            items=ranked_items,
            filters_ignored=merged_filters_ignored,
            sql_executed=merged_sql,
        )
