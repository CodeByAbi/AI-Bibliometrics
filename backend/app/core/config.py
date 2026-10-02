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


class Settings(BaseModel):
    """Immutable runtime settings wired to the ``DB_URL`` in ``.env``."""

    model_config = ConfigDict(frozen=True)

    db_url: str = Field(default="", description="Live PostgreSQL DSN (app_readonly role).")
    db_statement_timeout_ms: int = Field(default=10_000, ge=1_000, le=60_000)
    ollama_host: str = Field(default="http://localhost:11434")
    llm_model: str = Field(default="qwen2.5-coder:7b-instruct")
    embedding_model: str = Field(default="BAAI/bge-m3")
    embedding_dimension: int = Field(default=1024)
    ollama_timeout_s: int = Field(default=8, ge=1, le=120)
    vector_schema: str = Field(
        default="extensions",
        description="PostgreSQL schema holding the pgvector extension "
        "(Supabase layout: 'extensions'; vanilla local installs: 'public').",
    )

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


def _parse_int_env(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"Environment variable {name}={raw!r} is not a valid integer")


def _from_env() -> Settings:
    return Settings(
        db_url=(os.environ.get("DB_URL", "") or "").strip().strip("'\""),
        db_statement_timeout_ms=_parse_int_env("DB_STATEMENT_TIMEOUT_MS", "10000"),
        ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434").strip(),
        llm_model=os.environ.get("LLM_MODEL", "qwen2.5-coder:7b-instruct").strip(),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3").strip(),
        embedding_dimension=_parse_int_env("EMBEDDING_DIMENSION", "1024"),
        ollama_timeout_s=_parse_int_env("OLLAMA_TIMEOUT_S", "8"),
        vector_schema=os.environ.get("VECTOR_SCHEMA", "extensions").strip(),
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
