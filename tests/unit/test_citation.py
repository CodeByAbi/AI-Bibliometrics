"""Unit tests for CitationVerifier post-hoc service.

Docs Reference: docs/05 Retrieval Rag Design.md §7, docs/06 Api Design.md §5.
"""

from __future__ import annotations

import pytest
from backend.app.models.ask import SourceItem
from backend.app.services.synthesizer.citation import CitationVerifier


def test_citation_verifier_valid_doi_match():
    """Verify citation with valid DOI is retained in answer text."""
    sources = [
        SourceItem(
            publication_id="PUB001",
            title="Knowledge Graph Advances",
            year=2023,
            doi="10.1016/j.kg.2023.01.001",
            source_type="vector",
            relevance_score=0.85,
        )
    ]
    text = "Riset terbaru membahas graf pengetahuan [Knowledge Graph Advances, 2023, 10.1016/j.kg.2023.01.001]."
    res = CitationVerifier.verify(text, sources)

    assert len(res.verified_citations) == 1
    assert len(res.unverified_citations) == 0
    assert "[Knowledge Graph Advances, 2023, 10.1016/j.kg.2023.01.001]" in res.cleaned_text


def test_citation_verifier_valid_no_doi_match():
    """Verify citation with no-doi is validated by Title + Year."""
    sources = [
        SourceItem(
            publication_id="PUB002",
            title="Indonesian Benchmark Dataset",
            year=2024,
            doi=None,
            source_type="vector",
            relevance_score=0.75,
        )
    ]
    text = "Studi awal memperkenalkan dataset [Indonesian Benchmark Dataset, 2024, no-doi] untuk evaluasi."
    res = CitationVerifier.verify(text, sources)

    assert len(res.verified_citations) == 1
    assert len(res.unverified_citations) == 0
    assert "[Indonesian Benchmark Dataset, 2024, no-doi]" in res.cleaned_text


def test_citation_verifier_strips_hallucinated_citation():
    """Verify fictitious citation not in retrieved sources is stripped into unverified_citations."""
    sources = [
        SourceItem(
            publication_id="PUB001",
            title="Real Paper",
            year=2023,
            doi="10.1016/j.real.2023.01",
            source_type="vector",
            relevance_score=0.8,
        )
    ]
    text = "Hasil ini dikonfirmasi oleh [Fake Paper Title, 2025, 10.9999/fake.doi.2025] dalam temuannya."
    res = CitationVerifier.verify(text, sources)

    assert len(res.verified_citations) == 0
    assert len(res.unverified_citations) == 1
    assert "[Fake Paper Title, 2025, 10.9999/fake.doi.2025]" in res.unverified_citations
    assert "[Fake Paper Title" not in res.cleaned_text


def test_citation_verifier_mixed_valid_and_invalid_citations():
    """Verify valid citation is kept while fictitious citation is stripped."""
    sources = [
        SourceItem(
            publication_id="PUB001",
            title="Wharton Jelly Stem Cells",
            year=2022,
            doi="10.1007/s12015-022-10350-0",
            source_type="vector",
            relevance_score=0.9,
        )
    ]
    text = (
        "Terapi ini didukung oleh [Wharton Jelly Stem Cells, 2022, 10.1007/s12015-022-10350-0] "
        "namun diragukan oleh [Ghost Study, 2019, no-doi]."
    )
    res = CitationVerifier.verify(text, sources)

    assert len(res.verified_citations) == 1
    assert len(res.unverified_citations) == 1
    assert "[Wharton Jelly Stem Cells, 2022, 10.1007/s12015-022-10350-0]" in res.cleaned_text
    assert "Ghost Study" not in res.cleaned_text


def test_citation_verifier_empty_text():
    """Verify empty text produces clean empty result without errors."""
    res = CitationVerifier.verify("", [])
    assert res.cleaned_text == ""
    assert res.verified_citations == []
    assert res.unverified_citations == []


def test_citation_verifier_year_mismatch_with_valid_doi_stripped():
    """Strict year check: correct DOI but hallucinated year is stripped."""
    sources = [
        SourceItem(
            publication_id="PUB001",
            title="Knowledge Graph Advances",
            year=2023,
            doi="10.1016/j.kg.2023.01.001",
            source_type="vector",
            relevance_score=0.85,
        )
    ]
    text = "Riset ini dilaporkan pada [Knowledge Graph Advances, 1999, 10.1016/j.kg.2023.01.001]."
    res = CitationVerifier.verify(text, sources)

    assert len(res.verified_citations) == 0
    assert len(res.unverified_citations) == 1
    assert "Knowledge Graph Advances" not in res.cleaned_text


def test_citation_verifier_year_mismatch_no_doi_stripped():
    """Strict year check applies to no-doi title citations as well."""
    sources = [
        SourceItem(
            publication_id="PUB002",
            title="Indonesian Benchmark Dataset",
            year=2024,
            doi=None,
            source_type="vector",
            relevance_score=0.75,
        )
    ]
    text = "Studi ini memakai [Indonesian Benchmark Dataset, 2019, no-doi]."
    res = CitationVerifier.verify(text, sources)

    assert len(res.verified_citations) == 0
    assert len(res.unverified_citations) == 1


def test_citation_verifier_nd_year_defers_to_title():
    """n.d. cite year passes when the title matches and evidence year is known."""
    sources = [
        SourceItem(
            publication_id="PUB002",
            title="Indonesian Benchmark Dataset",
            year=2024,
            doi=None,
            source_type="vector",
            relevance_score=0.75,
        )
    ]
    text = "Studi ini memakai [Indonesian Benchmark Dataset, n.d., no-doi]."
    res = CitationVerifier.verify(text, sources)

    assert len(res.verified_citations) == 1
    assert len(res.unverified_citations) == 0


def test_citation_verifier_single_token_substring_rejected():
    """Substring hardening: bare 'Graph' must not match 'Knowledge Graph Advances'."""
    sources = [
        SourceItem(
            publication_id="PUB001",
            title="Knowledge Graph Advances",
            year=2023,
            doi="10.1016/j.kg.2023.01.001",
            source_type="vector",
            relevance_score=0.85,
        )
    ]
    text = "Lihat pembahasan pada [Graph, 2023, 10.1016/j.kg.2023.01.001]."
    res = CitationVerifier.verify(text, sources)

    # DOI matches and year matches, so DOI rule verifies even with short title.
    # Title-only path must still reject the single-token substring:
    text2 = "Lihat pembahasan pada [Graph, 2023, no-doi]."
    res2 = CitationVerifier.verify(text2, sources)
    assert len(res2.verified_citations) == 0
    assert len(res2.unverified_citations) == 1
