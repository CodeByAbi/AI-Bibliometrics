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
import pathlib
import sys

# Ensure project root is in sys.path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.db import get_db_connection
ALL_PUBLIC_TABLES = [
    "publications",
    "authors",
    "institutions",
    "keywords",
    "funding",
    "pub_author",
    "pub_institution",
    "publication_references",
    "chunks",
    "institution_collaboration",
    "author_collaboration",
]


def main() -> int:
    try:
        with get_db_connection(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'app_readonly';")
                if not cur.fetchone():
                    cur.execute("CREATE ROLE app_readonly NOLOGIN;")
                    print("role app_readonly: CREATED (NOLOGIN; LOGIN diatur saat Task 3)")
                else:
                    print("role app_readonly: already exists")

                cur.execute("GRANT USAGE ON SCHEMA public TO app_readonly;")
                
                # Grant on existing tables
                cur.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
                    """
                )
                live_tables = [r[0] for r in cur.fetchall()]
                
                for t in live_tables:
                    cur.execute(f'GRANT SELECT ON TABLE "{t}" TO app_readonly;')
                
                cur.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT SELECT ON TABLES TO app_readonly;"
                )
                print(f"grants: SELECT on {len(live_tables)} tables + default privileges")

                missing = []
                for t in live_tables:
                    cur.execute(
                        "SELECT has_table_privilege('app_readonly', %s, 'SELECT');", (t,)
                    )
                    if not cur.fetchone()[0]:
                        missing.append(t)
                if missing:
                    print(f"VERIFY FAIL: no SELECT on {missing}", file=sys.stderr)
                    return 1
                print(f"verify: SELECT OK on all {len(live_tables)} public tables")
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
