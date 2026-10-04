"""Post-hoc CitationVerifier service.

Docs Reference: docs/05 Retrieval Rag Design.md §7, docs/06 Api Design.md §5.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.logging import logger

# Standard canonical citation pattern: [Title, Year, DOI] or [Title, Year, no-doi]
CITATION_PATTERN = re.compile(
    r"\[([^,\[\]]+),\s*(\d{4}|n\.d\.),\s*(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+|no-doi)\]"
)


class CitationVerificationResult(BaseModel):
    """Output of post-hoc citation verification."""

    model_config = ConfigDict(frozen=True)

    cleaned_text: str
    verified_citations: List[str] = Field(default_factory=list)
    unverified_citations: List[str] = Field(default_factory=list)


def _normalize_title(title: str) -> str:
    """Normalize title for matching (lowercase, alphanumeric only)."""
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def _titles_match(cite_norm: str, valid_norm: str) -> bool:
    """Strict title matching: exact equality or high token overlap.

    Replaces the former bidirectional substring check (which let a single
    token such as "Graph" match "Knowledge Graph Advances"). Single-token
    cites require exact equality; multi-token cites require Jaccard >= 0.8.
    """
    if not cite_norm or not valid_norm:
        return False
    if cite_norm == valid_norm:
        return True
    cite_tokens = set(cite_norm.split())
    valid_tokens = set(valid_norm.split())
    if len(cite_tokens) < 2 or len(valid_tokens) < 2:
        return False
    union = cite_tokens | valid_tokens
    if not union:
        return False
    return len(cite_tokens & valid_tokens) / len(union) >= 0.8


def _years_match(cite_year: Optional[int], evidence_year: Optional[int]) -> bool:
    """Strict year check against a single evidence record.

    Rules, in both directions:
      * A numeric citation year must equal the evidence year. A correct DOI or
        title with a hallucinated year is still a hallucination and is stripped
        (zero-hallucination invariant).
      * ``n.d.`` (cite_year is None) does NOT bypass the check. Previously
        either side being None returned True, which made ``n.d.`` a universal
        escape hatch: a generator could emit ``[Title, n.d., DOI]`` for every
        citation and never have a year verified at all. If the evidence carries
        a year, ``n.d.`` is now a mismatch.
      * The reverse stays permissive: when the evidence itself has no year, a
        numeric citation year cannot contradict it, so the citation stands.

    The per-record call site is what makes this meaningful — the previous code
    asked "does this year match ANY valid year", which is not the same question
    as "does this year match the record this citation points at".
    """
    if cite_year is None:
        return evidence_year is None
    if evidence_year is None:
        return True
    return cite_year == evidence_year


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of spaces/tabs without touching line structure.

    The previous ``re.sub(r" +", " ", ...)`` only handled literal spaces, so a
    run of spaces around a stripped citation could still leave ``"a  b"`` while
    tabs and non-breaking spaces passed through untouched. Line breaks are
    preserved deliberately: answers are multi-line and joining them would change
    the rendered output.

    Also repairs the punctuation a removal leaves behind, e.g. ``"word ."`` →
    ``"word."`` and ``"a , b"`` → ``"a, b"``.
    """
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([.,;:!?])", r"\1", text)
    text = re.sub(r"([.,;:!?])(?=[ \t]*[\n\r])", r"\1", text)
    return text.strip()


class CitationVerifier:
    """Deterministic regex-based citation verifier against retrieved EvidenceSet / Sources."""

    @classmethod
    def verify(
        cls,
        text: str,
        valid_sources: Sequence[Any],
    ) -> CitationVerificationResult:
        """Scan text for citations, verify against known valid sources, and strip fictitious ones.

        Verification is per-record and conjunctive: a citation is verified only
        if EVERY field it asserts agrees with ONE evidence record.

        The previous implementation was disjunctive — rule 1 accepted a known DOI
        plus a matching year while never looking at the title, and rule 2
        accepted a matching title plus year while never looking at the DOI.
        Both halves are separately exploitable, and both were confirmed against
        this corpus:

          * ``[Real Title, 2025, 10.9999/fabricated]`` — the fabricated DOI is
            unknown, so rule 1 declined, and rule 2 verified on the title alone.
            A DOI that exists nowhere in the database survived verification.
          * ``[Fabricated Title, 2025, <real DOI>]`` — rule 1 verified on the
            real DOI and year, so a title that appears in no evidence survived.

        Under the zero-hallucination invariant both must be stripped. Now a DOI
        citation must match the title AND year of the record owning that DOI, and
        a ``no-doi`` citation must match the title AND year of some record.

        Parameters
        ----------
        text : str
            Narrative answer text containing citations.
        valid_sources : Sequence[Any]
            List of valid publication objects, SourceItems, EvidenceSourceRefs, or dicts.
        """
        if not text:
            return CitationVerificationResult(cleaned_text="", verified_citations=[], unverified_citations=[])

        # One entry per evidence record: (doi_lower | None, normalized title, year).
        # Kept as a list rather than a dict because title matching is fuzzy
        # (Jaccard), so it cannot be keyed by exact normalized title.
        records: List[Tuple[Optional[str], str, Optional[int]]] = []

        for src in valid_sources:
            if isinstance(src, dict):
                doi = src.get("doi")
                title = src.get("title")
                year = src.get("year")
            else:
                doi = getattr(src, "doi", None)
                title = getattr(src, "title", None)
                year = getattr(src, "year", None)

            norm_year = int(year) if year is not None else None
            doi_key = None
            if doi and str(doi).strip() and str(doi).strip().lower() != "no-doi":
                doi_key = str(doi).strip().lower()
            if title:
                norm_t = _normalize_title(str(title))
                if norm_t:
                    records.append((doi_key, norm_t, norm_year))

        verified: List[str] = []
        unverified: List[str] = []

        def _replace_cite(match: re.Match) -> str:
            # Annotated: `Match.group` is typed as returning Any on an unparameterised
            # Match, which made `return full_cite` an implicit Any return.
            full_cite: str = match.group(0)
            cite_title: str = match.group(1).strip()
            cite_year_str: str = match.group(2).strip()
            cite_doi: str = match.group(3).strip()

            cite_year = int(cite_year_str) if cite_year_str.isdigit() else None
            norm_cite_title = _normalize_title(cite_title)
            cite_doi_key = (
                cite_doi.strip().lower()
                if cite_doi.strip().lower() != "no-doi"
                else None
            )

            # Conjunctive match against a SINGLE record. Every field the citation
            # asserts must agree with the same record; fields it does not assert
            # are not consulted.
            for rec_doi, rec_title, rec_year in records:
                if cite_doi_key is not None and rec_doi != cite_doi_key:
                    continue
                if not _titles_match(norm_cite_title, rec_title):
                    continue
                if not _years_match(cite_year, rec_year):
                    continue
                verified.append(full_cite)
                return full_cite

            logger.warning("Stripped unverified citation from answer: %s", full_cite)
            unverified.append(full_cite)
            return ""

        cleaned = CITATION_PATTERN.sub(_replace_cite, text)
        cleaned = _collapse_whitespace(cleaned)

        return CitationVerificationResult(
            cleaned_text=cleaned,
            verified_citations=verified,
            unverified_citations=unverified,
        )
