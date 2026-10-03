"""Migration ledger and runner for database/migrations/*.sql.

Replaces `CREATE TABLE IF NOT EXISTS` as the idempotency mechanism. The
`IF NOT EXISTS` pattern silently no-ops after manual DDL drift, which means a
migration can be recorded as "applied" while the database actually holds
something else. This runner instead:

  * records every applied migration with a SHA-256 of its file contents,
  * refuses to re-apply a recorded migration,
  * reports a CHECKSUM MISMATCH as drift rather than proceeding silently,
  * supports `baseline` to adopt a pre-existing database (record the existing
    migrations as applied without executing them).

Requires an owner-privileged DSN because it writes DDL. Reads ``DB_URL_OWNER``
first and falls back to ``DB_URL``; if neither is set, exits with guidance.

Usage:
    python scripts/migrate.py status
    python scripts/migrate.py up [--dry-run]
    python scripts/migrate.py baseline [--from VER] [--to VER] --yes

Adopting a database that predates the ledger, then applying one migration:
    python scripts/migrate.py baseline --to 003 --yes   # adopt 000-003, leave 004 pending
    python scripts/migrate.py up                      # executes 004 only
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

try:
    from dotenv import load_dotenv

    load_dotenv(
        pathlib.Path(__file__).resolve().parent.parent / ".env", override=False
    )
except ImportError:  # pragma: no cover
    pass

try:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from scripts.db import _DRIVER, get_db_connection, safe_dsn_label
except ImportError as exc:  # pragma: no cover
    print("ERROR: could not import scripts.db (run from the repository root).", file=sys.stderr)
    raise SystemExit(1) from exc

# Driver selection (psycopg3 with a psycopg2 fallback) lives in scripts.db,
# which this script reuses rather than repeating.

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "database" / "migrations"

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version      TEXT PRIMARY KEY,
    checksum     TEXT        NOT NULL,
    statements   INTEGER     NOT NULL DEFAULT 0,
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def checksum(sql: str) -> str:
    """SHA-256 of the file contents, so an edited-after-apply file is detected."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def discover() -> list[tuple[str, pathlib.Path]]:
    """Return (version, path) for every ``*.sql`` migration, ordered by filename.

    The filename stem is the version. Ordering is lexicographic, so the numeric
    zero-padding in the existing files (000_, 001_, ...) is what makes this
    correct; a future migration must keep three digits.
    """
    if not MIGRATIONS_DIR.is_dir():
        print(f"ERROR: migrations directory not found: {MIGRATIONS_DIR}", file=sys.stderr)
        raise SystemExit(1)
    found = [
        (p.stem, p) for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.is_file()
    ]
    if not found:
        print(f"WARNING: no *.sql files in {MIGRATIONS_DIR}", file=sys.stderr)
    return found


