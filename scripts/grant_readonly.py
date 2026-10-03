"""Grant/re-grant SELECT read-only untuk app_readonly (docs/08 Security.md §1.1).

Dipakai ulang setiap kali objek baru dibuat di schema public (kolom
chunks.embedding, tabel edge, tabel Gold) — sesuai aturan re-grant docs/08.

Role dibuat NOLOGIN bila belum ada (kredensial LOGIN diatur saat wiring
backend / Task 3); grant bersifat idempoten. Verifikasi via
has_table_privilege; exit 1 bila ada tabel tanpa SELECT.

DSN dari process env: DB_URL_OWNER (fallback DB_URL). Jangan commit DSN.

Usage:
    python scripts/grant_readonly.py
    python scripts/grant_readonly.py --revoke-supabase-roles
"""

from __future__ import annotations
import argparse
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

#: Supabase-managed roles that back the public PostgREST API. Their anon key
#: ships in the frontend bundle by design, so anything they can reach is
#: effectively public. The project's access model is server-side only via
#: app_readonly (docs/08 §1.1), so these roles are not expected to need access.
SUPABASE_API_ROLES = ("anon", "authenticated")


def _apply_readonly_grants(cur) -> list[str]:
    cur.execute("GRANT USAGE ON SCHEMA public TO app_readonly;")

    # Strip the PostgreSQL default privileges on schema public from PUBLIC.
    # Verified on this deployment (PostgreSQL 17.6, Supabase): PUBLIC already
    # holds only USAGE, not CREATE — PG15+ removed CREATE from the default
    # public-schema ACL, so this is a no-op here. It is kept because the script
    # is also the documented setup path for older or self-hosted PostgreSQL,
    # where PUBLIC does inherit CREATE and could shadow application objects.
    cur.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC;")
    print("revoke: ALL ON SCHEMA public FROM PUBLIC")

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

    # Default privileges cover objects created later. Revoking the PUBLIC half
    # matters as much as the grant: without it, any table created after this
    # script runs is readable by PUBLIC by default, which silently reopens the
    # hole the REVOKE above closes. Both statements are scoped to the role that
    # owns the objects (the migration owner), so they must be re-run after
    # applying migrations as a different role.
    cur.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT ON TABLES TO app_readonly;"
    )
    cur.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE ALL ON TABLES FROM PUBLIC;"
    )
    print(f"grants: SELECT on {len(live_tables)} tables + default privileges")
    return live_tables


def audit_api_role_exposure(cur) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Report what the Supabase API roles can reach, and whether RLS masks it.

    Grants alone do not decide reachability: with RLS enabled and no policies,
    PostgreSQL denies every statement for a non-owner role regardless of the
    table grants. Verifying both is the difference between reporting a real
    exposure and crying wolf — on this deployment RLS is ON with zero policies,
    so anon is denied everything and the wide grants are latent, not active.

    Returns (reachable_rows, masked_rows): tables where the API role holds a
    privilege AND RLS is off (real exposure), versus tables where RLS is on
    (grants present but currently denied).
    """
    cur.execute(
        """
        SELECT c.relname,
               c.relrowsecurity,
               has_table_privilege('anon', c.oid, 'SELECT'),
               has_table_privilege('anon', c.oid, 'INSERT')
                 OR has_table_privilege('anon', c.oid, 'UPDATE')
                 OR has_table_privilege('anon', c.oid, 'DELETE')
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY c.relname
        """
    )
    reachable, masked = [], []
    for name, rls, can_read, can_write in cur.fetchall():
        if not (can_read or can_write):
            continue
        entry = {
            "table": name,
            "rls": bool(rls),
            "anon_select": bool(can_read),
            "anon_write": bool(can_write),
        }
        (masked if rls else reachable).append(entry)
    return reachable, masked


def _report_exposure(cur) -> list[dict[str, object]]:
    reachable, masked = audit_api_role_exposure(cur)
    if not reachable and not masked:
        print("exposure: no Supabase API role holds any privilege on public tables")
        return []

    if reachable:
        print(
            f"EXPOSURE: {len(reachable)} table(s) reachable by 'anon' with RLS OFF "
            "(genuinely public):",
            file=sys.stderr,
        )
        for e in reachable:
            print(
                f"  {e['table']:<28} read={e['anon_select']} write={e['anon_write']}",
                file=sys.stderr,
            )
    if masked:
        print(
            f"note: {len(masked)} table(s) hold anon/authenticated grants but RLS is ON "
            "with no policies, so they are currently denied. Latent, not active: the "
            "protection is RLS, not the grant.",
            file=sys.stderr,
        )
    return reachable


def _revoke_api_roles(cur, live_tables: list[str]) -> None:
    """Remove anon/authenticated privileges from public tables and defaults.

    Defense in depth. RLS already denies these roles today; revoking the grants
    means a future `ALTER TABLE ... DISABLE ROW LEVEL SECURITY` cannot silently
    turn the bibliometric corpus public.
    """
    for role in SUPABASE_API_ROLES:
        for t in live_tables:
            cur.execute(f'REVOKE ALL ON TABLE "{t}" FROM "{role}";')
        cur.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM \"{role}\";"
        )
        print(f"revoke: ALL on public tables + defaults FROM {role}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--revoke-supabase-roles",
        action="store_true",
        help="Also REVOKE anon/authenticated from public tables and default "
             "privileges. Off by default: whether the Supabase API roles should "
             "ever have access is a project policy decision, not a default.",
    )
    args = ap.parse_args()

    try:
        with get_db_connection(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'app_readonly';")
                if not cur.fetchone():
                    cur.execute("CREATE ROLE app_readonly NOLOGIN;")
                    print("role app_readonly: CREATED (NOLOGIN; LOGIN diatur saat Task 3)")
                else:
                    print("role app_readonly: already exists")

                live_tables = _apply_readonly_grants(cur)

                if args.revoke_supabase_roles:
                    _revoke_api_roles(cur, live_tables)

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

                exposed = _report_exposure(cur)
                if exposed:
                    return 2
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())