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
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

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
        return f"{u.scheme}://{u.username or '?'}@{u.hostname or '?'}:{u.port or '?ht'}/{ (u.path or '/?').lstrip('/') }"
    except Exception:
        return "<unparsable-dsn>"


def norm_type(data_type: str) -> str:
    t = data_type.strip().lower()
    return TYPE_ALIASES.get(t, t)


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
        conn = _driver.connect(dsn, connect_timeout=10, options="-c statement_timeout=10s")
    except Exception as exc:
        print(f"ERROR: connection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("HINT (no secrets shown): verify host/port (Supabase pooler normally uses "
              "port 6543, not 5432), user format (postgres.<project-ref>), password, "
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

    # Gate 3: SELECT grants for the auditing role (informational, no write probes ever).
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.role_table_grants
                WHERE grantee = current_user AND privilege_type = 'SELECT'
                  AND table_schema = 'public';
                """
            )
            granted = {r[0] for r in cur.fetchall()}
    for table in EXPECTED:
        if table in live and table not in granted:
            warnings.append(f"role '{db_user}' lacks SELECT on {table}")

    if not vector_ext:
        notes.append("extension 'vector' absent (expected: Task 1 PENDING, AC-DB-5/6).")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "driver": _DRIVER,
        "target": safe_label(dsn),
        "db_user": db_user,
        "status": "MATCH" if not errors else "MISMATCH",
        "errors": errors,
        "warnings": warnings,
        "notes": notes,
        "tables": table_reports,
        "chunk_stats": {"chunks": chunk_stats[0], "distinct_publications": chunk_stats[1]}
        if chunk_stats
        else None,
    }

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "schema_audit.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out_dir, "schema_audit.md"), "w", encoding="utf-8") as f:
        f.write(f"# Schema Audit — {result['generated_at']}\n\n")
        f.write(f"**Status: {result['status']}** · target `{result['target']}` · role `{db_user}`\n\n")
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
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
