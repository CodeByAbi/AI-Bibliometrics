"""Grounded answer synthesizer and evidence object constructor for SQLRoute.

Docs Reference: docs/05 Retrieval Rag Design.md §4, §6, §7; docs/06 Api Design.md §5.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field

from backend.app.models.ask import (
    EvidenceObject,
    FilterParams,
    SourceItem,
)
from backend.app.services.retrievers.graph_retriever import GraphRetrievalResult
from backend.app.services.retrievers.sql_retriever import SqlRetrievalResult
from backend.app.services.retrievers.vector_retriever import (
    VectorRetrievalResult,
)
from backend.app.services.synthesizer.citation import CitationVerifier
from backend.app.services.evidence.formatting import format_citation, format_period
from backend.app.services.evidence.models import EvidenceSet
from backend.app.services.evidence.unifier import EvidenceUnifier

__all__ = [
    "SynthesizedGraphResponse",
    "SynthesizedSqlResponse",
    "SynthesizedVectorResponse",
    "GraphAnswerSynthesizer",
    "SqlAnswerSynthesizer",
    "VectorAnswerSynthesizer",
    "format_citation",
    "format_period",
]


class SynthesizedSqlResponse(BaseModel):
    """Output of SQL grounded answer synthesis."""

    model_config = ConfigDict(frozen=True)

    status: str
    answer: str
    evidence_objects: List[EvidenceObject] = Field(default_factory=list)
    sources: List[SourceItem] = Field(default_factory=list)
    unverified_citations: List[str] = Field(default_factory=list)
    evidence_set: Optional[EvidenceSet] = None


class SynthesizedVectorResponse(BaseModel):
    """Output of Vector semantic search grounded answer synthesis."""

    model_config = ConfigDict(frozen=True)

    status: str
    answer: str
    evidence_objects: List[EvidenceObject] = Field(default_factory=list)
    sources: List[SourceItem] = Field(default_factory=list)
    unverified_citations: List[str] = Field(default_factory=list)
    evidence_set: Optional[EvidenceSet] = None

class SynthesizedGraphResponse(BaseModel):
    """Output of Graph collaboration network grounded answer synthesis."""

    model_config = ConfigDict(frozen=True)

    status: str
    answer: str
    evidence_objects: List[EvidenceObject] = Field(default_factory=list)
    sources: List[SourceItem] = Field(default_factory=list)
    unverified_citations: List[str] = Field(default_factory=list)
    evidence_set: Optional[EvidenceSet] = None



def _format_period(filters: Optional[FilterParams]) -> str:
    """Backward-compat alias for :func:`format_period` (kept for existing imports)."""
    return format_period(filters)


class SqlAnswerSynthesizer:
    """Deterministic, zero-hallucination synthesizer for SQL relational results."""

    @classmethod
    def synthesize(
        cls,
        question: str,
        sql_result: SqlRetrievalResult,
        filters: Optional[FilterParams] = None,
        evidence_set: Optional[EvidenceSet] = None,
    ) -> SynthesizedSqlResponse:
        """Synthesize narrative answer and EvidenceObjects from SQL execution result.

        When ``evidence_set`` is provided (Phase 5 wiring in ``ask.py``), it is
        reused verbatim so unification happens exactly once per request.
        Otherwise it is built via ``EvidenceUnifier.from_sql``.
        """
        # 1. Zero-match short circuit (retrieval-level + EvidenceSet-level).
        if sql_result.is_empty:
            return SynthesizedSqlResponse(
                status="not_found",
                answer="Data tidak ditemukan dalam database untuk kriteria pencarian tersebut.",
                evidence_objects=[],
                sources=[],
                unverified_citations=[],
                evidence_set=None,
            )

        ev_set = evidence_set if evidence_set is not None else EvidenceUnifier.from_sql(
            question, sql_result, filters=filters
        )
        if ev_set.is_empty:
            return SynthesizedSqlResponse(
                status="not_found",
                answer="Data tidak ditemukan dalam database untuk kriteria pencarian tersebut.",
                evidence_objects=[],
                sources=[],
                unverified_citations=[],
                evidence_set=ev_set,
            )

        answer_paragraphs: List[str] = []
        # 2. Deterministic narrative rendering from normalized EvidenceSet
        #    (single source of truth: ev_set.evidence_objects + ev_set.sources).
        for idx, ev in enumerate(ev_set.evidence_objects, 1):
            answer_paragraphs.append(f"{idx}. {ev.claim} {ev.format_citation_tag()}")

        if not ev_set.evidence_objects and ev_set.sources:
            answer_paragraphs.append("Berikut adalah hasil kueri database:")
            for idx, src in enumerate(ev_set.sources, 1):
                item_line = f"{idx}. **{src.title}** ({src.year or 'n.d.'})"
                cite_tag = format_citation(src.title, src.year, src.doi)
                answer_paragraphs.append(f"{item_line} {cite_tag}")

        full_answer = "\n\n".join(answer_paragraphs)

        return SynthesizedSqlResponse(
            status="ok",
            answer=full_answer,
            evidence_objects=ev_set.evidence_objects,
            sources=ev_set.sources,
            unverified_citations=[],
            evidence_set=ev_set,
        )


class VectorAnswerSynthesizer:
    """Deterministic grounded synthesizer for Vector semantic search results."""

    @classmethod
    def synthesize(
        cls,
        question: str,
        vector_result: VectorRetrievalResult,
        filters: Optional[FilterParams] = None,
        evidence_set: Optional[EvidenceSet] = None,
    ) -> SynthesizedVectorResponse:
        """Synthesize grounded narrative answer and EvidenceObjects from Vector search matches.

        When ``evidence_set`` is provided (Phase 5 wiring in ``ask.py``), it is
        reused verbatim so unification happens exactly once per request.
        """
        # 1. Zero-match short circuit (retrieval-level + EvidenceSet-level).
        if vector_result.is_empty:
            return SynthesizedVectorResponse(
                status="not_found",
                answer="Data tidak ditemukan dalam database untuk kriteria pencarian tersebut.",
                evidence_objects=[],
                sources=[],
                unverified_citations=[],
                evidence_set=None,
            )

        ev_set = evidence_set if evidence_set is not None else EvidenceUnifier.from_vector(
            question, vector_result, filters=filters
        )
        if ev_set.is_empty:
            return SynthesizedVectorResponse(
                status="not_found",
                answer="Data tidak ditemukan dalam database untuk kriteria pencarian tersebut.",
                evidence_objects=[],
                sources=[],
                unverified_citations=[],
                evidence_set=ev_set,
            )
        answer_paragraphs: List[str] = []
        answer_paragraphs.append(
            f"Berdasarkan pencarian semantik (ambang similaritas kosinus >= {vector_result.threshold:.2f}), "
            f"ditemukan {len(ev_set.evidence_objects)} publikasi yang relevan dengan kriteria pencarian:"
        )

        # 2. Deterministic narrative rendering from normalized EvidenceSet
        #    (single source of truth: ev_set.evidence_objects).
        # Item lookup is by EvidenceObject -> EvidenceItem via the same
        # deterministic rank order (EvidenceRanker.rank_items), so index i
        # of evidence_objects aligns with index i of items for vector sets.
        items_by_index = list(enumerate(ev_set.items, 1))
        for idx, ev in enumerate(ev_set.evidence_objects, 1):
            claim = ev.claim
            cite_tag = ev.format_citation_tag()
            item_line = f"{idx}. {claim} {cite_tag}"
            if idx <= len(items_by_index):
                _i, snippet_item = items_by_index[idx - 1]
                if snippet_item.source_type == "vector" and snippet_item.content:
                    snippet = snippet_item.content.strip()
                    if len(snippet) > 200:
                        snippet = snippet[:197] + "..."
                    item_line += f"\n   > *Ringkasan Abstrak:* {snippet}"
            answer_paragraphs.append(item_line)

        raw_answer = "\n\n".join(answer_paragraphs)
        verified_res = CitationVerifier.verify(raw_answer, ev_set.sources)

        return SynthesizedVectorResponse(
            status="ok",
            answer=verified_res.cleaned_text,
            evidence_objects=ev_set.evidence_objects,
            sources=ev_set.sources,
            unverified_citations=verified_res.unverified_citations,
            evidence_set=ev_set,
        )

class GraphAnswerSynthesizer:
    """Deterministic grounded synthesizer for Graph collaboration results."""

    @classmethod
    def synthesize(
        cls,
        question: str,
        graph_result: GraphRetrievalResult,
        filters: Optional[FilterParams] = None,
        evidence_set: Optional[EvidenceSet] = None,
    ) -> SynthesizedGraphResponse:
        """Synthesize grounded narrative answer and EvidenceObjects from Graph search results."""
        # 1. Zero-match short circuit (retrieval-level + EvidenceSet-level).
        if graph_result.is_empty:
            return SynthesizedGraphResponse(
                status="not_found",
                answer="Data tidak ditemukan dalam database untuk kriteria pencarian tersebut.",
                evidence_objects=[],
                sources=[],
                unverified_citations=[],
                evidence_set=None,
            )

        ev_set = evidence_set if evidence_set is not None else EvidenceUnifier.from_graph(
            question, graph_result, filters=filters
        )
        if ev_set.is_empty:
            return SynthesizedGraphResponse(
                status="not_found",
                answer="Data tidak ditemukan dalam database untuk kriteria pencarian tersebut.",
                evidence_objects=[],
                sources=[],
                unverified_citations=[],
                evidence_set=ev_set,
            )

        answer_paragraphs: List[str] = []
        entity_label = graph_result.target_entity_name or "entitas terkait"
        if graph_result.template_type == "T1":
            intro = f"Berdasarkan penelusuran jaringan kolaborasi institusi ({entity_label}), ditemukan {len(ev_set.evidence_objects)} mitra kolaborasi:"
        elif graph_result.template_type == "T2":
            intro = f"Berdasarkan penelusuran jaringan co-authorship penulis ({entity_label}), ditemukan {len(ev_set.evidence_objects)} rekan penulis (co-authors):"
        elif graph_result.template_type == "T3":
            intro = f"Berdasarkan penelusuran kolaborasi pada topik '{entity_label}', ditemukan {len(ev_set.evidence_objects)} institusi terkait:"
        elif graph_result.template_type in ("T4", "T4_AUTHOR", "T4_INSTITUTION"):
            intro = f"Berdasarkan penelusuran jalur kolaborasi multi-hop ({entity_label}), ditemukan {len(ev_set.evidence_objects)} jalur terhubung:"
        else:
            intro = f"Berdasarkan penelusuran jaringan kolaborasi, ditemukan {len(ev_set.evidence_objects)} relasi kolaborasi:"

        answer_paragraphs.append(intro)

        for idx, ev in enumerate(ev_set.evidence_objects, 1):
            claim = ev.claim
            cite_tag = ev.format_citation_tag()
            item_line = f"{idx}. {claim} {cite_tag}".rstrip()
            answer_paragraphs.append(item_line)

        raw_answer = "\n\n".join(answer_paragraphs)
        verified_res = CitationVerifier.verify(raw_answer, ev_set.sources)

        return SynthesizedGraphResponse(
            status="ok",
            answer=verified_res.cleaned_text,
            evidence_objects=ev_set.evidence_objects,
            sources=ev_set.sources,
            unverified_citations=verified_res.unverified_citations,
            evidence_set=ev_set,
        )
