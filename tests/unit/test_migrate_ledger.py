from __future__ import annotations

import pathlib

import pytest

from scripts import migrate


def _write(tmp_path: pathlib.Path, name: str, content: str) -> pathlib.Path:
    """Write a real migration file so checksum()/read_text() behave normally."""
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _ledger(entries: dict[str, str] | None = None) -> dict[str, dict[str, object]]:
    return {
        version: {"checksum": digest, "applied_at": "2026-10-03T00:00:00+00:00"}
        for version, digest in (entries or {}).items()
    }


def test_checksum_is_stable_and_content_sensitive():
    """Same bytes -> same digest; any edit changes it. This is the whole basis
    of drift detection, so it must not be order- or whitespace-insensitive."""
    assert migrate.checksum("SELECT 1;") == migrate.checksum("SELECT 1;")
    assert migrate.checksum("SELECT 1;") != migrate.checksum("SELECT 1")
    assert migrate.checksum("SELECT 1;") != migrate.checksum("SELECT  1;")


def test_discover_orders_by_filename_three_digits_first():
    """Lexicographic order only works because versions are zero-padded; 004
    must sort after 003 and before a hypothetical 010."""
    versions = [v for v, _ in migrate.discover()]
    assert versions == sorted(versions)
    assert versions[0].startswith("000"), "base schema migration must run first"
    assert "004_index_and_integrity_hardening" in versions


def test_plan_classifies_pending_when_unrecorded(tmp_path, monkeypatch):
    path = _write(tmp_path, "000_x.sql", "CREATE TABLE t();")
    monkeypatch.setattr(migrate, "discover", lambda: [("000_x", path)])
    pending, drifted, orphans = migrate.plan(_ledger())
    assert [v for v, _, _ in pending] == ["000_x"]
    assert drifted == []
    assert orphans == []


def test_plan_reports_drift_when_file_changed_after_apply(tmp_path, monkeypatch):
    """The behaviour `IF NOT EXISTS` could not provide: a post-apply edit is
    surfaced, not silently treated as already-done."""
    path = _write(tmp_path, "000_x.sql", "CREATE TABLE t();")
    monkeypatch.setattr(migrate, "discover", lambda: [("000_x", path)])
    ledger = _ledger({"000_x": "deadbeef" * 8})
    pending, drifted, _orphans = migrate.plan(ledger)
    assert pending == []
    assert len(drifted) == 1
    version, _, old, new = drifted[0]
    assert version == "000_x"
    assert old == "deadbeef" * 8
    assert new == migrate.checksum("CREATE TABLE t();")


def test_plan_keeps_matching_migration_silent(tmp_path, monkeypatch):
    path = _write(tmp_path, "000_x.sql", "CREATE TABLE t();")
    monkeypatch.setattr(migrate, "discover", lambda: [("000_x", path)])
    ledger = _ledger({"000_x": migrate.checksum("CREATE TABLE t();")})
    pending, drifted, _ = migrate.plan(ledger)
    assert pending == []
    assert drifted == []


def test_plan_flags_ledger_rows_with_no_file(tmp_path, monkeypatch):
    """A migration deleted from disk but still in the ledger is an orphan: the
    database claims a version nobody can reproduce."""
    path = _write(tmp_path, "000_x.sql", "SELECT 1;")
    monkeypatch.setattr(migrate, "discover", lambda: [("000_x", path)])
    ledger = _ledger({"000_x": "aa" * 32, "099_deleted": "bb" * 32})
    _pending, _drifted, orphans = migrate.plan(ledger)
    assert orphans == ["099_deleted"]


def test_plan_handles_multi_class_mix(tmp_path, monkeypatch):
    monkeypatch.setattr(
        migrate,
        "discover",
        lambda: [
            ("000_a", _write(tmp_path, "000_a.sql", "SELECT 1;")),
            ("001_b", _write(tmp_path, "001_b.sql", "SELECT 2;")),
            ("002_c", _write(tmp_path, "002_c.sql", "SELECT 3;")),
        ],
    )
    ledger = _ledger(
        {
            "001_b": migrate.checksum("SELECT 2;"),
            "002_c": "cc" * 32,
        }
    )
    pending, drifted, orphans = migrate.plan(ledger)
    assert [v for v, _, _ in pending] == ["000_a"]
    assert [d[0] for d in drifted] == ["002_c"]
    assert orphans == []


def test_resolve_dsn_prefers_explicit_then_owner(monkeypatch):
    monkeypatch.delenv("DB_URL", raising=False)
    monkeypatch.setenv("DB_URL_OWNER", "postgresql://owner@host/db")
    assert migrate.resolve_dsn(None) == "postgresql://owner@host/db"
    assert migrate.resolve_dsn("postgresql://explicit@host/db") == "postgresql://explicit@host/db"