def resolve_dsn(explicit: str | None) -> str:
    dsn = (explicit or os.environ.get("DB_URL_OWNER") or os.environ.get("DB_URL") or "").strip()
    if not dsn:
        print(
            "ERROR: no DSN. Set DB_URL_OWNER (owner credentials, required for DDL) "
            "or pass --dsn-env NAME. Refusing to fall back to nothing.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return dsn.strip("'\"")


def read_ledger(cur) -> dict[str, dict[str, object]]:
    cur.execute("SELECT version, checksum, applied_at FROM schema_migrations")
    return {
        str(v): {"checksum": str(c), "applied_at": str(a)}
        for v, c, a in cur.fetchall()
    }


def plan(ledger: dict[str, dict[str, object]]) -> tuple[list, list, list]:
    """Split discovered migrations into (pending, drifted, orphaned-ledger-rows)."""
    migrations = discover()
    pending, drifted = [], []
    for version, path in migrations:
        sql = path.read_text(encoding="utf-8")
        record = ledger.get(version)
        if record is None:
            pending.append((version, path, sql))
        elif record["checksum"] != checksum(sql):
            drifted.append((version, path, record["checksum"], checksum(sql)))
    known = {v for v, _ in migrations}
    orphans = sorted(set(ledger) - known)
    return pending, drifted, orphans


def cmd_status(args) -> int:
    dsn = resolve_dsn(args.dsn_env)
    print(f"[migrate] driver={_DRIVER} target={safe_dsn_label(dsn)}")
    with get_db_connection(dsn, statement_timeout_ms=120_000) as conn, conn.cursor() as cur:
        cur.execute(LEDGER_DDL)
        conn.commit()
        ledger = read_ledger(cur)

    pending, drifted, orphans = plan(ledger)
    print(f"\napplied: {len(ledger)}   pending: {len(pending)}   drifted: {len(drifted)}")
    for version, path, _sql in pending:
        print(f"  PENDING  {version}  ({path.name})")
    for version, _path, old, new in drifted:
        print(f"  DRIFT    {version}: ledger={old[:12]} file={new[:12]}")
    for version in orphans:
        print(f"  ORPHAN   {version}: in ledger but no matching file")
    return 0


def cmd_up(args) -> int:
    dsn = resolve_dsn(args.dsn_env)
    print(f"[migrate] driver={_DRIVER} target={safe_dsn_label(dsn)}")
    with get_db_connection(dsn, statement_timeout_ms=120_000) as conn:
        with conn.cursor() as cur:
            cur.execute(LEDGER_DDL)
            conn.commit()
            ledger = read_ledger(cur)

        pending, drifted, orphans = plan(ledger)

        if drifted:
            print("\nERROR: checksum drift detected; refusing to continue.", file=sys.stderr)
            for version, _path, old, new in drifted:
                print(
                    f"  {version}: applied as {old[:12]} but the file now hashes to {new[:12]}",
                    file=sys.stderr,
                )
            print(
                "\nResolve by either reverting the file, or re-recording the migration "
                "deliberately:\n"
                "  UPDATE schema_migrations SET checksum = '<new>' WHERE version = '<v>';",
                file=sys.stderr,
            )
            return 2
        if orphans:
            print(
                f"WARNING: ledger references {len(orphans)} migration(s) with no file: "
                f"{', '.join(orphans)}",
                file=sys.stderr,
            )

        if not pending:
            print("\nnothing to apply; database is at the recorded version.")
            return 0

        if args.dry_run:
            print("\n--dry-run: would apply, in order:")
            for version, path, _sql in pending:
                print(f"  {version}  ({path.name})")
            return 0

        for version, path, sql in pending:
            print(f"\napplying {version} ({path.name})...")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                print(f"ERROR: {version} failed and was rolled back: {exc}", file=sys.stderr)
                return 1
            digest = checksum(sql)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO schema_migrations (version, checksum) "
                    "VALUES (%s, %s) ON CONFLICT (version) DO UPDATE "
                    "SET checksum = EXCLUDED.checksum, applied_at = now()",
                    (version, digest),
                )
            conn.commit()
            print(f"  applied {version} (sha256 {digest[:12]})")

    print(f"\ndone. ledger now at {datetime.now(UTC).isoformat()}")
    return 0


def version_num(version: str) -> int | None:
    """Numeric position of a migration version stem like ``003_gold``.

    Returns the leading digit run as an int, or ``None`` when there is none.
    Only the number is compared, deliberately: adding the stem as a tiebreaker
    makes ``--to 003`` exclude ``003_gold``, because ``"003_gold" > "003"``
    lexically. Two stems sharing a number occupy the same position and are both
    included by an inclusive bound.
    """
    digits = ""
    for ch in version:
        if not ch.isdigit():
            break
        digits += ch
    return int(digits) if digits else None


