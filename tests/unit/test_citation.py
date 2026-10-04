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


def test_citation_verifier_nd_year_is_stripped_when_evidence_has_a_year():
    """n.d. must NOT bypass the year check (W7).

    This inverts the previous contract. `_years_match` used to return True when
    EITHER side was None, which made "n.d." a universal escape hatch: a generator
    could emit `[Title, n.d., DOI]` for every citation in an answer and no year
    would ever be verified.

    Here the evidence record's year is 2024, so "n.d." asserts "no date" about a
    dated publication. That is an inaccurate citation and is stripped under the
    zero-hallucination invariant.
    """
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

    assert len(res.verified_citations) == 0
    assert len(res.unverified_citations) == 1


def test_citation_verifier_nd_year_verifies_when_evidence_also_lacks_a_year():
    """n.d. against a record with no year is honest, so it must still verify."""
    sources = [
        SourceItem(
            publication_id="PUB003",
            title="Indonesian Benchmark Dataset",
            year=None,
            doi=None,
            source_type="vector",
            relevance_score=0.75,
        )
    ]
    text = "Studi ini memakai [Indonesian Benchmark Dataset, n.d., no-doi]."
    res = CitationVerifier.verify(text, sources)

    assert len(res.verified_citations) == 1
    assert len(res.unverified_citations) == 0


def test_citation_verifier_rejects_fabricated_doi_on_a_real_title():
    """A DOI absent from the database must not survive on a real title (W7).

    The old rule 1 only checked the DOI, then rule 2 checked only the title, so
    the fabricated DOI simply fell through to the title check and verified.
    """
    sources = [
        SourceItem(
            publication_id="PUB004",
            title="Mesenchymal Stem Cells And Inflammation In Vivo",
            year=2025,
            doi="10.1016/real.2025.001",
            source_type="sql",
            relevance_score=0.9,
        )
    ]
    text = "Lihat [Mesenchymal Stem Cells And Inflammation In Vivo, 2025, 10.9999/fabricated.doi]."
    res = CitationVerifier.verify(text, sources)

    assert len(res.verified_citations) == 0, "fabricated DOI must not verify"
    assert len(res.unverified_citations) == 1


def test_citation_verifier_rejects_fabricated_title_on_a_real_doi():
    """A title absent from evidence must not survive on a real DOI (W7).

    The mirror image: the old rule 1 verified on the real DOI plus year and never
    inspected the title, so a title that appears in no evidence object passed.
    """
    sources = [
        SourceItem(
            publication_id="PUB005",
            title="Mesenchymal Stem Cells And Inflammation In Vivo",
            year=2025,
            doi="10.1016/real.2025.001",
            source_type="sql",
            relevance_score=0.9,
        )
    ]
    text = "Lihat [Totally Fabricated Study Title About Quantum Biology, 2025, 10.1016/real.2025.001]."
    res = CitationVerifier.verify(text, sources)

    assert len(res.verified_citations) == 0, "fabricated title must not verify"
    assert len(res.unverified_citations) == 1


def test_citation_verifier_requires_fields_to_agree_with_the_same_record():
    """Fields must agree with ONE record, not with different records each.

    Two records: A has the cited DOI with a different title; B has the cited
    title with a different year. Matching DOI against A and title against B is
    not verification, and per-record matching rejects it.
    """
    sources = [
        SourceItem(
            publication_id="PUB006",
            title="Completely Different Paper About Nothing Alike",
            year=2025,
            doi="10.1016/mismatch.2025.777",
            source_type="sql",
            relevance_score=0.9,
        ),
        SourceItem(
            publication_id="PUB007",
            title="Mesenchymal Stem Cells And Inflammation In Vivo",
            year=1999,
            doi="10.1016/other.1999.888",
            source_type="sql",
            relevance_score=0.9,
        ),
    ]
    # DOI belongs to PUB006 (whose title differs); title belongs to PUB007
    # (whose year is 1999). Neither record satisfies both.
    text = "Lihat [Mesenchymal Stem Cells And Inflammation In Vivo, 2025, 10.1016/mismatch.2025.777]."
    res = CitationVerifier.verify(text, sources)

    assert len(res.verified_citations) == 0
    assert len(res.unverified_citations) == 1


def test_citation_verifier_accepts_fully_consistent_citation():
    """The positive case must still pass: title, year and DOI all agree."""
    sources = [
        SourceItem(
            publication_id="PUB008",
            title="Mesenchymal Stem Cells And Inflammation In Vivo",
            year=2025,
            doi="10.1016/real.2025.001",
            source_type="sql",
            relevance_score=0.9,
        )
    ]
    text = "Lihat [Mesenchymal Stem Cells And Inflammation In Vivo, 2025, 10.1016/real.2025.001]."
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
