"""Database connection and utility helpers for scripts.

Provides driver-agnostic connection management, Windows libpq root.crt
parking for private-CA Supabase connections, and pgvector type registration.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import sys
from typing import Iterator
from urllib.parse import urlparse

# Auto-load project .env so DB_URL/DB_URL_OWNER resolve without manual export.
# python-dotenv is pinned in requirements.txt. Silent no-op if missing.
try:
    from dotenv import load_dotenv

    _ROOT = pathlib.Path(__file__).resolve().parent.parent
    load_dotenv(_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

# Driver: prefer psycopg v3, fall back to psycopg2
try:
    import psycopg as driver  # type: ignore[no-redef]
    from psycopg import Connection, Cursor
    _DRIVER = "psycopg3"
except ImportError:  # pragma: no cover
    try:
        import psycopg2 as driver  # type: ignore[no-redef]
        from psycopg2.extensions import connection as Connection, cursor as Cursor  # type: ignore
        _DRIVER = "psycopg2"
    except ImportError:
        print("ERROR: no postgres driver (install psycopg[binary] or psycopg2).", file=sys.stderr)
        sys.exit(1)

# pgvector registration if available
try:
    if _DRIVER == "psycopg3":
        from pgvector.psycopg import register_vector
    else:
        from pgvector.psycopg2 import register_vector
    _HAS_PGVECTOR = True
except ImportError:
    register_vector = None  # type: ignore
    _HAS_PGVECTOR = False


def safe_dsn_label(dsn: str) -> str:
    """Return host:port/db with credentials redacted."""
    try:
        p = urlparse(dsn)
        user = p.username or "anonymous"
        host = p.hostname or "localhost"
        port = f":{p.port}" if p.port else ""
        db = (p.path or "/").lstrip("/")
        return f"{p.scheme}://{user}@{host}{port}/{db}"
    except Exception:
        return "<malformed-dsn>"


@contextlib.contextmanager
def park_windows_root_crt() -> Iterator[None]:
    """Temporarily park stray %APPDATA%/postgresql/root.crt if present on Windows.
    
    On Windows, libpq treats any presence of root.crt as verify-ca/verify-full,
    which fails when connecting to Supabase session pooler issued by a private CA.
    """
    if sys.platform != "win32":
        yield
        return

    appdata = os.environ.get("APPDATA")
    if not appdata:
        yield
        return

    crt_path = pathlib.Path(appdata) / "postgresql" / "root.crt"
    parked_path = pathlib.Path(appdata) / "postgresql" / "root.crt.parked"

    parked = False
    if crt_path.exists():
        # Another concurrent run may have already parked (parked_path exists):
        # skip rename to avoid clobbering instead of overwriting user files.
        if parked_path.exists():
            pass
        else:
            try:
                crt_path.rename(parked_path)
                parked = True
            except OSError:
                pass
    elif parked_path.exists():
        # Heal stale state from a previous crash (SIGKILL between rename/restore).
        try:
            parked_path.rename(crt_path)
        except OSError:
            pass

    try:
        yield
    finally:
        if parked and parked_path.exists() and not crt_path.exists():
            try:
                parked_path.rename(crt_path)
            except OSError:
                pass


@contextlib.contextmanager
def get_db_connection(
    dsn: str | None = None,
    dsn_env: str = "DB_URL_OWNER",
    fallback_env: str = "DB_URL",
    autocommit: bool = False,
    statement_timeout_ms: int = 10_000,
    register_vec: bool = True,
) -> Iterator[Connection]:
    """Context manager yielding a PostgreSQL connection with clean lifecycle."""
    if not dsn:
        dsn = os.environ.get(dsn_env) or os.environ.get(fallback_env)
    if not dsn:
        raise ValueError(f"Neither ${dsn_env} nor ${fallback_env} is set in process env.")

    with park_windows_root_crt():
        if _DRIVER == "psycopg3":
            conn = driver.connect(
                dsn,
                connect_timeout=10,
                options=f"-c statement_timeout={statement_timeout_ms}ms",
                autocommit=autocommit,
            )
            if register_vec and _HAS_PGVECTOR and register_vector is not None:
                try:
                    register_vector(conn)
                except Exception:
                    # pgvector extension might not be enabled yet on first migration run
                    pass
        else:
            conn = driver.connect(dsn, connect_timeout=10)
            conn.autocommit = autocommit
            # Mirror psycopg3 options="-c statement_timeout=..." (10s invariant).
            try:
                with conn.cursor() as _cur:  # type: ignore[union-attr]
                    _cur.execute(f"SET statement_timeout = '{int(statement_timeout_ms)}ms'")  # type: ignore[arg-type]
            except Exception:
                pass
            if register_vec and _HAS_PGVECTOR and register_vector is not None:
                try:
                    register_vector(conn)
                except Exception:
                    pass

        try:
            yield conn
        finally:
            conn.close()
