"""Unit tests for the read-only grant script's exposure audit.

Docs Reference: docs/08 Security.md 1.1.

The subtle behaviour pinned here is that a table GRANT is not the same as
reachability. On this deployment Supabase grants `anon` full privileges on every
public table, yet RLS is enabled with zero policies, so PostgreSQL denies every
statement anyway. The audit must classify that as masked (latent), not as an
exposure -- reporting it as an exposure would cry wolf, and reporting it as
clean would hide the latent risk.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from scripts import grant_readonly


class _FakeCursor:
    """Minimal cursor stub returning canned rows for the audit query."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return self._rows


def test_no_privileges_reports_clean():
    cur = _FakeCursor([("publications", True, False, False)])
    reachable, masked = grant_readonly.audit_api_role_exposure(cur)
    assert reachable == []
    assert masked == []


def test_rls_on_with_no_policies_is_masked_not_exposed():
    """The live Supabase case: wide grants, RLS ON. Must be reported as latent,
    because RLS is what denies it -- not the grants."""
    cur = _FakeCursor([("publications", True, True, True)])
    reachable, masked = grant_readonly.audit_api_role_exposure(cur)
    assert reachable == [], "RLS-enabled table must not be reported as an exposure"
    assert len(masked) == 1
    assert masked[0]["table"] == "publications"
    assert masked[0]["rls"] is True
    assert masked[0]["anon_select"] is True
    assert masked[0]["anon_write"] is True


def test_rls_off_with_privileges_is_a_real_exposure():
    """RLS OFF means the grants are actually honoured: that IS an exposure."""
    cur = _FakeCursor([("chunks", False, True, True)])
    reachable, masked = grant_readonly.audit_api_role_exposure(cur)
    assert len(reachable) == 1
    assert masked == []
    assert reachable[0]["table"] == "chunks"
    assert reachable[0]["rls"] is False


def test_read_only_privilege_is_tracked_separately_from_write():
    cur = _FakeCursor([("topics", True, True, False)])
    _reachable, masked = grant_readonly.audit_api_role_exposure(cur)
    assert masked[0]["anon_select"] is True
    assert masked[0]["anon_write"] is False


def test_mixed_tables_split_into_reachable_and_masked():
    cur = _FakeCursor(
        [
            ("chunks", True, True, True),      # RLS on,  privileged -> masked
            ("keywords", False, True, False),  # RLS off, SELECT       -> reachable
            ("authors", False, False, True),   # RLS off, INSERT       -> reachable
            ("topics", True, True, False),     # RLS on,  SELECT       -> masked
            ("funding", True, False, False),   # no privilege at all   -> excluded
        ]
    )
    reachable, masked = grant_readonly.audit_api_role_exposure(cur)
    assert {e["table"] for e in reachable} == {"keywords", "authors"}
    assert {e["table"] for e in masked} == {"chunks", "topics"}


def test_report_exposure_is_silent_when_nothing_is_granted(capsys):
    cur = _FakeCursor([("publications", True, False, False)])
    assert grant_readonly._report_exposure(cur) == []
    out = capsys.readouterr()
    assert "no Supabase API role holds any privilege" in out.out


def test_report_exposure_names_every_reachable_table(capsys):
    cur = _FakeCursor(
        [
            ("chunks", False, True, True),
            ("keywords", False, True, False),
        ]
    )
    exposed = grant_readonly._report_exposure(cur)
    assert {e["table"] for e in exposed} == {"chunks", "keywords"}
    err = capsys.readouterr().err
    assert "EXPOSURE" in err
    assert "chunks" in err and "keywords" in err


def test_report_exposure_distinguishes_latent_from_active(capsys):
    cur = _FakeCursor(
        [
            ("chunks", True, True, True),      # RLS on  -> latent
            ("keywords", False, True, False),  # RLS off -> active
        ]
    )
    grant_readonly._report_exposure(cur)
    err = capsys.readouterr().err
    assert "Latent, not active" in err
    assert "EXPOSURE" in err


def test_supabase_api_roles_are_the_documented_pair():
    """Widening this tuple changes the project's access model; pin it."""
    assert grant_readonly.SUPABASE_API_ROLES == ("anon", "authenticated")