"""Fase 1 pre-tasks R1 + R2 (reports/RECONCILIATION.md) — OWNER session.

R1: backfill kolom *_normalized yang hilang dari data/*_cleaned.csv via
    TEMP staging + csv.DictReader/executemany INSERT + UPDATE ... FROM
    (faithful copy, tanpa TRUNCATE/DELETE; junction tables tidak tersentuh),
    lalu CREATE INDEX (AC-DB-3) + SET NOT NULL bila tidak ada NULL
    (spec docs/04). INSERT dipilih agar driver-agnostic (psycopg v3/v2).
R2: ALTER TABLE publication_references ADD COLUMN reference_id BIGSERIAL PK
    (auto-number existing rows server-side; CSV tidak diubah).
Re-grant: GRANT SELECT idempoten ke app_readonly (docs/08 Security.md).

Batasan aman: tidak ada TRUNCATE / DELETE / DROP di script ini.
Tiap tabel R1 + R2 jalan dalam transaksinya sendiri — gagal = rollback total
untuk unit itu, DB kembali utuh.

DSN dari process env: DB_URL_OWNER (fallback DB_URL). Jangan commit DSN.

Usage:
    python scripts/backfill_r1_r2.py
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

# Driver: prefer psycopg v3 (lockfile), fall back to psycopg2 (local dev).
# Staging load memakai csv + executemany INSERT agar identik di kedua driver.
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

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

STATEMENT_TIMEOUT_MS = 10_000

# table -> {pk, normcol, spectype, csv, csvcols, index}
R1_TABLES = {
    "authors": {
        "pk": "author_id",
        "normcol": "author_name_normalized",
        "spectype": "VARCHAR(255)",
        "csv": DATA / "authors_cleaned.csv",
        "csvcols": ["author_id", "author_name", "author_name_normalized"],
        "index": "idx_authors_name_norm",
    },
    "institutions": {
        "pk": "institution_id",
        "normcol": "institution_name_normalized",
        "spectype": "TEXT",
        "csv": DATA / "institutions_cleaned.csv",
        "csvcols": [
            "institution_id",
            "institution_name",
            "city",
            "country",
            "institution_name_normalized",
        ],
        "index": "idx_institutions_name_norm",
    },
    "funding": {
        "pk": "funding_id",
        "normcol": "funding_agency_normalized",
        "spectype": "TEXT",
        "csv": DATA / "funding_cleaned.csv",
        "csvcols": [
            "publication_id",
            "funding_id",
            "funding_agency",
            "grant_number",
            "funding_text",
            "source_text",
            "funding_agency_normalized",
        ],
        "index": "idx_funding_agency_norm",
    },
}

GRANT_TABLES = ["authors", "institutions", "funding", "publication_references"]


def q_ident(name: str) -> str:
    if not name.replace("_", "").isalnum() or not name:
        raise ValueError(f"unsafe identifier: {name!r}")
    return f'"{name}"'


def backfill_one(cur, table: str, cfg: dict) -> dict:
    pk, normcol = cfg["pk"], cfg["normcol"]
    csvcols = cfg["csvcols"]
    stg = f"stg_{table}_r1"
    cols_sql = ", ".join(q_ident(c) + " TEXT" for c in csvcols)

    cur.execute(f"CREATE TEMP TABLE {q_ident(stg)} ({cols_sql}) ON COMMIT DROP;")
    with open(cfg["csv"], "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = [[r.get(c, "") for c in csvcols] for r in reader]
    placeholders = ", ".join(["%s"] * len(csvcols))
    cur.executemany(
        f"INSERT INTO {q_ident(stg)} ({', '.join(q_ident(c) for c in csvcols)}) "
        f"VALUES ({placeholders});",
        rows,
    )
    cur.execute(f"SELECT COUNT(*) FROM {q_ident(stg)};")
    staged = cur.fetchone()[0]

    cur.execute(
        f"ALTER TABLE {q_ident(table)} "
        f"ADD COLUMN IF NOT EXISTS {q_ident(normcol)} {cfg['spectype']};"
    )

    # ::text di sisi live agar robust bila tipe PK live != text.
    cur.execute(
        f"UPDATE {q_ident(table)} m SET {q_ident(normcol)} = s.{q_ident(normcol)} "
        f"FROM {q_ident(stg)} s WHERE m.{q_ident(pk)}::text = s.{q_ident(pk)};"
    )
    updated = cur.rowcount

    cur.execute(
        f"SELECT COUNT(*) FROM {q_ident(stg)} s WHERE NOT EXISTS "
        f"(SELECT 1 FROM {q_ident(table)} m WHERE m.{q_ident(pk)}::text = s.{q_ident(pk)});"
    )
    orphans = cur.fetchone()[0]

    cur.execute(
        f"SELECT COUNT(*) FROM {q_ident(stg)} "
        f"WHERE NULLIF({q_ident(normcol)}, '') IS NOT NULL;"
    )
    staged_nonblank = cur.fetchone()[0]
    cur.execute(
        f"SELECT COUNT(*) FROM {q_ident(table)} "
        f"WHERE NULLIF({q_ident(normcol)}, '') IS NOT NULL;"
    )
    live_nonblank = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {q_ident(table)} WHERE {q_ident(normcol)} IS NULL;")
    live_null = cur.fetchone()[0]

    notnull_applied = False
    if live_null == 0:
        cur.execute(
            f"ALTER TABLE {q_ident(table)} "
            f"ALTER COLUMN {q_ident(normcol)} SET NOT NULL;"
        )
        notnull_applied = True

    cur.execute(
        f"CREATE INDEX IF NOT EXISTS {q_ident(cfg['index'])} "
        f"ON {q_ident(table)} ({q_ident(normcol)});"
    )

    cur.execute(
        f"SELECT {q_ident(normcol)}, COUNT(*) FROM {q_ident(table)} "
        f"GROUP BY 1 ORDER BY 2 DESC LIMIT 3;"
    )
    groupby_top3 = [[r[0], r[1]] for r in cur.fetchall()]

    return {
        "staged": staged,
        "updated": updated,
        "orphan_staging_ids": orphans,
        "staged_nonblank": staged_nonblank,
        "live_nonblank": live_nonblank,
        "live_null": live_null,
        "notnull_applied": notnull_applied,
        "index": cfg["index"],
        "groupby_top3": groupby_top3,
    }


def add_reference_id(cur) -> dict:
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
        "AND table_name = 'publication_references' AND column_name = 'reference_id';"
    )
    if cur.fetchone():
        cur.execute("SELECT COUNT(*), COALESCE(MAX(reference_id), 0) FROM publication_references;")
        n, mx = cur.fetchone()
        return {"already_existed": True, "rows": n, "max_id": mx}
    # Tabel live memakai composite PK (publication_id, reference_order); spec
    # docs/04 meminta reference_id BIGSERIAL PK. Ganti dalam 1 transaksi:
    # tidak ada FK yang mereferensikan tabel ini (leaf), natural key
    # dipertahankan sebagai UNIQUE.
    cur.execute(
        "SELECT COUNT(*) FROM pg_constraint "
        "WHERE confrelid = to_regclass('public.publication_references');"
    )
    if cur.fetchone()[0]:
        raise RuntimeError("publication_references is referenced by an FK; aborting PK swap")
    cur.execute("ALTER TABLE publication_references DROP CONSTRAINT publication_references_pkey;")
    cur.execute(
        "ALTER TABLE publication_references "
        "ADD COLUMN reference_id BIGSERIAL PRIMARY KEY;"
    )
    cur.execute(
        "ALTER TABLE publication_references "
        "ADD CONSTRAINT publication_references_natural_key "
        "UNIQUE (publication_id, reference_order);"
    )
    cur.execute("SELECT COUNT(*), MAX(reference_id) FROM publication_references;")
    n, mx = cur.fetchone()
    return {"already_existed": False, "rows": n, "max_id": mx, "dense": (mx == n)}


def main() -> int:
    dsn = os.environ.get("DB_URL_OWNER") or os.environ.get("DB_URL")
    if not dsn:
        print("FATAL: set DB_URL_OWNER (fallback DB_URL) in process env.", file=sys.stderr)
        return 1
    if "@" not in dsn or "://" not in dsn:
        print("FATAL: DSN looks malformed (expected driver://...).", file=sys.stderr)
        return 1

    summary: dict = {"r1": {}, "r2": {}, "grants": []}
    conn = _driver.connect(dsn, connect_timeout=10)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}';")
            cur.execute("SELECT current_user;")
            summary["db_user"] = cur.fetchone()[0]

        for table, cfg in R1_TABLES.items():
            if not cfg["csv"].exists():
                print(f"FATAL: missing CSV {cfg['csv']}", file=sys.stderr)
                return 1
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}';")
                    summary["r1"][table] = backfill_one(cur, table, cfg)
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                print(f"R1 {table}: ROLLED BACK ({exc})", file=sys.stderr)
                return 1
            r = summary["r1"][table]
            ok = (
                r["orphan_staging_ids"] == 0
                and r["live_nonblank"] == r["staged_nonblank"]
                and r["live_null"] == 0
            )
            print(
                f"R1 {table}: staged={r['staged']} updated={r['updated']} "
                f"orphans={r['orphan_staging_ids']} live_nonblank={r['live_nonblank']} "
                f"live_null={r['live_null']} notnull={r['notnull_applied']} "
                f"index={r['index']} -> {'OK' if ok else 'CHECK'}"
            )
            if not ok:
                print(f"R1 {table}: verification mismatch, stopping.", file=sys.stderr)
                return 1

        try:
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}';")
                summary["r2"] = add_reference_id(cur)
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            print(f"R2: ROLLED BACK ({exc})", file=sys.stderr)
            return 1
        print(f"R2 publication_references.reference_id: {summary['r2']} -> OK")

        # Re-grant SELECT idempoten (docs/08). Gagal grant = warning, bukan fatal.
        conn.autocommit = True
        with conn.cursor() as cur:
            for t in GRANT_TABLES:
                try:
                    cur.execute(f"GRANT SELECT ON TABLE {q_ident(t)} TO app_readonly;")
                    summary["grants"].append({t: "granted"})
                except Exception as exc:  # noqa: BLE001
                    summary["grants"].append({t: f"SKIPPED ({exc})"})
            try:
                cur.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT SELECT ON TABLES TO app_readonly;"
                )
                summary["grants"].append({"default_privileges": "granted"})
            except Exception as exc:  # noqa: BLE001
                summary["grants"].append({"default_privileges": f"SKIPPED ({exc})"})
        print("grants:", json.dumps(summary["grants"]))
    finally:
        conn.close()

    print("SUMMARY " + json.dumps(summary, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
