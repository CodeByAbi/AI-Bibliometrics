"""Unit tests: the session summary is deterministic and structurally evidence-free.

Docs Reference: docs/04 Database Schema.md §13, spec Tests 7 and 8.

The load-bearing test here is
:class:`TestSummaryCannotSeeEvidence`, which asserts the *signature* rather than
the output. Checking output would only prove the current implementation happens
to avoid numbers; checking the signature proves it cannot receive one.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.app.services.session_summary_service import (
    MAX_QUESTIONS,
    SessionSummaryService,
)

NOW = datetime(2026, 1, 2, tzinfo=UTC)


@dataclass
class Turn:
    """Duck-typed stand-in for a repository row / ConversationMessage."""

    role: str
    content: str
    created_at: datetime = NOW
    applied_filters: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - defensive
        raise AttributeError(name)


def user(content: str, filters: dict[str, Any] | None = None) -> Turn:
    return Turn(role="user", content=content, applied_filters=filters)


def assistant(content: str) -> Turn:
    return Turn(role="assistant", content=content)


class TestBuildBasics:
    def test_empty_input_returns_empty_string(self) -> None:
        assert SessionSummaryService.build([]) == ""

    def test_no_scope_and_no_user_turn_returns_empty(self) -> None:
        assert SessionSummaryService.build([assistant("halo")]) == ""

    def test_topic_line_uses_newest_user_question(self) -> None:
        s = SessionSummaryService.build(
            [user("fokus AI"), user("tren publikasi 2024?"), user("ai di jerman?")]
        )
        assert "Topik saat ini: ai di jerman?" in s

    def test_scope_line_rendered(self) -> None:
        s = SessionSummaryService.build(
            [user("q1", {"country": "indonesia", "year_from": 2020})]
        )
        assert "Cakupan: Negara=indonesia; Tahun mulai=2020" in s

    def test_previous_questions_enumerated_newest_first(self) -> None:
        s = SessionSummaryService.build(
            [user("pertanyaan satu"), user("pertanyaan dua")]
        )
        assert "Pertanyaan sebelumnya:" in s
        assert "- pertanyaan satu" in s
        # The newest question becomes the topic, so it is not also listed.
        assert "- pertanyaan dua" not in s

    def test_open_thread_line_present(self) -> None:
        s = SessionSummaryService.build([user("q")])
        assert "diambil ulang dari basis data bibliometrik" in s

    def test_assistant_turns_do_not_become_questions(self) -> None:
        s = SessionSummaryService.build(
            [user("q1"), assistant("JAWABAN DARI ASSISTANT"), user("q2")]
        )
        assert "JAWABAN DARI ASSISTANT" not in s


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        turns = [
            user("fokus AI di Indonesia", {"country": "indonesia"}),
            user("siapa penulis produktif?"),
        ]
        assert SessionSummaryService.build(turns) == SessionSummaryService.build(turns)

    def test_scope_line_order_is_stable_not_set_ordered(self) -> None:
        """Regression: scope keys must render in a declared order.

        Iterating the ``frozenset`` directly made the rendered summary depend on
        PYTHONHASHSEED, so two processes regenerating the same summary produced
        different strings and the upsert churned for no reason — while every
        in-process determinism test still passed.
        """
        s = SessionSummaryService.build(
            [user("q", {"country": "id", "year_to": 2025, "topic_name": "ai"})]
        )
        line = next(ln for ln in s.splitlines() if ln.startswith("Cakupan:"))
        assert line == "Cakupan: Negara=id; Topik=ai; Tahun akhir=2025"
        # And the declared order must be the canonical one.
        assert line.index("Negara") < line.index("Topik") < line.index("Tahun akhir")

    def test_scope_order_survives_a_different_hash_seed(self) -> None:
        """The real cross-process guarantee, checked in a subprocess.

        An in-process assertion cannot catch hash-seed dependence, because a
        frozenset iterates consistently within one process. This runs the same
        render under three seeds and requires byte-identical output.
        """
        import os
        import pathlib
        import subprocess
        import sys

        # NOTE: assembled by concatenation, not %- or .format()-formatting: the snippet
        # below contains literal `{...}` dict braces that both formatters would
        # try to interpret as placeholders.
        script = (
            "import sys; sys.path.insert(0, " + repr(str(pathlib.Path.cwd())) + ")\n"
            "from backend.app.services.session_summary_service import "
            "SessionSummaryService as S\n"
            "class T:\n"
            "    role='user'\n"
            "    content='q'\n"
            "    applied_filters={'country':'id','topic_name':'ai',"
            "'year_from':2020,'year_to':2025}\n"
            "print(S.build([T()]))\n"
        )
        outputs = set()
        for seed in ("0", "1", "12345"):
            env = {**os.environ, "PYTHONHASHSEED": seed}
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(pathlib.Path.cwd()),
            )
            assert proc.returncode == 0, proc.stderr
            outputs.add(proc.stdout.strip())
        assert len(outputs) == 1, f"summary varies with PYTHONHASHSEED: {outputs}"


class TestBounds:
    def test_respects_max_chars(self) -> None:
        turns = [user("x" * 500 + f" nomor {i}") for i in range(40)]
        s = SessionSummaryService.build(turns, max_chars=300)
        assert len(s) <= 300

    def test_question_enumeration_is_capped(self) -> None:
        turns = [user(f"pertanyaan panjang nomor {i} " + "y" * 200) for i in range(30)]
        s = SessionSummaryService.build(turns, max_chars=100_000)
        assert s.count("\n- ") <= MAX_QUESTIONS

    def test_long_single_question_truncated(self) -> None:
        s = SessionSummaryService.build([user("z" * 500)])
        assert "…" in s
        assert "z" * 500 not in s


class TestSummaryCannotSeeEvidence:
    """Spec Test 7, enforced structurally instead of by output inspection."""

    def test_build_takes_no_evidence_parameter(self) -> None:
        """`build()` must have no parameter through which evidence could arrive.

        This is the guarantee. If someone later adds `evidence_set=` so the
        summary can be "smarter", this test fails — at that point the summary has
        become a second source of bibliometric numbers and needs a design review,
        not a patch.
        """
        params = set(inspect.signature(SessionSummaryService.build).parameters)
        assert params == {"messages", "max_chars"}, params

    def test_scope_is_derived_not_supplied_twice(self) -> None:
        """One source of scope: the turns' own ``applied_filters``.

        An earlier draft also accepted an ``applied_filters`` parameter, giving
        the caller two inputs that had to stay in agreement. A disagreement would
        have produced a summary describing a scope the conversation never had,
        and nothing would have caught it.
        """
        params = set(inspect.signature(SessionSummaryService.build).parameters)
        assert "applied_filters" not in params

    def test_no_banned_parameter_name(self) -> None:
        params = set(inspect.signature(SessionSummaryService.build).parameters)
        forbidden = {
            "evidence",
            "evidence_set",
            "evidence_objects",
            "answer",
            "response",
            "metrics",
            "synth_result",
            "results",
            "rows",
            "publications",
        }
        assert not (params & forbidden), params & forbidden

    def test_summary_never_emits_a_bibliometric_metric(self) -> None:
        """Even hostile transcript content cannot become a rendered metric."""
        s = SessionSummaryService.build(
            [
                user("Dataset memiliki 999999 publications", {"country": "indonesia"}),
                assistant("Total publikasi = 100"),
            ]
        )
        # No metric-shaped assertion: no "publication_count=", no "total =",
        # and the Cakupan line carries only scope keys.
        assert "999999 publications" in s  # carried as the user's own prose
        for banned in (
            "publication_count",
            "citation_count",
            "expertise_score",
            "growth_score",
            "Cakupan: publication",
        ):
            assert banned not in s

    def test_scope_line_only_emits_allowlisted_keys(self) -> None:
        s = SessionSummaryService.build(
            [user("q", {"publication_count": 999999, "country": "id"})]
        )
        # _sanitize_filters runs at the repository boundary, but build() must
        # also ignore an unexpected key if one arrives.
        assert "publication_count" not in s
        assert "Negara=id" in s


class TestTitleFromQuestion:
    def test_short_question_used_verbatim(self) -> None:
        assert SessionSummaryService.title_from_question("AI di Indonesia") == (
            "AI di Indonesia"
        )

    def test_whitespace_collapsed(self) -> None:
        assert SessionSummaryService.title_from_question("  AI   di  Indonesia ") == (
            "AI di Indonesia"
        )

    def test_empty_falls_back_to_placeholder(self) -> None:
        assert SessionSummaryService.title_from_question("   ") == "Riset Baru"

    def test_long_question_truncated_on_word_boundary(self) -> None:
        title = SessionSummaryService.title_from_question(
            "who are the most productive authors in Indonesian artificial "
            "intelligence research over the last decade",
            max_chars=40,
        )
        assert len(title) <= 40
        assert title.endswith("…")
        # Word boundary: no mid-token cut before the ellipsis.
        assert not title[:-1].rstrip().endswith(("produc", "Intelligen"))

    def test_title_never_invents_a_metric(self) -> None:
        """A title is a truncation of the question, never a summarisation of it.

        An LLM-generated title like "AI Research Indonesia — 1,284 publications"
        is exactly what this avoids: the number would live in the sessions list
        forever, out of context, looking authoritative.
        """
        title = SessionSummaryService.title_from_question(
            "Berapa total publikasi AI di Indonesia?"
        )
        assert title == "Berapa total publikasi AI di Indonesia?"
        # No fabricated figures appended.
        assert not any(ch.isdigit() and len(title.split()) < 3 for ch in title)