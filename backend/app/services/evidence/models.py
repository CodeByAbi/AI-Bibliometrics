"""Canonical Evidence models and EvidenceSet schema for the Evidence Layer.

Docs Reference: docs/05 Retrieval Rag Design.md §4, docs/10 Implementation Plan.md §1 (Task 7), docs/11 Roadmap.md (Fase 5).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

from backend.app.models.ask import (
    EvidenceObject,
    EvidenceSourceRef,
    SourceItem,
)


class EvidenceItem(BaseModel):
    """Normalized atomic evidence unit from any retrieval source (SQL, Vector, Graph, Analytics)."""

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(..., description="ID unik sumber bukti (misal: chunk_id, edge_id, row_id)")
    source_type: Literal["sql", "vector", "graph", "analytics"] = Field(
        ...,
        description="Jalur asal retrieval bukti",
    )
    content: str = Field(..., description="Teks bukti atau representasi faktual")
    score: float = Field(default=1.0, ge=0.0, description="Skor relevansi atau bobot relasi")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Tingkat keyakinan bukti data")
    publication_id: Optional[str] = Field(None, description="ID kanonikal publikasi terkait jika ada")
    title: Optional[str] = Field(None, description="Judul publikasi terkait jika ada")
    year: Optional[int] = Field(None, description="Tahun publikasi terkait jika ada")
    doi: Optional[str] = Field(None, description="DOI publikasi terkait jika ada")
    eid: Optional[str] = Field(None, description="EID Scopus publikasi terkait jika ada")
    provenance_ids: List[str] = Field(default_factory=list, description="Array ID asal untuk auditability lineage")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata kontekstual tambahan")


class EvidenceSet(BaseModel):
    """Canonical unified evidence container decoupling retrieval engines from LLM synthesis."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(..., description="Pertanyaan asli pengguna")
    evidence_objects: List[EvidenceObject] = Field(
        default_factory=list,
        description="Array objek bukti numerik dan faktual terverifikasi",
    )
    sources: List[SourceItem] = Field(
        default_factory=list,
        description="Daftar naskah/literatur sumber bukti yang terdeduplikasi",
    )
    items: List[EvidenceItem] = Field(
        default_factory=list,
        description="Daftar item bukti individual ternormalisasi",
    )
    filters_ignored: List[str] = Field(
        default_factory=list,
        description="Daftar filter yang tidak terpakai/diabaikan selama retrieval",
    )
    sql_executed: Optional[str] = Field(
        None,
        description="Kueri SQL yang dieksekusi jika bersumber dari SQL/Vector/Graph",
    )

    @property
    def is_empty(self) -> bool:
        """True if the evidence set contains no valid evidence objects, sources, or items."""
        return len(self.evidence_objects) == 0 and len(self.sources) == 0 and len(self.items) == 0

    @property
    def count(self) -> int:
        """Total count of primary evidence items or objects."""
        return max(len(self.items), len(self.evidence_objects), len(self.sources))

    def to_metrics_json(self, indent: int = 2) -> str:
        """Serialize verified metrics and evidence objects to formatted JSON."""
        if not self.evidence_objects:
            return "[]"
        payload = [ev.model_dump() for ev in self.evidence_objects]
        return json.dumps(payload, ensure_ascii=False, indent=indent)

    def to_chunks_text(self) -> str:
        """Serialize publication chunks and snippets as structured text."""
        if not self.items and not self.sources:
            return "(No retrieved publication chunks)"

        lines: List[str] = []
        if self.items:
            for idx, it in enumerate(self.items, 1):
                cite_doi = it.doi or "no-doi"
                title_str = it.title or "Untitled"
                year_str = str(it.year) if it.year is not None else "n.d."
                header = f"[{idx}] [{title_str}, {year_str}, {cite_doi}] (source_type: {it.source_type}, score: {it.score:.4f})"
                lines.append(header)
                if it.content:
                    lines.append(f"    Content: {it.content}")
                if it.provenance_ids:
                    lines.append(f"    Provenance: {', '.join(it.provenance_ids)}")
        elif self.sources:
            for idx, s in enumerate(self.sources, 1):
                cite_doi = s.doi or "no-doi"
                year_str = str(s.year) if s.year is not None else "n.d."
                score_str = f", relevance: {s.relevance_score:.4f}" if s.relevance_score is not None else ""
                header = f"[{idx}] [{s.title}, {year_str}, {cite_doi}] (source_type: {s.source_type}{score_str})"
                lines.append(header)
                if s.provenance:
                    lines.append(f"    Provenance: {s.provenance}")

        return "\n".join(lines)

    def to_prompt_context(self) -> str:
        """Construct the full dual-block untrusted prompt context per docs/05 §6."""
        metrics_json = self.to_metrics_json()
        chunks_text = self.to_chunks_text()

        return (
            "==================== BEGIN VERIFIED METRICS & EVIDENCE OBJECTS ====================\n"
            f"{metrics_json}\n"
            "==================== END VERIFIED METRICS & EVIDENCE OBJECTS ======================\n\n"
            "==================== BEGIN RETRIEVED PUBLICATIONS & CHUNKS =======================\n"
            f"{chunks_text}\n"
            "==================== END RETRIEVED PUBLICATIONS & CHUNKS ========================="
        )

    def to_untrusted_evidence_block(self) -> str:
        """Construct prompt-injection defense framing per AGENTS.md conventions."""
        prompt_ctx = self.to_prompt_context()
        return (
            "=== BEGIN RETRIEVED EVIDENCE (UNTRUSTED DATA) ===\n"
            f"{prompt_ctx}\n"
            "=== END RETRIEVED EVIDENCE ==="
        )
