"""Unit tests for online query embedding service.

Docs Reference: docs/05 Retrieval Rag Design.md §5.2, docs/09 Tech Stack.md §2.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.services.embedding import (
    EmbeddingError,
    _embed_via_ollama,
    clear_embedding_model_cache,
    generate_query_embedding,
)


@pytest.mark.asyncio
async def test_generate_query_embedding_empty_query_raises():
    """Embedding empty or whitespace-only query raises EmbeddingError."""
    with pytest.raises(EmbeddingError, match="Cannot embed empty query text"):
        await generate_query_embedding("   ")


@pytest.mark.asyncio
async def test_generate_query_embedding_local_success():
    """Verify local SentenceTransformer generates 1024-d float vector."""
    mock_st = MagicMock()
    mock_st.encode.return_value = [0.05] * 1024

    with patch("backend.app.services.embedding._load_sentence_transformer", return_value=mock_st):
        clear_embedding_model_cache()
        vec = await generate_query_embedding("Knowledge Graph Question Answering")
        assert len(vec) == 1024
        assert isinstance(vec[0], float)
        assert pytest.approx(vec[0], abs=1e-4) == 0.05


@pytest.mark.asyncio
async def test_generate_query_embedding_dimension_mismatch_raises():
    """Verify mismatch between embedding dimension and settings raises EmbeddingError."""
    mock_st = MagicMock()
    mock_st.encode.return_value = [0.1] * 512  # Wrong dimension

    with patch("backend.app.services.embedding._load_sentence_transformer", return_value=mock_st):
        clear_embedding_model_cache()
        with pytest.raises(EmbeddingError, match="Embedding dimension mismatch"):
            await generate_query_embedding("Test query")


@pytest.mark.asyncio
async def test_generate_query_embedding_ollama_fallback():
    """Verify fallback to Ollama when local model load fails."""
    clear_embedding_model_cache()
    fake_vector = [0.02] * 1024

    with patch("backend.app.services.embedding._load_sentence_transformer", side_effect=RuntimeError("Local ST failed")):
        with patch("backend.app.services.embedding._embed_via_ollama", new_callable=AsyncMock) as mock_ollama:
            mock_ollama.return_value = fake_vector
            vec = await generate_query_embedding("Fallback query")
            assert len(vec) == 1024
            assert mock_ollama.called


@pytest.mark.asyncio
async def test_generate_query_embedding_all_backends_fail_raises():
    """Verify failure of both local ST and Ollama raises EmbeddingError."""
    clear_embedding_model_cache()

    with patch("backend.app.services.embedding._load_sentence_transformer", side_effect=RuntimeError("Local failed")):
        with patch("backend.app.services.embedding._embed_via_ollama", side_effect=RuntimeError("Ollama failed")):
            with pytest.raises(EmbeddingError, match="Failed to generate query embedding"):
                await generate_query_embedding("Doomed query")
