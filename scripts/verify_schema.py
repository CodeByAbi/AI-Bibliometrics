"""Task 0 — Prototype schema audit (Phase 0).

Read-only verification of the live PostgreSQL against the 9 canonical
Silver tables defined in docs/04 Database Schema.md §4 (+ checklist §10).

Reads DB_URL from the process environment (never from a committed file,
never printed — logs show host/db/user only, password always redacted).

Exit codes: 0 = exact match · 2 = schema mismatch · 1 = connection/config error.

Usage:
    python scripts/verify_schema.py [--dsn-env DB_URL] [--out-dir reports]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

# Load project .env so DB_URL resolves without manual export (python-dotenv
# is pinned in requirements.txt). Silent no-op if package missing.
try:
    from dotenv import load_dotenv

    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

# Windows libpq quirk: a stray %APPDATA%/postgresql/root.crt forces
# verify-ca against the Supabase pooler's private CA and fails even with
# sslmode=require. Reuse the parking helper from scripts/db.py when available.
try:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from scripts.db import park_windows_root_crt
except ImportError:  # pragma: no cover — fallback: connect without parking
    import contextlib

    park_windows_root_crt = contextlib.nullcontext  # type: ignore[no-redef]

# Driver: prefer psycopg v3 (docker image), fall back to psycopg2 (local dev).
try:
    import psycopg as _driver  # type: ignore[no-redef]

    _DRIVER = "psycopg3"
except ImportError:  # pragma: no cover
    try:
        import psycopg2 as _driver  # type: ignore[no-redef]

        _DRIVER = "psycopg2"
    except ImportError:
        print("ERROR: no postgres driver (install psycopg[binary] or psycopg2).", file=sys.stderr)
        sys.exit(1)


# Canonical Silver schema, docs/04 §4.1–§4.5. Values are advisory base types;
# presence/absence of tables+columns is the hard gate, type drift is a warning.
EXPECTED: dict[str, dict[str, str]] = {
    "publications": {
        "publication_id": "varchar", "title": "text", "abstract": "text",
        "doi": "varchar", "eid": "varchar", "year": "smallint",
        "citation_count": "integer", "document_type": "varchar",
        "publication_stage": "varchar", "open_access": "varchar",
        "language_of_original_document": "varchar", "publisher": "varchar",
        "source": "text", "volume": "varchar", "issue": "varchar",
        "art_no": "varchar", "page_start": "varchar", "page_end": "varchar",
    },
    "authors": {
        "author_id": "varchar", "author_name": "varchar",
        "author_name_normalized": "varchar",
    },
    "institutions": {
        "institution_id": "varchar", "institution_name": "text",
        "institution_name_normalized": "text", "city": "varchar", "country": "varchar",
    },
    "keywords": {
        "keyword_id": "bigint", "publication_id": "varchar",
        "keyword": "varchar", "keyword_type": "varchar",
    },
    "funding": {
        "funding_id": "bigint", "publication_id": "varchar",
        "funding_agency": "text", "funding_agency_normalized": "text",
        "grant_number": "varchar", "funding_text": "text",
    },
    "pub_author": {
        "publication_id": "varchar", "author_id": "varchar",
        "author_order": "smallint",
    },
    "pub_institution": {
        "publication_id": "varchar", "institution_id": "varchar",
    },
    "publication_references": {
        "reference_id": "bigint", "publication_id": "varchar",
        "reference_order": "integer", "reference_text": "text",
    },
    "chunks": {
        "chunk_id": "bigint", "publication_id": "varchar",
        "chunk_text": "text", "section": "varchar",
    },
}

# *_normalized aggregation columns must be indexed (docs/04 AC-DB-3).
NORMALIZED_INDEX_HINTS = {
    "authors": "author_name_normalized",
    "institutions": "institution_name_normalized",
    "funding": "funding_agency_normalized",
}

TYPE_ALIASES = {
    "character varying": "varchar",
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
    "double precision": "float8",
}


def safe_label(dsn: str) -> str:
    """host/db/user only — password never leaves this function unmasked."""
    try:
        u = urlparse(dsn)
        return f"{u.scheme}://{u.username or '?'}@{u.hostname or '?'}:{u.port or '?'}/{(u.path or '/?').lstrip('/')}"
    except Exception:
        return "<unparsable-dsn>"


def public_label(dsn: str) -> str:
    """Artifact-safe label for committed reports: role without project-ref.

    Console logs may keep safe_label(); committed JSON/MD must not carry the
    Supabase project-ref embedded in the pooler username.
    """
    try:
        u = urlparse(dsn)
        user = (u.username or "?").split(".")[0]
        return f"{u.scheme}://{user}@{u.hostname or '?'}:{u.port or '?'}/{(u.path or '/?').lstrip('/')}"
    except Exception:
        return "<unparsable-dsn>"


def norm_type(data_type: str) -> str:
    t = data_type.strip().lower()
    return TYPE_ALIASES.get(t, t)


# ===========================================================================
# Application (conversation) schema — docs/04 §13
# ===========================================================================
# Audited SEPARATELY from the Silver/Gold checks above, and deliberately
# optional: session persistence is off until an operator supplies
# DB_URL_SESSION, so an absent `app` schema is a note, not an error. Once the
# schema exists, drift in it IS an error, because the runtime reads and writes
# those tables on every session-aware request.
APP_SCHEMA = "app"

EXPECTED_APP_TABLES: dict[str, list[str]] = {
    "research_sessions": [
        "session_id",
        "title",
        "status",
        "created_at",
        "updated_at",
        "last_message_at",
    ],
    "research_messages": [
        "message_id",
        "session_id",
        "role",
        "content",
        "status",
        "applied_filters",
        "request_id",
        "route",
        "seq",
        "created_at",
        # Migration 006. Per-turn rendering provenance, fenced by
        # audit_provenance_columns() below — see the exception note there.
        "evidence_objects",
        "sources",
    ],
    "research_session_summaries": [
        "session_id",
        "summary",
        "messages_covered",
        "created_at",
        "updated_at",
    ],
}

#: Indexes migration 005 creates. Reported when missing, but as a warning: the
#: tables still function without them, they just degrade to sequential scans.
EXPECTED_APP_INDEXES = (
    "idx_research_messages_session_created",
    "idx_research_sessions_last_message",
    "idx_research_sessions_updated",
)

#: Per-turn provenance columns added by migration 006. These are the ONLY
#: columns in schema `app` permitted to carry bibliometric metric values, and
#: only as an immutable snapshot of one already-verified response.
#:
#: They are a documented, owner-signed-off exception to the Session Isolation
#: Invariant (migration 006 header; docs/03 §0.3 #5). The exception is narrow,
#: and this table is what keeps it narrow:
#:
#:   * they must be JSONB — an opaque snapshot, never a scalar metric column
#:     that a query could aggregate with plain SQL arithmetic;
#:   * they must appear on research_messages ONLY. A provenance column on
#:     research_sessions would be a session-level aggregate, which is exactly
#:     what migration 005 refused to create and what these columns exist not to
#:     become.
#:
#: They are deliberately NOT in the `banned` metric-column set below. That set
#: targets session-level *aggregates*; these are per-turn receipts. A snapshot
#: row cannot answer a question — retrieval re-queries `public` for that — so
#: treating it as a cached aggregate would be a false positive.
PROVENANCE_COLUMNS: dict[str, tuple[str, ...]] = {
    "research_messages": ("evidence_objects", "sources"),
}

#: Tables that must NOT carry provenance columns. Enforced, not merely intended:
#: see PROVENANCE_COLUMNS above.
PROVENANCE_FORBIDDEN_TABLES = ("research_sessions", "research_session_summaries")


def audit_provenance_columns(live: dict[str, dict[str, str]]) -> dict:
    """Audit the migration 006 provenance exception.

    Returns three lists, all of which should be empty on a healthy database:

    ``wrong_type``    a provenance column that is not JSONB. A scalar or array
                      column would let a snapshot be summed, compared or
                      filtered with ordinary SQL, which is precisely the
                      aggregate use the exception forbids.
    ``misplaced``     a provenance column on a table other than
                      research_messages — i.e. a session-level aggregate.
    ``unexpected``    a provenance-named column that this script does not know
                      about. Catches someone adding ``evidence_count_v2``
                      without registering (and therefore without fencing) it.
    """
    known = {col for cols in PROVENANCE_COLUMNS.values() for col in cols}
    wrong_type: list[str] = []
    misplaced: list[str] = []
    unexpected: list[str] = []

    for table, columns in live.items():
        for col, dtype in columns.items():
            # Any column that looks like a provenance snapshot must be
            # registered above, or this reports it.
            if col in known:
                if table not in PROVENANCE_COLUMNS:
                    misplaced.append(f"{table}.{col}")
                elif dtype != "jsonb":
                    wrong_type.append(f"{table}.{col} is {dtype}, expected jsonb")
            elif col.endswith(("_objects", "_sources", "_evidence")):
                unexpected.append(f"{table}.{col}")

    for table in PROVENANCE_FORBIDDEN_TABLES:
        for col in live.get(table, {}):
            if col in known:
                misplaced.append(f"{table}.{col}")

    return {
        "wrong_type": sorted(wrong_type),
        "misplaced": sorted(misplaced),
        "unexpected": sorted(unexpected),
    }


def audit_app_schema(cur) -> dict:
    """Audit the `app` schema and, critically, its isolation from `public`.

    The important output is not the column list — it is
    ``foreign_keys_into_public``, which must always be empty. A foreign key from
    `app` into `public` would make the bibliometric corpus depend on the session
    lifecycle, inverting the Session Isolation Invariant (docs/03 §0.3 #5) and
    letting a session delete cascade into the corpus.
    """
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s);",
        (APP_SCHEMA,),
    )
    if not cur.fetchone()[0]:
        return {"present": False}

    cur.execute(
        """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s
        ORDER BY table_name, ordinal_position;
        """,
        (APP_SCHEMA,),
    )
    live: dict[str, dict[str, str]] = {}
    for tname, cname, dtype in cur.fetchall():
        live.setdefault(tname, {})[cname] = norm_type(dtype)

    cur.execute(
        """
        SELECT src.relname, tgt.relname, con.confdeltype
        FROM pg_constraint con
        JOIN pg_class src ON src.oid = con.conrelid
        JOIN pg_namespace srcn ON srcn.oid = src.relnamespace
        JOIN pg_class tgt ON tgt.oid = con.confrelid
        JOIN pg_namespace tgtn ON tgtn.oid = tgt.relnamespace
        WHERE con.contype = 'f'
          AND srcn.nspname = %s
          AND tgtn.nspname <> %s;
        """,
        (APP_SCHEMA, APP_SCHEMA),
    )
    fks_into_public = [
        {"from": s, "to": t, "on_delete": d} for s, t, d in cur.fetchall()
    ]

    cur.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname = %s;", (APP_SCHEMA,)
    )
    indexes = sorted({r[0] for r in cur.fetchall()})

    # Columns that would indicate a cached bibliometric aggregate.
    banned = {
        "publication_count",
        "citation_count",
        "author_count",
        "institution_count",
        "expertise_score",
        "growth_score",
        "evidence_count",
    }
    metric_columns = sorted(
        f"{t}.{c}"
        for t, cols in live.items()
        for c in cols
        if c in banned
    )

    return {
        "present": True,
        "tables": live,
        "indexes": indexes,
        "missing_indexes": sorted(set(EXPECTED_APP_INDEXES) - set(indexes)),
        "foreign_keys_into_public": fks_into_public,
        "metric_columns": metric_columns,
        "provenance": audit_provenance_columns(live),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Task 0 prototype schema audit (read-only).")
    ap.add_argument("--dsn-env", default="DB_URL", help="Env var holding the connection string.")
    ap.add_argument("--out-dir", default="reports", help="Directory for JSON/MD audit artifacts.")
    ap.add_argument("--sslmode", default="require",
                    help="Appended to the DSN unless it already sets sslmode. "
                         "Cloud pools (Supabase/Neon) mandate 'require'; "
                         "pass 'prefer'/'disable' for local plaintext Postgres.")
    ap.add_argument("--sslrootcert", default="",
                    help="Optional CA bundle path for full chain verification "
                         "(libpq sslrootcert). Needed on machines whose default "
                         "root.crt cannot validate the server's private CA.")
    ap.add_argument("--role", default="app_readonly",
                    help="Runtime role whose SELECT grants Gate 3 verifies. Must be "
                         "checked by NAME, not by the auditing identity: an owner "
                         "holds every privilege implicitly, so auditing grants as "
                         "the owner is vacuous. Falls back to current_user when the "
                         "role does not exist.")
    args = ap.parse_args()

    dsn = os.environ.get(args.dsn_env, "").strip().strip("'\"")
    if not dsn:
        print(f"ERROR: env var {args.dsn_env} is not set. Export it (never commit it).", file=sys.stderr)
        return 1
    if "sslmode=" not in dsn.lower():
        sep = "&" if "?" in dsn else "?"
        dsn += f"{sep}sslmode={args.sslmode}"
    if args.sslrootcert and "sslrootcert=" not in dsn.lower():
        dsn += f"&sslrootcert={args.sslrootcert}"

    print(f"[verify_schema] driver={_DRIVER} target={safe_label(dsn)}")
    try:
        with park_windows_root_crt():
            conn = _driver.connect(dsn, connect_timeout=10, options="-c statement_timeout=10s")
    except Exception as exc:
        print(f"ERROR: connection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("HINT (no secrets shown): verify host/port (Supabase pooler offers "
              "session mode :5432 and transaction mode :6543), user format "
              "(postgres.<project-ref>), password, "
              "and DB IP-allowlist for this machine.", file=sys.stderr)
        return 1

    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    table_reports: dict = {}

    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user;")
            db_user = cur.fetchone()[0]

            cur.execute(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position;
                """
            )
            live: dict[str, dict[str, str]] = {}
            for tname, cname, dtype in cur.fetchall():
                live.setdefault(tname, {})[cname] = norm_type(dtype)

            cur.execute(
                "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public';"
            )
            index_defs = [(r[0], r[1], r[2]) for r in cur.fetchall()]

            cur.execute("SELECT COUNT(*) FROM pg_extension WHERE extname = 'vector';")
            vector_ext = cur.fetchone()[0] > 0

            chunk_stats = None
            if "chunks" in live:
                try:
                    cur.execute("SELECT COUNT(*), COUNT(DISTINCT publication_id) FROM chunks;")
                    chunk_stats = cur.fetchone()
                except Exception as exc:
                    warnings.append(f"chunks ratio probe failed (non-fatal): {exc}")
                    conn.rollback()

            cur.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname = current_user;"
            )
            auditor_is_superuser = bool(cur.fetchone()[0])

            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s);",
                (args.role,),
            )
            runtime_role_exists = bool(cur.fetchone()[0])

            # Gate 3 checks the RUNTIME role, not the auditor. Querying
            # role_table_grants for current_user is vacuous when the audit runs
            # as the table owner: the owner holds every privilege implicitly, so
            # the check always passes and can never detect a missing grant.
            # has_table_privilege() accounts for inherited membership too.
            privilege_target = args.role if runtime_role_exists else db_user
            if runtime_role_exists:
                cur.execute(
                    """
                    SELECT c.relname
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r'
                      AND has_table_privilege(%s, c.oid, 'SELECT')
                    """,
                    (privilege_target,),
                )
            else:
                cur.execute(
                    """
                    SELECT table_name
                    FROM information_schema.role_table_grants
                    WHERE grantee = current_user AND privilege_type = 'SELECT'
                      AND table_schema = 'public';
                    """
                )
            granted = {r[0] for r in cur.fetchall()}

            # Gate 4: foreign-key column index coverage. A composite PK only
            # indexes its leading column, so the reverse side of every junction
            # needs its own index or FK lookups and ON DELETE CASCADE degrade to
            # sequential scans. indkey is a 0-based int2vector: an FK column is
            # covered when it is the LEADING column of some index.
            cur.execute(
                """
                SELECT c.conrelid::regclass::text AS tbl, a.attname AS col
                FROM pg_constraint c
                JOIN pg_attribute a
                  ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                WHERE c.contype = 'f'
                  AND c.connamespace = 'public'::regnamespace
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_index i
                      WHERE i.indrelid = c.conrelid AND i.indkey[0] = a.attnum
                  )
                ORDER BY tbl, col;
                """
            )
            fk_index_gaps = [(r[0], r[1]) for r in cur.fetchall()]

            # Application schema (conversation state). Independent of the Silver
            # gates above: an absent `app` schema is a note, not a failure,
            # because session persistence is opt-in via DB_URL_SESSION.
            app_audit = audit_app_schema(cur)

    if app_audit["present"]:
        # NOTE: loop var deliberately NOT named `expected_cols` — the Silver Gate 1
        # loop below binds that name to a dict[str, str] in the same function
        # scope, and reusing it for a list[str] makes mypy infer the wrong type
        # for the pre-existing loop.
        for table, app_cols in EXPECTED_APP_TABLES.items():
            if table not in app_audit["tables"]:
                errors.append(f"missing app table: {table}")
                continue
            live_cols = app_audit["tables"][table]
            for col in app_cols:
                if col not in live_cols:
                    errors.append(f"missing app column: {table}.{col}")
        for idx in app_audit["missing_indexes"]:
            warnings.append(
                f"missing app index: {idx} (session reads degrade to seq scans)"
            )
        # The two hard invariants. Both are structural, so they are errors, not
        # warnings: either one means the Session Isolation Invariant is broken.
        for fk in app_audit["foreign_keys_into_public"]:
            errors.append(
                f"VIOLATION Session Isolation Invariant: app.{fk['from']} has a "
                f"foreign key to {fk['to']} — session lifecycle must never reach "
                f"the bibliometric corpus"
            )
        for col in app_audit["metric_columns"]:
            errors.append(
                f"VIOLATION data ownership: app.{col} is a bibliometric metric. "
                f"Session tables store conversation state only; metrics must be "
                f"retrieved from `public` on demand."
            )
        # The migration 006 provenance fence. The exception is legitimate only
        # while it stays per-turn, opaque and registered; each of these three
        # failures is a widening of it, so they are errors rather than warnings.
        prov = app_audit["provenance"]
        for entry in prov["wrong_type"]:
            errors.append(
                f"VIOLATION provenance fence: app.{entry}. A provenance snapshot "
                f"must be JSONB so it cannot be summed or filtered as a scalar "
                f"metric (migration 006)."
            )
        for entry in prov["misplaced"]:
            errors.append(
                f"VIOLATION provenance fence: app.{entry} is a provenance column "
                f"on a session-level table. Per-turn snapshots may not become a "
                f"session aggregate (migration 006)."
            )
        for entry in prov["unexpected"]:
            errors.append(
                f"VIOLATION provenance fence: app.{entry} looks like an "
                f"unregistered provenance snapshot. Register and fence it in "
                f"PROVENANCE_COLUMNS, or remove it."
            )
    else:
        notes.append(
            f"schema `{APP_SCHEMA}` absent — session persistence disabled "
            f"(expected until DB_URL_SESSION is configured)."
        )
    # Gate 1: table + column presence (hard), type drift (warning).
    for table, expected_cols in EXPECTED.items():
        rep = {"missing_columns": [], "type_drift": {}, "extra_columns": []}
        if table not in live:
            errors.append(f"missing table: {table}")
            table_reports[table] = rep
            continue
        live_cols = live[table]
        for col, want in expected_cols.items():
            if col not in live_cols:
                errors.append(f"missing column: {table}.{col}")
                rep["missing_columns"].append(col)
            elif live_cols[col] != want:
                warnings.append(f"type drift: {table}.{col} live={live_cols[col]} spec={want}")
                rep["type_drift"][col] = {"live": live_cols[col], "spec": want}
        for col in live_cols:
            if col not in expected_cols:
                rep["extra_columns"].append(col)
                notes.append(f"extra column (prototype-tolerated): {table}.{col}")
        table_reports[table] = rep

    # Gate 2: *_normalized index hints (AC-DB-3).
    for table, col in NORMALIZED_INDEX_HINTS.items():
        hit = [ix for (t, ix, _def) in index_defs if t == table and col in _def]
        if not hit:
            warnings.append(f"no index covering {table}.{col} (AC-DB-3 wants it indexed)")

    # Gate 3: SELECT grants for the RUNTIME role (informational, no write probes).
    if auditor_is_superuser:
        warnings.append(
            f"auditor '{db_user}' is a superuser: schema inspection is authoritative, "
            "but it cannot demonstrate privilege containment (see Gate 3 on "
            f"'{privilege_target}')"
        )
    if not runtime_role_exists:
        warnings.append(
            f"runtime role '{args.role}' does not exist; Gate 3 fell back to the "
            f"auditing role '{db_user}' (run scripts/grant_readonly.py first)"
        )
    for table in EXPECTED:
        if table in live and table not in granted:
            warnings.append(f"role '{privilege_target}' lacks SELECT on {table}")

    # Gate 4: foreign-key column index coverage. Advisory rather than an error:
    # a missing FK index degrades performance, it does not corrupt the schema.
    # Resolution: database/migrations/004_index_and_integrity_hardening.sql
    for tbl, col in fk_index_gaps:
        warnings.append(
            f"FK column {tbl}.{col} is not the leading column of any index "
            "(FK lookups and ON DELETE CASCADE will sequentially scan)"
        )

    if not vector_ext:
        notes.append("extension 'vector' absent (expected: Task 1 PENDING, AC-DB-5/6).")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "driver": _DRIVER,
        "target": public_label(dsn),
        "db_user": db_user,
        "auditor_is_superuser": auditor_is_superuser,
        "privilege_checked_role": privilege_target,
        "runtime_role_exists": runtime_role_exists,
        "status": "MATCH" if not errors else "MISMATCH",
        "errors": errors,
        "warnings": warnings,
        "notes": notes,
        "fk_index_gaps": [{"table": t, "column": c} for t, c in fk_index_gaps],
        "tables": table_reports,
        "app_schema": app_audit,
        "chunk_stats": {"chunks": chunk_stats[0], "distinct_publications": chunk_stats[1]}
        if chunk_stats
        else None,
    }

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "schema_audit.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out_dir, "schema_audit.md"), "w", encoding="utf-8") as f:
        f.write(f"# Schema Audit — {result['generated_at']}\n\n")
        f.write(f"**Status: {result['status']}** · target `{result['target']}` · "
                f"auditor `{db_user}` · grants checked for `{privilege_target}`\n\n")
        f.write(f"- Auditor is superuser: **{auditor_is_superuser}**\n")
        f.write(f"- Runtime role `{args.role}` exists: **{runtime_role_exists}**\n")
        f.write(f"- FK columns lacking a leading-column index: **{len(fk_index_gaps)}**\n\n")
        if app_audit["present"]:
            f.write("## Application schema (`app`) — conversation state\n\n")
            f.write(f"- Tables: **{len(app_audit['tables'])}**\n")
            f.write(f"- Indexes: **{len(app_audit['indexes'])}**\n")
            f.write(
                "- Foreign keys into `public`: "
                f"**{len(app_audit['foreign_keys_into_public'])}** "
                "(must be 0 — Session Isolation Invariant)\n"
            )
            f.write(
                "- Bibliometric metric columns: "
                f"**{len(app_audit['metric_columns'])}** "
                "(must be 0 — data ownership rule)\n"
            )
            prov = app_audit["provenance"]
            f.write(
                "- Provenance fence (migration 006 exception): "
                f"wrong_type **{len(prov['wrong_type'])}**, "
                f"misplaced **{len(prov['misplaced'])}**, "
                f"unregistered **{len(prov['unexpected'])}** "
                "(all must be 0 — per-turn snapshots stay JSONB and stay "
                "on `research_messages`)\n\n"
            )
        else:
            f.write("## Application schema (`app`)\n\n_absent_ — session "
                    "persistence disabled (no DB_URL_SESSION)\n\n")
        for section in ("errors", "warnings", "notes"):
            items = result[section]
            f.write(f"## {section} ({len(items)})\n")
            f.write(("".join(f"- {i}\n" for i in items) or "_none_\n") + "\n")

    print(f"[verify_schema] status={result['status']} "
          f"errors={len(errors)} warnings={len(warnings)} notes={len(notes)}")
    for e in errors:
        print(f"  ERROR: {e}")
    for w in warnings:
        print(f"  WARN:  {w}")
    print(f"[verify_schema] artifacts: {args.out_dir}/schema_audit.json + schema_audit.md")
    if app_audit["present"]:
        prov = app_audit["provenance"]
        prov_violations = (
            len(prov["wrong_type"]) + len(prov["misplaced"]) + len(prov["unexpected"])
        )
        print(
            f"[verify_schema] app schema: "
            f"{len(app_audit['tables'])} table(s), "
            f"fk_into_public={len(app_audit['foreign_keys_into_public'])} (must be 0), "
            f"metric_columns={len(app_audit['metric_columns'])} (must be 0), "
            f"provenance_violations={prov_violations} (must be 0)"
        )
    else:
        print("[verify_schema] app schema: absent (session persistence disabled)")
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
