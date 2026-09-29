"""Grounded answer synthesizer and evidence object constructor for SQLRoute.

Docs Reference: docs/05 Retrieval Rag Design.md §4, §6, §7; docs/06 Api Design.md §5.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field

from backend.app.models.ask import (
    EvidenceObject,
    EvidenceSourceRef,
    FilterParams,
    SourceItem,
)
from backend.app.services.retrievers.sql_retriever import SqlRetrievalResult
from backend.app.services.retrievers.vector_retriever import (
    VectorMatchItem,
    VectorRetrievalResult,
)
from backend.app.services.synthesizer.citation import CitationVerifier

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


def format_citation(title: Optional[str], year: Optional[int], doi: Optional[str]) -> str:
    """Format citation string adhering strictly to canonical [Title, Year, DOI/no-doi]."""
    t = title or "Untitled"
    y = str(year) if year is not None else "n.d."
    d = doi.strip() if doi and doi.strip() else "no-doi"
    return f"[{t}, {y}, {d}]"


def _format_period(filters: Optional[FilterParams]) -> str:
    """Render the observation window, honoring exact year and ranges (P1-5)."""
    if not filters:
        return "all-time"
    if filters.year is not None:
        return str(filters.year)
    if filters.year_from is not None and filters.year_to is not None:
        return f"{filters.year_from}-{filters.year_to}"
    if filters.year_from is not None:
        return f"{filters.year_from}-present"
    if filters.year_to is not None:
        return f"up-to-{filters.year_to}"
    return "all-time"


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

        rows = sql_result.rows
        cols = set(sql_result.columns)
        period_str = _format_period(filters)

        evidence_objects: List[EvidenceObject] = []
        sources: List[SourceItem] = []
        answer_paragraphs: List[str] = []

        # Case A: Single aggregate scalar (e.g., total_publications)
        if len(rows) == 1 and ("total_publications" in cols or "count" in cols):
            val = rows[0].get("total_publications", rows[0].get("count", 0))
            claim_text = f"Berdasarkan data database, total publikasi tercatat sebanyak {val}."
            if filters and filters.year:
                claim_text = f"Berdasarkan data database, total publikasi pada tahun {filters.year} adalah {val}."

            ev = EvidenceObject(
                claim=claim_text,
                metric="publication_count",
                value=int(val) if isinstance(val, (int, float)) else str(val),
                period=period_str,
                sources=[],
                confidence=1.0,
            )
            evidence_objects.append(ev)
            answer_paragraphs.append(claim_text)

        # Case B: Author rankings (author_name + publication_count)
        elif "author_name" in cols and ("publication_count" in cols or "count" in cols):
            answer_paragraphs.append("Berikut adalah daftar penulis berdasarkan jumlah publikasi dalam database:")
            for idx, r in enumerate(rows, 1):
                name = r.get("author_name", "Unknown")
                count_val = r.get("publication_count", r.get("count", 0))
                answer_paragraphs.append(f"{idx}. **{name}** — {count_val} publikasi")
                evidence_objects.append(
                    EvidenceObject(
                        claim=f"Penulis {name} memiliki {count_val} publikasi dalam database ({period_str})",
                        metric="publication_count",
                        value=int(count_val) if isinstance(count_val, (int, float)) else str(count_val),
                        period=period_str,
                        sources=[],
                        confidence=1.0,
                    )
                )

        # Case C: Institution rankings (institution_name + publication_count)
        elif "institution_name" in cols and ("publication_count" in cols or "count" in cols):
            answer_paragraphs.append("Berikut adalah daftar institusi berdasarkan jumlah publikasi dalam database:")
            for idx, r in enumerate(rows, 1):
                name = r.get("institution_name", "Unknown")
                count_val = r.get("publication_count", r.get("count", 0))
                answer_paragraphs.append(f"{idx}. **{name}** — {count_val} publikasi")
                evidence_objects.append(
                    EvidenceObject(
                        claim=f"Institusi {name} memiliki {count_val} publikasi dalam database ({period_str})",
                        metric="publication_count",
                        value=int(count_val) if isinstance(count_val, (int, float)) else str(count_val),
                        period=period_str,
                        sources=[],
                        confidence=1.0,
                    )
                )

        # Case D: Publication list (publication_id, title, year, citation_count, doi)
        elif "title" in cols:
            answer_paragraphs.append("Ditemukan publikasi berikut dalam database yang sesuai dengan kriteria:")
            for idx, r in enumerate(rows, 1):
                pub_id = str(r.get("publication_id", f"pub_{idx}"))
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
                            confidence=1.0,
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
                        provenance=None,
                    )
                )

        # Case E: Generic table output
        # AC-RAG-2 requires every status=ok response to carry evidence_objects,
        # so emit a row-count evidence even when columns match no known shape.
        else:
            answer_paragraphs.append("Berikut adalah hasil kueri database:")
            for idx, r in enumerate(rows, 1):
                row_str = ", ".join(f"{k}: {v}" for k, v in r.items() if v is not None)
                answer_paragraphs.append(f"{idx}. {row_str}")
            evidence_objects.append(
                EvidenceObject(
                    claim=f"Kueri database mengembalikan {len(rows)} baris ({period_str})",
                    metric="publication_count",
                    value=len(rows),
                    period=period_str,
                    sources=[],
                    confidence=1.0,
                )
            )

        full_answer = "\n\n".join(answer_paragraphs)

        return SynthesizedSqlResponse(
            status="ok",
            answer=full_answer,
            evidence_objects=evidence_objects,
            sources=sources,
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

        matches = vector_result.matches
        period_str = _format_period(filters)

        evidence_objects: List[EvidenceObject] = []
        sources: List[SourceItem] = []
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

            src_ref = EvidenceSourceRef(
                publication_id=m.publication_id,
                doi=m.doi,
                eid=m.eid,
                title=m.title,
                year=m.year,
            )

            evidence_objects.append(
                EvidenceObject(
                    claim=f"Publikasi '{m.title}' teridentifikasi relevan dengan topik kueri (skor kemiripan kosinus: {m.similarity_score:.2f})",
                    metric="similarity_score",
                    value=round(m.similarity_score, 4),
                    period=str(m.year) if m.year is not None else period_str,
                    sources=[src_ref],
                    confidence=min(1.0, max(0.0, round(m.similarity_score, 4))),
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

        raw_answer = "\n\n".join(answer_paragraphs)
        verified_res = CitationVerifier.verify(raw_answer, matches)

        return SynthesizedVectorResponse(
            status="ok",
            answer=verified_res.cleaned_text,
            evidence_objects=evidence_objects,
            sources=sources,
            unverified_citations=verified_res.unverified_citations,
        )
