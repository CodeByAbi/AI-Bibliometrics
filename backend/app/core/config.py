"""Backend runtime configuration (Phase 0+).

Single source of truth for ``DB_URL`` and companion settings. Values come
from the process environment; the project ``.env`` is auto-loaded for local
runs (``docker compose`` injects it via ``env_file`` instead). Secrets are
never logged — use :func:`safe_db_label` for host/db/user-only labels.

Contract: ``.env.example`` (DB_URL, OLLAMA_HOST, model IDs, timeouts).
"""

from __future__ import annotations

import os
import pathlib
import re
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover

    def load_dotenv(*args, **kwargs) -> bool:  # type: ignore[no-redef]
        return False


def _project_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent.parent.parent


load_dotenv(_project_root() / ".env", override=False)


# Browser origins permitted by default (local development only). Overridden via
# the comma-separated ``CORS_ORIGINS`` env var — see ``Settings.cors_origins``.
DEFAULT_CORS_ORIGINS: list[str] = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]


class Settings(BaseModel):
    """Immutable runtime settings wired to the ``DB_URL`` in ``.env``."""

    model_config = ConfigDict(frozen=True)

    db_url: str = Field(default="", description="Live PostgreSQL DSN (app_readonly role).")
    db_statement_timeout_ms: int = Field(default=10_000, ge=1_000, le=60_000)
    ollama_host: str = Field(default="http://localhost:11434")
    llm_model: str = Field(default="qwen2.5-coder:7b-instruct")
    embedding_model: str = Field(default="BAAI/bge-m3")
    embedding_dimension: int = Field(default=1024)
    vector_cosine_threshold: float = Field(
        default=0.48,
        ge=0.0,
        le=1.0,
        description="Cosine similarity gate for VectorRoute. Recalibrated from "
        "0.65 to 0.48 against the labelled retrieval benchmark "
        "(tests/fixtures/retrieval_benchmark_v1.json, 94 queries, "
        "reports/retrieval_calibration.md). At 0.65 only 40.5% of answerable "
        "queries returned any evidence, because bge-m3 compresses natural-"
        "language queries into a narrower band than 0.65 assumes. 0.48 is the "
        "measured negative-query ceiling: the highest score any strict-absence "
        "negative reached in the corpus, so it admits evidence without "
        "admitting a negative (FP rate 0/15). Override via "
        "VECTOR_COSINE_THRESHOLD to retune per deployment without a code "
        "change. Changing it moves the Zero-Hallucination invariant's operating "
        "point, so it is a documented deployment decision. PROTOTYPE-CALIBRATED: "
        "derived from a 20-publication corpus and must be re-validated after "
        "production-scale ingestion.",
    )
    vector_top_k: int = Field(
        default=8,
        ge=1,
        le=50,
        description="Distinct publications VectorRoute returns per query "
        "(docs/05 §5.2, FR4.4). Applied AFTER the cosine gate and AFTER "
        "per-publication dedup, so it bounds evidence volume, not the ANN "
        "search window. Override via VECTOR_TOP_K to retune without a code "
        "change.",
    )
    embedding_prewarm: bool = Field(
        default=True,
        description="Load the query-embedding model during application startup "
        "instead of on the first VectorRoute request. Measured first-request "
        "cost without it: 29.9-185.8 s (HuggingFace fetch + weight load) "
        "versus 154-185 ms warm.",
    )
    ollama_timeout_s: int = Field(default=8, ge=1, le=120)
    ollama_max_retries: int = Field(
        default=1,
        ge=0,
        le=3,
        description="Additional attempts after the first for OUTBOUND Ollama "
        "calls (0 = single attempt). Total outbound attempts are therefore "
        "1 + this value. Kept separate from OLLAMA_TIMEOUT_S because the two "
        "budgets multiply: at the defaults a naive 2-attempt retry on an 8 s "
        "timeout blocks POST /api/v1/ask for ~17 s. Default 1 preserves the "
        "documented one-transient-retry behaviour for connect errors while "
        "timeouts stay single-attempt (see retry.retry_on_timeout).",
    )
    text2sql_timeout_s: int = Field(
        default=6,
        ge=1,
        le=60,
        description="Per-attempt timeout for the Ollama Text-to-SQL fallback on "
        "the synchronous POST /api/v1/ask path. Deliberately shorter than "
        "OLLAMA_TIMEOUT_S: Text-to-SQL is the only LLM call a request can be "
        "blocked on without opting in, so it gets the tighter budget. 6 s is "
        "the largest value that still leaves room for routing + entity "
        "resolution + the 10 s DB statement_timeout inside the documented "
        "request SLA.",
    )
    synthesis_timeout_s: int = Field(
        default=120,
        ge=5,
        le=600,
        description="Per-attempt timeout for narrative synthesis (POST "
        "/api/v1/ask with llm_synthesis=true). Separate from OLLAMA_TIMEOUT_S "
        "because the two calls have very different work. MEASURED on the "
        "CPU-only reference deployment, not estimated: qwen2.5-coder:7b "
        "(4.36 GB) sustains 5.7-7.3 tok/s generation and ~70 tok/s prompt eval, "
        "and loading the weights costs 69 s. The resulting budget is therefore "
        "dominated by WHETHER THE MODEL IS RESIDENT, not by generation: warm, a "
        "real synthesis call measures 22 s (1359 prompt tokens, 128 generated); "
        "cold, the 69 s load is added and the same call needs ~91 s. The previous "
        "8 s budget could never emit a token, and a 60 s budget still failed 100% "
        "of the time in practice because Ollama's default 5-minute keep_alive "
        "evicts the model between requests, so every call was a cold one. 120 s "
        "covers the cold path with headroom. To make synthesis FAST rather than "
        "merely possible, raise Ollama's KEEP_ALIVE so the model stays resident; "
        "that is a server-side setting, not an application one.",
    )
    synthesis_num_predict: int = Field(
        default=128,
        ge=32,
        le=2048,
        description="Token cap for narrative synthesis. Sized from measurement, "
        "not convenience: at 5.9 tok/s, 512 tokens is ~87 s of generation, which "
        "does not fit any budget a synchronous request should honour. 128 tokens "
        "measured 14-19 s warm and yields a usable paragraph. Raise only together "
        "with SYNTHESIS_TIMEOUT_S and only after re-measuring tok/s on the target "
        "hardware. NOTE: if the model has been idle past Ollama's keep_alive, the "
        "next request also pays the ~69 s load and will exceed this budget, "
        "falling back to the deterministic renderer. Raise OLLAMA_KEEP_ALIVE for "
        "deployments that need always-warm synthesis.",
    )
    vector_schema: str = Field(
        default="extensions",
        description="PostgreSQL schema holding the pgvector extension "
        "(Supabase layout: 'extensions'; vanilla local installs: 'public').",
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CORS_ORIGINS),
        description="Browser origins allowed by CORSMiddleware. Comma-separated "
        "via CORS_ORIGINS. Defaults to local dev origins only, so a deployed "
        "frontend must set CORS_ORIGINS explicitly.",
    )
    rate_limit_rpm: int = Field(
        default=60,
        ge=1,
        le=10_000,
        description="Per-IP sliding-window request budget for /api/v1/ask "
        "(docs/08 §3). Overridden via RATE_LIMIT_RPM.",
    )
    # --- Session persistence (application domain, docs/04 §13) ---------------
    # A SEPARATE credential from db_url on purpose. The retrieval pool runs as
    # app_readonly against `public`; session writes need INSERT/UPDATE/DELETE
    # against `app`. Handing those to one connection would make a single
    # compromised path able to mutate the canonical corpus, so the two domains
    # get two pools, two roles, two DSNs (docs/03 §0.3 invariant 5).
    #
    # Empty by default: session persistence is OFF until an operator supplies
    # DB_URL_SESSION. When off, /api/v1/sessions returns 503 and /api/v1/ask
    # behaves exactly as it did before this feature existed — stateless, no
    # session_id round-trip. An explicit 503 beats silently dropping the
    # caller's session_id.
    db_url_session: str = Field(
        default="",
        description="PostgreSQL DSN for the app_session role (schema `app` only). "
        "Empty disables session persistence entirely.",
    )
    session_schema: str = Field(
        default="app",
        description="PostgreSQL schema holding conversation state. Must differ "
        "from the bibliometric search_path — the whole isolation guarantee rests "
        "on session tables being unreachable from the retrieval read path.",
    )
    session_recent_messages_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="N in 'session_summary + N recent messages + current "
        "question'. Bounds the conversation context so a long session cannot "
        "grow the prompt without limit. Overridden via "
        "SESSION_RECENT_MESSAGES_LIMIT.",
    )
    session_summary_max_chars: int = Field(
        default=1200,
        ge=200,
        le=8_000,
        description="Hard cap on the rendered session summary. The summary is "
        "regenerated deterministically from the transcript, so truncation is "
        "lossless with respect to the next turn's scope resolution.",
    )
    session_statement_timeout_ms: int = Field(
        default=5_000,
        ge=500,
        le=60_000,
        description="statement_timeout for the session pool. Shorter than the "
        "retrieval pool's 10s: session writes are single-row point updates, so "
        "anything slower than this is contention, not work.",
    )

    @field_validator("session_schema")
    @classmethod
    def validate_session_schema(cls, v: str) -> str:
        """Allow only a plain SQL identifier — interpolated as a schema qualifier
        in SessionRepository SQL, never as user input. Rejecting `public` is the
        point: session tables must not live beside the bibliometric corpus."""
        vv = (v or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", vv):
            raise ValueError(
                f"SESSION_SCHEMA must be a plain SQL identifier, got {v!r}"
            )
        if vv.lower() in {"public", "information_schema", "pg_catalog", "pg_toast"}:
            raise ValueError(
                f"SESSION_SCHEMA must not be {vv!r}: the application domain is "
                "physically separated from the bibliometric schema on purpose "
                "(docs/03 §0.3 invariant 5)"
            )
        return vv

    @field_validator("vector_schema")
    @classmethod
    def validate_vector_schema(cls, v: str) -> str:
        """Allow only plain SQL identifiers — the value is interpolated as a
        schema qualifier in VectorRetriever SQL, never as user input."""
        vv = (v or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", vv):
            raise ValueError(
                f"VECTOR_SCHEMA must be a plain SQL identifier, got {v!r}"
            )
        return vv

    @property
    def session_persistence_enabled(self) -> bool:
        """True when a session credential has been configured.

        The single switch every session call site branches on. It is derived
        rather than configured so there is no way to end up with the flag set
        and no DSN behind it — the two can never disagree.
        """
        return bool(self.db_url_session)


def _parse_int_env(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"Environment variable {name}={raw!r} is not a valid integer")


def _parse_float_env(name: str, default: str) -> float:
    raw = os.environ.get(name, default)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Environment variable {name}={raw!r} is not a valid float"
        ) from exc


def _parse_bool_env(name: str, default: str) -> bool:
    raw = str(os.environ.get(name, default)).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"Environment variable {name}={raw!r} is not a valid boolean")


