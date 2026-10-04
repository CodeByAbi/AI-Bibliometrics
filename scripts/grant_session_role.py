"""Grant/re-grant DML untuk role app_session pada schema `app` (docs/08 §1.4).

Sibling of ``scripts/grant_readonly.py``, and deliberately kept separate from it.
That script grants ``app_readonly`` SELECT on ``public``; this one grants
``app_session`` write access to ``app`` and NOTHING ELSE. Neither script ever
touches the other's domain, which is what keeps the bibliometric read path and
the session write path on separate credentials (docs/03 §0.3 invariant 5).

Idempotent. Verifies BOTH directions and exits non-zero if either leaks:

    app_readonly  -> 0 privileges on schema app
    app_session   -> 0 privileges on schema public

That bidirectional assertion is the machine-checkable form of AC-SESSION-16.
One direction is the obvious one (a write role must not read the corpus); the
other is the one that actually bites in practice — a session role that inherits
``public`` reach can read every publication, and a later "just add an UPDATE for
a maintenance job" turns that into a corpus write.

Run order (migration creates the schema, this creates the privileges):

    python scripts/migrate.py up
    python scripts/grant_session_role.py

DSN: ``DB_URL_OWNER`` (fallback ``DB_URL``). Never commit a DSN.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# Ensure project root is in sys.path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.db import get_db_connection

#: The application (conversation) domain. Hard-coded rather than read from
#: ``Settings`` because this is an owner-side DDL/ACL script: it must not depend
#: on the runtime process environment, or a deployment with a typo'd
#: ``SESSION_SCHEMA`` could silently grant a role against the wrong schema.
APP_SCHEMA = "app"

SESSION_TABLES = (
    "research_sessions",
    "research_messages",
    "research_session_summaries",
)

#: Roles that must hold NO privilege on the application domain. ``app_readonly``
#: is listed because it is the retrieval read role and must never see conversation
#: state — if it did, a session table could be pulled into the AST whitelist by
#: accident and Text-to-SQL would gain a path to the transcript.
MUST_NOT_REACH_APP = ("app_readonly",)

#: Roles that must hold NO privilege on the bibliometric domain.
MUST_NOT_REACH_PUBLIC = ("app_session",)

#: Privileges the session role needs on its own domain. Explicit, and narrower
#: than "ALL": no TRUNCATE, no REFERENCES, no TRIGGER. An accidental
#: ``TRUNCATE research_messages`` cannot fire through this grant.
SESSION_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")


def _role_exists(cur, role: str) -> bool:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s;", (role,))
    return cur.fetchone() is not None


def _apply_session_grants(cur) -> list[str]:
    """Create the schema ACL and the table grants for ``app_session``."""
    if not _role_exists(cur, "app_session"):
        # NOLOGIN, matching grant_readonly.py: the password/LOGIN grant is an
        # operator wiring step (DB_URL_SESSION in .env), never something this
        # script invents.
        cur.execute("CREATE ROLE app_session NOLOGIN;")
        print(
            "role app_session: CREATED (NOLOGIN; LOGIN diatur saat wiring "
            "DB_URL_SESSION)"
        )
    else:
        print("role app_session: already exists")

    # Ownership stays with the migration owner. app_session gets USAGE (to
    # resolve names) but never CREATE, so it cannot add objects to the
    # application domain — a compromised session role cannot extend its own
    # reach.
    cur.execute(f"REVOKE ALL ON SCHEMA {APP_SCHEMA} FROM PUBLIC;")
    cur.execute(f"GRANT USAGE ON SCHEMA {APP_SCHEMA} TO app_session;")
    print(f"schema {APP_SCHEMA}: USAGE -> app_session, ALL revoked from PUBLIC")

    grants = ", ".join(SESSION_TABLE_PRIVILEGES)
    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = %s AND table_type = 'BASE TABLE'
        ORDER BY table_name;
        """,
        (APP_SCHEMA,),
    )
    live_tables = [r[0] for r in cur.fetchall()]

    missing_schema_tables = sorted(set(SESSION_TABLES) - set(live_tables))
    if missing_schema_tables:
        print(
            f"ERROR: schema {APP_SCHEMA} is missing {missing_schema_tables}. "
            f"Run `python scripts/migrate.py up` first.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    for t in live_tables:
        cur.execute(f'GRANT {grants} ON TABLE {APP_SCHEMA}."{t}" TO app_session;')

    # Default privileges cover objects created later. Revoking the PUBLIC half
    # matters as much as the grant: without it, any table added to `app` after
    # this script runs is writable by PUBLIC by default.
    cur.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {APP_SCHEMA} "
        f"GRANT {grants} ON TABLES TO app_session;"
    )
    cur.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {APP_SCHEMA} "
        "REVOKE ALL ON TABLES FROM PUBLIC;"
    )
    print(
        f"grants: {grants} on {len(live_tables)} {APP_SCHEMA} table(s) "
        "+ default privileges"
    )
    return live_tables


def _apply_negative_grants(cur) -> None:
    """Strip every accidental path between the two domains, in both directions."""
    for role in MUST_NOT_REACH_APP:
        if not _role_exists(cur, role):
            continue
        cur.execute(f"REVOKE ALL ON SCHEMA {APP_SCHEMA} FROM {role};")
        for t in SESSION_TABLES:
            cur.execute(f"REVOKE ALL ON TABLE {APP_SCHEMA}.\"{t}\" FROM {role};")
        print(f"revoke: ALL on {APP_SCHEMA} FROM {role}")

    for role in MUST_NOT_REACH_PUBLIC:
        if not _role_exists(cur, role):
            continue
        cur.execute(f"REVOKE ALL ON SCHEMA public FROM {role};")
        cur.execute("SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE';")
        for (t,) in cur.fetchall():
            cur.execute(f'REVOKE ALL ON TABLE public."{t}" FROM {role};')
        cur.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM "
            f'"{role}";'
        )
        print(f"revoke: ALL on public + defaults FROM {role}")


