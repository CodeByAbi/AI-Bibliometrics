"""Grant/re-grant SELECT read-only untuk app_readonly (docs/08 Security.md §1.1).

Dipakai ulang setiap kali objek baru dibuat di schema public (kolom
chunks.embedding, tabel edge, tabel Gold) — sesuai aturan re-grant docs/08.

Role dibuat NOLOGIN bila belum ada (kredensial LOGIN diatur saat wiring
backend / Task 3); grant bersifat idempoten. Verifikasi via
has_table_privilege; exit 1 bila ada tabel tanpa SELECT.

DSN dari process env: DB_URL_OWNER (fallback DB_URL). Jangan commit DSN.

Usage:
    python scripts/grant_readonly.py
"""

from __future__ import annotations

import os
import sys

# Driver: prefer psycopg v3 (lockfile), fall back to psycopg2 (local dev).
try:
    import psycopg as _driver  # type: ignore[no-redef]
except ImportError:  # pragma: no cover
    try:
        import psycopg2 as _driver  # type: ignore[no-redef]
    except ImportError:
        print("ERROR: no postgres driver (install psycopg[binary] or psycopg2).", file=sys.stderr)
        sys.exit(1)

CANONICAL_TABLES = [
    "publications",
    "authors",
    "institutions",
    "keywords",
    "funding",
    "pub_author",
    "pub_institution",
    "publication_references",
    "chunks",
]


def main() -> int:
    dsn = os.environ.get("DB_URL_OWNER") or os.environ.get("DB_URL")
    if not dsn:
        print("FATAL: set DB_URL_OWNER (fallback DB_URL) in process env.", file=sys.stderr)
        return 1

    conn = _driver.connect(dsn, connect_timeout=10)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'app_readonly';")
            if not cur.fetchone():
                cur.execute("CREATE ROLE app_readonly NOLOGIN;")
                print("role app_readonly: CREATED (NOLOGIN; LOGIN diatur saat Task 3)")
            else:
                print("role app_readonly: already exists")

            cur.execute("GRANT USAGE ON SCHEMA public TO app_readonly;")
            for t in CANONICAL_TABLES:
                cur.execute(f'GRANT SELECT ON TABLE "{t}" TO app_readonly;')
            cur.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT ON TABLES TO app_readonly;"
            )
            print(f"grants: SELECT on {len(CANONICAL_TABLES)} tables + default privileges")

            missing = []
            for t in CANONICAL_TABLES:
                cur.execute(
                    "SELECT has_table_privilege('app_readonly', %s, 'SELECT');", (t,)
                )
                if not cur.fetchone()[0]:
                    missing.append(t)
            if missing:
                print(f"VERIFY FAIL: no SELECT on {missing}", file=sys.stderr)
                return 1
            print(f"verify: SELECT OK on all {len(CANONICAL_TABLES)} canonical tables")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
