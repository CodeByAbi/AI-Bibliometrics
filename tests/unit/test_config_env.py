"""Config env wiring: every documented tuning knob must be honoured.

Regression: SYNTHESIS_TIMEOUT_S, SYNTHESIS_NUM_PREDICT and HNSW_EF_SEARCH
were documented in .env.example but _from_env() never read them, so the
code defaults always won and tuning via .env silently did nothing.
"""

from backend.app.core.config import get_settings


def test_synthesis_and_hnsw_knobs_read_from_env(monkeypatch):
    """Documented .env knobs reach Settings instead of defaults."""
    monkeypatch.setenv("SYNTHESIS_TIMEOUT_S", "60")
    monkeypatch.setenv("SYNTHESIS_NUM_PREDICT", "64")
    monkeypatch.setenv("HNSW_EF_SEARCH", "50")
    get_settings.cache_clear()
    try:
        settings = get_settings()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

    assert settings.synthesis_timeout_s == 60
    assert settings.synthesis_num_predict == 64
    assert settings.hnsw_ef_search == 50


def test_synthesis_and_hnsw_knobs_default_without_env(monkeypatch):
    """Unset knobs fall back to the measured defaults."""
    monkeypatch.delenv("SYNTHESIS_TIMEOUT_S", raising=False)
    monkeypatch.delenv("SYNTHESIS_NUM_PREDICT", raising=False)
    monkeypatch.delenv("HNSW_EF_SEARCH", raising=False)
    get_settings.cache_clear()
    try:
        settings = get_settings()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

    assert settings.synthesis_timeout_s == 120
    assert settings.synthesis_num_predict == 128
    assert settings.hnsw_ef_search == 100