def test_resolve_dsn_falls_back_to_db_url(monkeypatch):
    monkeypatch.delenv("DB_URL_OWNER", raising=False)
    monkeypatch.setenv("DB_URL", "postgresql://app@host/db")
    assert migrate.resolve_dsn(None) == "postgresql://app@host/db"


def test_resolve_dsn_strips_wrapping_quotes(monkeypatch):
    monkeypatch.delenv("DB_URL_OWNER", raising=False)
    monkeypatch.setenv("DB_URL", "'postgresql://app@host/db'")
    assert migrate.resolve_dsn(None) == "postgresql://app@host/db"


def test_resolve_dsn_exits_when_nothing_configured(monkeypatch):
    monkeypatch.delenv("DB_URL_OWNER", raising=False)
    monkeypatch.delenv("DB_URL", raising=False)
    with pytest.raises(SystemExit):
        migrate.resolve_dsn(None)


# --- baseline version-range selection ---------------------------------------
#
# The bug this exists to prevent: baselining an existing database recorded ALL
# migrations as applied, including ones the database had never received, so a
# subsequent `up` silently skipped them forever.

_ALL = [
    ("000_silver", pathlib.Path("000_silver.sql")),
    ("001_vector", pathlib.Path("001_vector.sql")),
    ("002_edges", pathlib.Path("002_edges.sql")),
    ("003_gold", pathlib.Path("003_gold.sql")),
    ("004_index", pathlib.Path("004_index.sql")),
]


def _stems(selected) -> list[str]:
    return [v for v, _ in selected]


def test_select_range_without_bounds_returns_everything():
    assert _stems(migrate.select_range(_ALL)) == _stems(_ALL)


def test_select_range_to_upper_bound_excludes_newer_migrations():
    """The adoption case: adopt 000-003 and leave 004 pending for `up`."""
    selected = migrate.select_range(_ALL, to_version="003")
    assert _stems(selected) == ["000_silver", "001_vector", "002_edges", "003_gold"]
    assert "004_index" not in _stems(selected)


def test_select_range_from_lower_bound_excludes_older_migrations():
    selected = migrate.select_range(_ALL, from_version="002")
    assert _stems(selected) == ["002_edges", "003_gold", "004_index"]


def test_select_range_both_bounds_is_inclusive():
    selected = migrate.select_range(_ALL, from_version="001", to_version="002")
    assert _stems(selected) == ["001_vector", "002_edges"]


def test_select_range_matches_full_stem_not_just_the_digits():
    """`--to 003` and `--to 003_gold` must select the same migration."""
    short = migrate.select_range(_ALL, to_version="003")
    full = migrate.select_range(_ALL, to_version="003_gold")
    assert _stems(short) == _stems(full)


def test_select_range_bounds_are_inclusive_at_both_ends():
    single = migrate.select_range(_ALL, from_version="002", to_version="002")
    assert _stems(single) == ["002_edges"]


def test_select_range_can_return_nothing_without_raising():
    assert migrate.select_range(_ALL, from_version="010") == []

def test_version_num_parses_leading_digits():
    assert migrate.version_num("004_index_and_integrity_hardening") == 4
    assert migrate.version_num("000") == 0
    assert migrate.version_num("010") == 10


def test_version_num_returns_none_without_leading_digits():
    """A bound with no numeric prefix has no position in the ordering, so the
    range helpers reject it rather than silently matching nothing."""
    assert migrate.version_num("silver") is None
    assert migrate.version_num("") is None


@pytest.mark.parametrize("bound", ["silver", "v3", "Gold"])
def test_select_range_rejects_non_numeric_bounds(bound):
    """Silently selecting nothing would look like success, which is exactly the
    failure mode this tool exists to prevent. An EMPTY bound is exempt: it is the
    argparse default and legitimately means "no bound"."""
    with pytest.raises(ValueError):
        migrate.select_range(_ALL, to_version=bound)
    with pytest.raises(ValueError):
        migrate.select_range(_ALL, from_version=bound)


def test_empty_bound_means_unset_not_an_error():
    assert _stems(migrate.select_range(_ALL, to_version="", from_version="")) == _stems(_ALL)


def test_real_migration_stems_sort_and_range_correctly():
    """Guard against the tests' abbreviated stems hiding a real-world failure:
    actual discovery returns long names like 004_index_and_integrity_hardening."""
    real = [
        (v, pathlib.Path(f"database/migrations/{v}.sql"))
        for v, _ in migrate.discover()
    ]
    assert [v for v, _ in migrate.select_range(real, to_version="003")] == [
        "000_silver_schema",
        "001_vector_and_chunks_schema",
        "002_collaboration_edges_schema",
        "003_gold_analytics_schema",
    ]
    assert [v for v, _ in migrate.select_range(real, to_version="003_gold")] == [
        "000_silver_schema",
        "001_vector_and_chunks_schema",
        "002_collaboration_edges_schema",
        "003_gold_analytics_schema",
    ]
