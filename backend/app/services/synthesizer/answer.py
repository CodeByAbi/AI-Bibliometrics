"""Grounded answer synthesizer and evidence object constructor for SQLRoute.

Docs Reference: docs/05 Retrieval Rag Design.md §4, §6, §7; docs/06 Api Design.md §5.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field

from backend.app.models.ask import (
    EvidenceObject,
    FilterParams,
    SourceItem,
)
from backend.app.services.retrievers.sql_retriever import SqlRetrievalResult
from backend.app.services.retrievers.vector_retriever import (
    VectorMatchItem,
    VectorRetrievalResult,
)
from backend.app.services.synthesizer.citation import CitationVerifier
from backend.app.services.evidence.formatting import format_citation, format_period
from backend.app.services.evidence.models import EvidenceSet
from backend.app.services.evidence.unifier import EvidenceUnifier

__all__ = [
    "SynthesizedSqlResponse",
    "SynthesizedVectorResponse",
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


class SynthesizedVectorResponse(BaseModel):
    """Output of Vector semantic search grounded answer synthesis."""

    model_config = ConfigDict(frozen=True)

    status: str
    answer: str
    evidence_objects: List[EvidenceObject] = Field(default_factory=list)
    sources: List[SourceItem] = Field(default_factory=list)
    unverified_citations: List[str] = Field(default_factory=list)


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
    ) -> SynthesizedSqlResponse:
        """Synthesize narrative answer and EvidenceObjects from SQL execution result."""
        # 1. Zero-match short circuit
        if sql_result.is_empty:
            return SynthesizedSqlResponse(
                status="not_found",
                answer="Data tidak ditemukan dalam database untuk kriteria pencarian tersebut.",
                evidence_objects=[],
                sources=[],
                unverified_citations=[],
            )

        ev_set = EvidenceUnifier.from_sql(question, sql_result, filters=filters)
        rows = sql_result.rows
        cols = set(sql_result.columns)
        answer_paragraphs: List[str] = []
        # Case A: Single aggregate scalar (e.g., total_publications)
        if len(rows) == 1 and ("total_publications" in cols or "count" in cols):
            val = rows[0].get("total_publications", rows[0].get("count", 0))
            claim_text = f"Berdasarkan data database, total publikasi tercatat sebanyak {val}."
            if filters and filters.year:
                claim_text = f"Berdasarkan data database, total publikasi pada tahun {filters.year} adalah {val}."
            answer_paragraphs.append(claim_text)

        # Case B: Author rankings (author_name + publication_count)
        elif "author_name" in cols and ("publication_count" in cols or "count" in cols):
            answer_paragraphs.append("Berikut adalah daftar penulis berdasarkan jumlah publikasi dalam database:")
            for idx, r in enumerate(rows, 1):
                name = r.get("author_name", "Unknown")
                count_val = r.get("publication_count", r.get("count", 0))
                answer_paragraphs.append(f"{idx}. **{name}** — {count_val} publikasi")

        # Case C: Institution rankings (institution_name + publication_count)
        elif "institution_name" in cols and ("publication_count" in cols or "count" in cols):
            answer_paragraphs.append("Berikut adalah daftar institusi berdasarkan jumlah publikasi dalam database:")
            for idx, r in enumerate(rows, 1):
                name = r.get("institution_name", "Unknown")
                count_val = r.get("publication_count", r.get("count", 0))
                answer_paragraphs.append(f"{idx}. **{name}** — {count_val} publikasi")

        # Case D: Publication list (publication_id, title, year, citation_count, doi)
        elif "title" in cols:
            answer_paragraphs.append("Ditemukan publikasi berikut dalam database yang sesuai dengan kriteria:")
            for idx, r in enumerate(rows, 1):
                title = r.get("title", "Untitled")
                year = r.get("year")
                doi = r.get("doi")
                citations = r.get("citation_count", 0)

                cite_tag = format_citation(title, year, doi)
                item_line = f"{idx}. **{title}** ({year})"
                if citations is not None:
                    item_line += f" — {citations} sitasi"
                item_line += f" {cite_tag}"
                answer_paragraphs.append(item_line)

        # Case E: Generic table output
        else:
            answer_paragraphs.append("Berikut adalah hasil kueri database:")
            for idx, r in enumerate(rows, 1):
                row_str = ", ".join(f"{k}: {v}" for k, v in r.items() if v is not None)
                answer_paragraphs.append(f"{idx}. {row_str}")

        full_answer = "\n\n".join(answer_paragraphs)

        return SynthesizedSqlResponse(
            status="ok",
            answer=full_answer,
            evidence_objects=ev_set.evidence_objects,
            sources=ev_set.sources,
            unverified_citations=[],
        )


class VectorAnswerSynthesizer:
    """Deterministic grounded synthesizer for Vector semantic search results."""

    @classmethod
    def synthesize(
        cls,
        question: str,
        vector_result: VectorRetrievalResult,
        filters: Optional[FilterParams] = None,
    ) -> SynthesizedVectorResponse:
        """Synthesize grounded narrative answer and EvidenceObjects from Vector search matches."""
        # 1. Zero-match short circuit
        if vector_result.is_empty:
            return SynthesizedVectorResponse(
                status="not_found",
                answer="Data tidak ditemukan dalam database untuk kriteria pencarian tersebut.",
                evidence_objects=[],
                sources=[],
                unverified_citations=[],
            )

        ev_set = EvidenceUnifier.from_vector(question, vector_result, filters=filters)
        matches = vector_result.matches
        answer_paragraphs: List[str] = []
        answer_paragraphs.append(
            f"Berdasarkan pencarian semantik (ambang similaritas kosinus >= {vector_result.threshold:.2f}), "
            f"ditemukan {len(matches)} publikasi yang relevan dengan kriteria pencarian:"
        )

        for idx, m in enumerate(matches, 1):
            cite_tag = format_citation(m.title, m.year, m.doi)
            snippet = m.chunk_text.strip()
            if len(snippet) > 200:
                snippet = snippet[:197] + "..."

            item_line = (
                f"{idx}. **{m.title}** ({m.year or 'n.d.'}) "
                f"— Skor Kemiripan: {m.similarity_score:.2f} {cite_tag}\n"
                f"   > *Ringkasan Abstrak:* {snippet}"
            )
            answer_paragraphs.append(item_line)

        raw_answer = "\n\n".join(answer_paragraphs)
        verified_res = CitationVerifier.verify(raw_answer, matches)

        return SynthesizedVectorResponse(
            status="ok",
            answer=verified_res.cleaned_text,
            evidence_objects=ev_set.evidence_objects,
            sources=ev_set.sources,
            unverified_citations=verified_res.unverified_citations,
        )