def _parse_csv_env(name: str, default: list[str]) -> list[str]:
    """Parse a comma-separated env var into an ordered, de-duplicated list.

    Blank segments and surrounding whitespace/quotes are stripped. Setting the
    variable to something that yields no usable entry is a configuration error
    and fails fast, mirroring the ``VECTOR_SCHEMA`` validator — silently
    falling back to localhost origins would look like a working app while every
    real browser request is blocked by CORS.
    """
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    items: list[str] = []
    for segment in raw.split(","):
        value = segment.strip().strip("'\"")
        if value and value not in items:
            items.append(value)
    if not items:
        raise ValueError(
            f"Environment variable {name} was set but produced no usable entries"
        )
    return items


def _from_env() -> Settings:
    return Settings(
        db_url=(os.environ.get("DB_URL", "") or "").strip().strip("'\""),
        db_statement_timeout_ms=_parse_int_env("DB_STATEMENT_TIMEOUT_MS", "10000"),
        ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434").strip(),
        llm_model=os.environ.get("LLM_MODEL", "qwen2.5-coder:7b-instruct").strip(),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3").strip(),
        embedding_dimension=_parse_int_env("EMBEDDING_DIMENSION", "1024"),
        vector_cosine_threshold=_parse_float_env("VECTOR_COSINE_THRESHOLD", "0.48"),
            vector_top_k=_parse_int_env("VECTOR_TOP_K", "8"),
        embedding_prewarm=_parse_bool_env("EMBEDDING_PREWARM", "true"),
        ollama_timeout_s=_parse_int_env("OLLAMA_TIMEOUT_S", "8"),
        ollama_max_retries=_parse_int_env("OLLAMA_MAX_RETRIES", "1"),
        text2sql_timeout_s=_parse_int_env("TEXT2SQL_TIMEOUT_S", "6"),
        vector_schema=os.environ.get("VECTOR_SCHEMA", "extensions").strip(),
        cors_origins=_parse_csv_env("CORS_ORIGINS", DEFAULT_CORS_ORIGINS),
        rate_limit_rpm=_parse_int_env("RATE_LIMIT_RPM", "60"),
        db_url_session=(
            (os.environ.get("DB_URL_SESSION", "") or "").strip().strip("'\"")
        ),
        session_schema=os.environ.get("SESSION_SCHEMA", "app").strip(),
        session_recent_messages_limit=_parse_int_env(
            "SESSION_RECENT_MESSAGES_LIMIT", "10"
        ),
        session_summary_max_chars=_parse_int_env("SESSION_SUMMARY_MAX_CHARS", "1200"),
        session_statement_timeout_ms=_parse_int_env(
            "SESSION_STATEMENT_TIMEOUT_MS", "5000"
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings; call ``get_settings.cache_clear()`` in tests to reload."""
    return _from_env()


def safe_db_label(dsn: str) -> str:
    """host/db/user only — password never leaves this function unmasked."""
    try:
        u = urlparse(dsn)
        return (
            f"{u.scheme}://{u.username or '?'}@{u.hostname or '?'}:"
            f"{u.port or '?'}/{(u.path or '/?').lstrip('/')}"
        )
    except Exception:
        return "<unparsable-dsn>"


def dsn_with_sslmode(dsn: str, sslmode: str = "require") -> str:
    """Append ``sslmode`` unless the DSN already sets it (Supabase/Neon mandate it)."""
    if "sslmode=" in dsn.lower():
        return dsn
    sep = "&" if "?" in dsn else "?"
    return f"{dsn}{sep}sslmode={sslmode}"