def _schema_exists(cur, schema: str) -> bool:
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s);",
        (schema,),
    )
    return bool(cur.fetchone()[0])


def _leaked_privileges(cur, role: str, schema: str) -> list[str]:
    """Return the (table, privilege) pairs ``role`` holds on ``schema``.

    Includes the schema-level USAGE check, because USAGE alone is enough to
    resolve a name inside a schema whose tables are individually locked down,
    so a USAGE-only leak still counts as a boundary defect.

    An absent schema reports no leaks rather than raising: before migration 005
    the ``app`` schema legitimately does not exist, and ``--check-only`` has to be
    runnable in that state to be useful as a pre-flight probe. The caller
    distinguishes "clean" from "nothing to check" via ``schema_exists``.
    """
    if not _schema_exists(cur, schema):
        return []
    if not _role_exists(cur, role):
        return []

    leaks: list[str] = []
    cur.execute(
        "SELECT has_schema_privilege(%s, %s, 'USAGE');", (role, schema)
    )
    if cur.fetchone()[0]:
        leaks.append(f"{schema}(USAGE)")

    cur.execute(
        """
        SELECT c.relname, p.priv_type
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL unnest(
            ARRAY['SELECT','INSERT','UPDATE','DELETE',
                  'TRUNCATE','REFERENCES','TRIGGER']
        ) AS p(priv_type)
        WHERE n.nspname = %s
          AND c.relkind = 'r'
          AND has_table_privilege(%s, c.oid, p.priv_type)
        ORDER BY c.relname, p.priv_type;
        """,
        (schema, role),
    )
    for relname, priv in cur.fetchall():
        leaks.append(f"{schema}.{relname}({priv})")
    return leaks


def verify_isolation(cur) -> list[str]:
    """Both-direction privilege check. Returns a list of human-readable leaks."""
    problems: list[str] = []

    if not _schema_exists(cur, APP_SCHEMA):
        # Pre-migration state, not a failure. Say so plainly rather than
        # reporting a clean bill of health for a schema that is not there.
        print(
            f"note: schema `{APP_SCHEMA}` does not exist yet. Run "
            f"`python scripts/migrate.py up` first; nothing to verify."
        )
        return problems

    for role in MUST_NOT_REACH_APP:
        leaks = _leaked_privileges(cur, role, APP_SCHEMA)
        if leaks:
            problems.append(
                f"{role} unexpectedly reaches {APP_SCHEMA}: {', '.join(leaks)}"
            )
        else:
            print(f"verify OK: {role} has 0 privileges on {APP_SCHEMA}")

    for role in MUST_NOT_REACH_PUBLIC:
        leaks = _leaked_privileges(cur, role, "public")
        if leaks:
            problems.append(f"{role} unexpectedly reaches public: {', '.join(leaks)}")
        else:
            print(f"verify OK: {role} has 0 privileges on public")

    # The session role must actually be able to do its job, otherwise "0 leaks"
    # could just mean the role was never granted anything.
    cur.execute(
        """
        SELECT bool_and(
            has_table_privilege('app_session', c.oid, 'SELECT')
            AND has_table_privilege('app_session', c.oid, 'INSERT')
            AND has_table_privilege('app_session', c.oid, 'UPDATE')
            AND has_table_privilege('app_session', c.oid, 'DELETE')
        )
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind = 'r';
        """,
        (APP_SCHEMA,),
    )
    row = cur.fetchone()
    if not row or not row[0]:
        problems.append(
            f"app_session is missing SELECT/INSERT/UPDATE/DELETE on all "
            f"{APP_SCHEMA} tables"
        )
    else:
        print(f"verify OK: app_session holds DML on all {APP_SCHEMA} tables")

    # Explicit TRUNCATE denial: the grant is SELECT/INSERT/UPDATE/DELETE only.
    cur.execute(
        """
        SELECT bool_or(has_table_privilege('app_session', c.oid, 'TRUNCATE'))
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind = 'r';
        """,
        (APP_SCHEMA,),
    )
    row = cur.fetchone()
    if row and row[0]:
        problems.append(
            f"app_session holds TRUNCATE on {APP_SCHEMA} (grant set drifted)"
        )

    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check-only",
        action="store_true",
        help="Verify privileges without applying any GRANT/REVOKE.",
    )
    args = ap.parse_args()

    try:
        with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
            if args.check_only:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_type = 'BASE TABLE';",
                    (APP_SCHEMA,),
                )
                live_tables = [r[0] for r in cur.fetchall()]
                print(f"check-only: {len(live_tables)} {APP_SCHEMA} table(s) present")
            else:
                live_tables = _apply_session_grants(cur)
                _apply_negative_grants(cur)

            problems = verify_isolation(cur)
            if problems:
                print("VERIFY FAIL:", file=sys.stderr)
                for p in problems:
                    print(f"  {p}", file=sys.stderr)
                return 1
            print(
                "verify: isolation intact — bibliometric read path and session "
                "write path hold no overlapping privileges"
            )
    except SystemExit:
        raise
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())