def select_range(
    migrations: Sequence[tuple[str, pathlib.Path]],
    from_version: str | None = None,
    to_version: str | None = None,
) -> list[tuple[str, pathlib.Path]]:
    """Narrow discovered migrations to an inclusive version range.

    Bounds are ordered by their leading digits, so ``--to 003`` means "every
    migration up to and including 003", not "the 003 prefix". Passing a bound as
    a full stem also works: ``--to 003_gold`` resolves to the same position as
    ``--to 003``.

    This exists because baselining an existing database needs a partial range.
    A database adopted mid-project already holds the schema of every migration
    before the one you actually want to execute; baselining ALL of them would
    mark a not-yet-applied migration as done and it would then be skipped
    forever. Pass ``--to 003`` to adopt 000-003 and leave 004 pending.
    """
    selected: Sequence[tuple[str, pathlib.Path]] = migrations
    if from_version:
        low = version_num(from_version)
        if low is None:
            raise ValueError(f"--from must start with digits, got {from_version!r}")
        selected = [m for m in selected if (n := version_num(m[0])) is not None and n >= low]
    if to_version:
        high = version_num(to_version)
        if high is None:
            raise ValueError(f"--to must start with digits, got {to_version!r}")
        selected = [m for m in selected if (n := version_num(m[0])) is not None and n <= high]
    return list(selected)


def cmd_baseline(args) -> int:
    """Record existing migrations as applied WITHOUT executing them.

    For adopting a database whose schema predates the ledger. Records the
    CURRENT file checksums, so from that point on normal drift detection
    applies. This is the supported way to start tracking a live database --
    never edit the ledger by hand to skip a migration.
    """
    dsn = resolve_dsn(args.dsn_env)
    migrations = select_range(discover(), args.from_version, args.to_version)
    if not migrations:
        print(
            f"no migrations in range "
            f"[{args.from_version or 'start'}..{args.to_version or 'end'}]; "
            "nothing to baseline.",
            file=sys.stderr,
        )
        return 1
    if not args.yes:
        print("baseline records these migrations as APPLIED without executing them:", file=sys.stderr)
        for version, path in migrations:
            print(f"  {version}  ({path.name})", file=sys.stderr)
        print(
            "\nOnly correct for migrations the database ALREADY holds. Anything "
            "left out of this range stays pending and will execute on `up`.",
            file=sys.stderr,
        )
        return 1

    print(f"[migrate] driver={_DRIVER} target={safe_dsn_label(dsn)}")
    with get_db_connection(dsn, statement_timeout_ms=120_000) as conn:
        with conn.cursor() as cur:
            cur.execute(LEDGER_DDL)
            conn.commit()
            for version, path in migrations:
                digest = checksum(path.read_text(encoding="utf-8"))
                cur.execute(
                    "INSERT INTO schema_migrations (version, checksum) "
                    "VALUES (%s, %s) ON CONFLICT (version) DO NOTHING",
                    (version, digest),
                )
                print(f"  baselined {version} (sha256 {digest[:12]})")
        conn.commit()
    print(
        "\nbaselined. Future edits to these files will now report as drift. "
        "Run `status` to see what is still pending."
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Apply database/migrations/*.sql under a checksum ledger."
    )
    sub = ap.add_subparsers(dest="command", required=True)

    def add_common(p) -> None:
        p.add_argument("--dsn-env", default="", help="Env var holding an owner DSN.")

    p_status = sub.add_parser("status", help="Show applied/pending/drifted migrations.")
    add_common(p_status)
    p_status.set_defaults(func=cmd_status)

    p_up = sub.add_parser("up", help="Apply pending migrations in filename order.")
    add_common(p_up)
    p_up.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be applied without touching the database.",
    )
    p_up.set_defaults(func=cmd_up)

    p_base = sub.add_parser(
        "baseline",
        help="Adopt an existing database: record migrations as applied without executing.",
    )
    add_common(p_base)
    p_base.add_argument(
        "--yes",
        action="store_true",
        help="Confirm. Without this flag the command only prints what it would do.",
    )
    p_base.add_argument(
        "--from",
        dest="from_version",
        default="",
        metavar="VER",
        help="Lowest version to baseline (inclusive, filename-stem prefix match).",
    )
    p_base.add_argument(
        "--to",
        dest="to_version",
        default="",
        metavar="VER",
        help="Highest version to baseline (inclusive). Anything above stays PENDING "
             "and will execute on `up` -- this is how you adopt an existing "
             "database and still apply one migration for real, e.g. "
             "`baseline --to 003 --yes` then `up` applies only 004.",
    )
    p_base.set_defaults(func=cmd_baseline)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
