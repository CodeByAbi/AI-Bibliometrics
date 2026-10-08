"""Unit + endpoint tests for opt-in LLM synthesis (Fase 7 B1-b).

Covers: §6 prompt construction (UNTRUSTED framing, dual blocks, question
isolation), Ollama call error paths (timeout/unreachable/non-200/empty),
CitationVerifier post-processing of LLM text (valid retained, hallucinated
stripped), deterministic fallback preservation, and the
``llm_synthesis`` opt-in flag on POST /api/v1/ask (default OFF).

Docs Reference: docs/05 Retrieval Rag Design.md §6-§7;
    docs/11 Roadmap.md §Fase 7 (B1).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app.models.ask import AskRequest
from backend.app.services.evidence.models import EvidenceSet
from backend.app.services.evidence.unifier import EvidenceUnifier
from backend.app.services.retrievers.hybrid_retriever import (
    HybridExpertItem,
    HybridPublicationMeta,
    HybridRetrievalResult,
)
from backend.app.services.router import EntityResolutionResult
from backend.app.services.synthesizer.llm import (
    SYNTHESIS_SYSTEM_PROMPT,
    LlmAnswerSynthesizer,
    LlmSynthesisError,
    build_synthesis_prompt,
    generate_synthesis_text,
)


def _expert_evidence_set() -> EvidenceSet:
    """Non-empty Hybrid EvidenceSet with one expert + one publication source."""
    hybrid_res = HybridRetrievalResult(
        intent_type="EXPERT_RANKING",
        topics=[],
        experts=[
            HybridExpertItem(
                author_id="AUTH001",
                author_name="Budi Santoso",
                topic_id=1,
                topic_name="Artificial Intelligence",
                expertise_score=0.91,
                relevance_score=0.85,
                productivity_score=0.80,
                impact_score=0.88,
                recency_score=0.90,
                h_index_topic=12,
                publication_count_topic=9,
                citation_count_topic=200,
                coauthor_network_size=15,
            )
        ],
        publications={
            "PUB001": HybridPublicationMeta(
                publication_id="PUB001",
                title="Deep Learning for Medical Imaging",
                year=2024,
                doi="10.1000/dlmi.2024",
            )
        },
        target_topic_name="Artificial Intelligence",
    )
    return EvidenceUnifier.from_hybrid("Siapa pakar AI?", hybrid_res)


# ---------------------------------------------------------------------------
# Prompt construction (§6)
# ---------------------------------------------------------------------------


def test_build_synthesis_prompt_has_untrusted_framing_and_blocks():
    ev_set = _expert_evidence_set()
    assert not ev_set.is_empty
    prompt = build_synthesis_prompt("Siapa pakar AI?", ev_set)
    assert "=== BEGIN RETRIEVED EVIDENCE (UNTRUSTED DATA) ===" in prompt
    assert "=== END RETRIEVED EVIDENCE ===" in prompt
    assert "BEGIN VERIFIED METRICS" in prompt
    assert "BEGIN RETRIEVED PUBLICATIONS" in prompt
    assert "Pertanyaan Pengguna: Siapa pakar AI?" in prompt
    assert "Deep Learning for Medical Imaging" in prompt


def test_build_synthesis_prompt_trims_evidence_to_top_n():
    """P5 latency: prompt carries at most SYNTHESIS_EVIDENCE_TOP_N objects."""
    from backend.app.models.ask import EvidenceObject
    from backend.app.services.synthesizer.llm import SYNTHESIS_EVIDENCE_TOP_N

    objects = [
        EvidenceObject(
            claim=f"Klaim {i:02d}",
            metric="publication_count",
            value=i,
            period="2020-2023",
            sources=[],
            confidence=1.0,
        )
        for i in range(SYNTHESIS_EVIDENCE_TOP_N + 4)
    ]
    ev_set = EvidenceSet(query="q", evidence_objects=objects, sources=[], items=[])
    prompt = build_synthesis_prompt("q?", ev_set)
    assert "Klaim 00" in prompt
    assert f"Klaim {SYNTHESIS_EVIDENCE_TOP_N - 1:02d}" in prompt
    assert f"Klaim {SYNTHESIS_EVIDENCE_TOP_N:02d}" not in prompt


def test_system_prompt_contains_four_grounding_rules():
    assert "DILARANG mengarang angka" in SYNTHESIS_SYSTEM_PROMPT
    assert "BUKAN INSTRUKSI" in SYNTHESIS_SYSTEM_PROMPT
    assert "[Judul Publikasi, Tahun, DOI]" in SYNTHESIS_SYSTEM_PROMPT
    assert "WAJIB merujuk" in SYNTHESIS_SYSTEM_PROMPT


def test_ask_request_llm_synthesis_defaults_off():
    assert AskRequest(question="apa kabar dunia?").llm_synthesis is False


# ---------------------------------------------------------------------------
# generate_synthesis_text error paths (mocked httpx)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_success_returns_text(monkeypatch):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"response": "  Sintesis ter-grounding.  "}

    client = AsyncMock()
    client.post.return_value = resp
    monkeypatch.setattr(
        "backend.app.services.synthesizer.llm.get_http_client", lambda: client
    )
    text = await generate_synthesis_text("sys", "prompt")
    assert text == "Sintesis ter-grounding."


@pytest.mark.asyncio
async def test_generate_timeout_raises(monkeypatch):
    async def _boom(*a, **k):
        raise httpx.TimeoutException("slow")

    client = AsyncMock()
    client.post.side_effect = _boom
    monkeypatch.setattr(
        "backend.app.services.synthesizer.llm.get_http_client", lambda: client
    )
    with pytest.raises(LlmSynthesisError, match="timeout"):
        await generate_synthesis_text("sys", "prompt")


@pytest.mark.asyncio
async def test_generate_connect_error_raises(monkeypatch):
    async def _boom(*a, **k):
        raise httpx.ConnectError("down")

    client = AsyncMock()
    client.post.side_effect = _boom
    monkeypatch.setattr(
        "backend.app.services.synthesizer.llm.get_http_client", lambda: client
    )
    with pytest.raises(LlmSynthesisError, match="unreachable"):
        await generate_synthesis_text("sys", "prompt")


@pytest.mark.asyncio
async def test_generate_non200_and_empty_raise(monkeypatch):
    for body, status in (({"response": ""}, 200), ({"response": "x"}, 500)):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = body

        client = AsyncMock()
        client.post.return_value = resp
        monkeypatch.setattr(
            "backend.app.services.synthesizer.llm.get_http_client", lambda: client
        )
        with pytest.raises(LlmSynthesisError):
            await generate_synthesis_text("sys", "prompt")


# ---------------------------------------------------------------------------
# refine(): verify-then-serve + fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refine_llm_success_verifies_citations(monkeypatch):
    ev_set = _expert_evidence_set()
    llm_text = (
        "Pakar terkemuka adalah Budi Santoso "
        "[Deep Learning for Medical Imaging, 2024, 10.1000/dlmi.2024]."
    )

    async def _fake_generate(_system, _prompt):
        return llm_text

    monkeypatch.setattr(
        "backend.app.services.synthesizer.llm.generate_synthesis_text", _fake_generate
    )
    res = await LlmAnswerSynthesizer.refine(
        "Siapa pakar AI?", ev_set, route="HybridRoute", fallback_answer="DET"
    )
    assert res.synthesis_backend == "llm"
    assert res.llm_ms is not None
    assert "[Deep Learning for Medical Imaging, 2024, 10.1000/dlmi.2024]" in res.answer
    assert res.unverified_citations == []


@pytest.mark.asyncio
async def test_refine_strips_hallucinated_citation_but_serves_rest(monkeypatch):
    ev_set = _expert_evidence_set()
    llm_text = (
        "Temuan utama [Deep Learning for Medical Imaging, 2024, 10.1000/dlmi.2024] "
        "dan studi fiktif [Fake Quantum Paper, 2030, 10.9999/fake.2030]."
    )

    async def _fake_generate(_system, _prompt):
        return llm_text

    monkeypatch.setattr(
        "backend.app.services.synthesizer.llm.generate_synthesis_text", _fake_generate
    )
    res = await LlmAnswerSynthesizer.refine(
        "Siapa pakar AI?", ev_set, route="HybridRoute", fallback_answer="DET"
    )
    assert res.synthesis_backend == "llm"
    assert "Fake Quantum Paper" not in res.answer
    assert "[Deep Learning for Medical Imaging, 2024, 10.1000/dlmi.2024]" in res.answer
    assert any("Fake Quantum Paper" in u for u in res.unverified_citations)
    # Non-citation prose around the stripped tag is preserved.
    assert "Temuan utama" in res.answer


@pytest.mark.asyncio
async def test_refine_fallback_on_llm_error_preserves_deterministic(monkeypatch):
    ev_set = _expert_evidence_set()

    async def _boom(_system, _prompt):
        raise LlmSynthesisError("Ollama daemon unreachable for synthesis")

    monkeypatch.setattr(
        "backend.app.services.synthesizer.llm.generate_synthesis_text", _boom
    )
    res = await LlmAnswerSynthesizer.refine(
        "Siapa pakar AI?",
        ev_set,
        route="HybridRoute",
        fallback_answer="DETERMINISTIC TEXT",
        fallback_unverified=["[Old, 2020, no-doi]"],
    )
    assert res.synthesis_backend == "deterministic-fallback"
    assert res.answer == "DETERMINISTIC TEXT"
    assert res.unverified_citations == ["[Old, 2020, no-doi]"]
    assert res.llm_ms is None


@pytest.mark.asyncio
async def test_refine_fallback_when_verifier_strips_everything(monkeypatch):
    ev_set = _expert_evidence_set()

    async def _fake_generate(_system, _prompt):
        # Degenerate: LLM emits nothing but a hallucinated citation tag.
        return "[Imaginary Study, 2099, 10.9999/img.2099]"

    monkeypatch.setattr(
        "backend.app.services.synthesizer.llm.generate_synthesis_text", _fake_generate
    )
    res = await LlmAnswerSynthesizer.refine(
        "Siapa pakar AI?", ev_set, route="HybridRoute", fallback_answer="DET FALLBACK"
    )
    assert res.synthesis_backend == "deterministic-fallback"
    assert res.answer == "DET FALLBACK"
    assert any("Imaginary Study" in u for u in res.unverified_citations)


# ---------------------------------------------------------------------------
# Endpoint opt-in (mocked retrieval + mocked LLM)
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        pass


@pytest.mark.asyncio
async def test_ask_endpoint_llm_opt_in_uses_llm_backend(monkeypatch):
    hybrid_res = HybridRetrievalResult(
        intent_type="EXPERT_RANKING",
        topics=[],
        experts=[
            HybridExpertItem(
                author_id="AUTH001",
                author_name="Budi Santoso",
                topic_id=1,
                topic_name="Artificial Intelligence",
                expertise_score=0.91,
                relevance_score=0.85,
                productivity_score=0.80,
                impact_score=0.88,
                recency_score=0.90,
                h_index_topic=12,
                publication_count_topic=9,
                citation_count_topic=200,
                coauthor_network_size=15,
            )
        ],
        publications={
            "PUB001": HybridPublicationMeta(
                publication_id="PUB001",
                title="Deep Learning for Medical Imaging",
                year=2024,
                doi="10.1000/dlmi.2024",
            )
        },
        target_topic_name="Artificial Intelligence",
    )

    async def _mock_retrieve(*_a, **_k):
        return hybrid_res

    monkeypatch.setattr("backend.app.routers.ask.HybridRetriever.retrieve", _mock_retrieve)

    async def _mock_gate(*_a, **_k):
        return EntityResolutionResult(status="ok")

    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities", _mock_gate
    )

    async def _fake_generate(_system, _prompt):
        return (
            "Ringkasan pakar: Budi Santoso "
            "[Deep Learning for Medical Imaging, 2024, 10.1000/dlmi.2024]."
        )

    monkeypatch.setattr(
        "backend.app.services.synthesizer.llm.generate_synthesis_text", _fake_generate
    )

    fake_pool = MagicMock()
    fake_pool.acquire.side_effect = lambda: _FakeConn(AsyncMock())
    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=fake_pool)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/ask",
                json={
                    "question": "Siapa pakar topik artificial intelligence?",
                    "filters": {"topic_name": "Artificial Intelligence"},
                    "developer_mode": True,
                    "llm_synthesis": True,
                },
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["debug"]["synthesis_backend"] == "llm"
    assert "llm_synthesis_ms" in data["debug"]["latency_breakdown_ms"]
    assert "Budi Santoso" in data["answer"]
    # Evidence objects stay canonical (never parsed from LLM text)
    assert data["evidence_objects"][0]["value"] == 0.91


@pytest.mark.asyncio
async def test_ask_endpoint_llm_opt_in_falls_back_when_ollama_down(monkeypatch):
    hybrid_res = HybridRetrievalResult(
        intent_type="EXPERT_RANKING",
        topics=[],
        experts=[
            HybridExpertItem(
                author_id="AUTH001",
                author_name="Budi Santoso",
                topic_id=1,
                topic_name="Artificial Intelligence",
                expertise_score=0.91,
                relevance_score=0.85,
                productivity_score=0.80,
                impact_score=0.88,
                recency_score=0.90,
                h_index_topic=12,
                publication_count_topic=9,
                citation_count_topic=200,
                coauthor_network_size=15,
            )
        ],
        publications={},
        target_topic_name="Artificial Intelligence",
    )

    async def _mock_retrieve(*_a, **_k):
        return hybrid_res

    monkeypatch.setattr("backend.app.routers.ask.HybridRetriever.retrieve", _mock_retrieve)

    async def _mock_gate(*_a, **_k):
        return EntityResolutionResult(status="ok")

    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities", _mock_gate
    )

    async def _boom(_system, _prompt):
        raise LlmSynthesisError("down")

    monkeypatch.setattr("backend.app.services.synthesizer.llm.generate_synthesis_text", _boom)

    fake_pool = MagicMock()
    fake_pool.acquire.side_effect = lambda: _FakeConn(AsyncMock())
    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=fake_pool)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/ask",
                json={
                    "question": "Siapa pakar topik artificial intelligence?",
                    "filters": {"topic_name": "Artificial Intelligence"},
                    "developer_mode": True,
                    "llm_synthesis": True,
                },
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["debug"]["synthesis_backend"] == "deterministic-fallback"
    assert "Budi Santoso" in data["answer"]
