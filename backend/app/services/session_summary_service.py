"""SessionSummaryService — deterministic conversation memory.

Docs Reference: docs/04 Database Schema.md §13, docs/05 §6 (prompt framing).

WHAT THIS IS FOR
================
The summary exists so a long session does not have to send its whole transcript
to the LLM. It records what the conversation is *about* — scope, questions
asked, entities named, open threads — so a follow-up question can be
interpreted without replaying every turn.

WHAT IT MUST NEVER BE
=====================
Bibliometric memory. No publication counts, citation counts, expertise scores,
growth scores or any other metric belongs in here. Those live in `public` and
are re-retrieved on demand; a cached copy in the summary is a number that goes
stale the next time the corpus is re-ingested, with no way for a reader to tell
which of the two numbers is authoritative.

HOW THAT IS GUARANTEED, RATHER THAN MERELY INTENDED
==================================================
``build()`` takes exactly two inputs: stored turns and their resolved filter
scope. There is no parameter through which an ``EvidenceObject``, an
``EvidenceSet``, a synthesis result or an answer string could arrive. The
service is structurally incapable of quoting a metric, so it does not need a
list of banned phrases, a regex filter, or a prompt instruction telling the
model not to — there is no model here either.

That also means Test 7 (a summary seeded with "Dataset memiliki 999999
publications", then "How many publications?") cannot be defeated by this file.
The number is in the transcript text and gets carried as prose if it appears
inside a user's own question; it never becomes a value the retrieval layer
consults. The answer still comes from ``publications``.

Deliberately no LLM call. A model-written summary would add a second model call
to the request path, a second timeout/fallback branch, and — most importantly —
the exact failure mode above: the model deciding to write "1,238 publications"
into the summary and thereby creating a second, unauditable source of numbers.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from backend.app.models.session import merge_applied_filters, render_scope_parts

#: Maximum characters of a single past question kept in the summary. Long
#: questions are truncated because the summary's job is to jog scope recall, not
#: to archive the transcript — the full text is already in research_messages.
MAX_QUESTION_CHARS = 160

#: How many past questions the summary enumerates. Beyond this, older questions
#: stop changing what the next turn means: a user is not still referring back to
#: turn 3 of 40, they are answering turn 39.
MAX_QUESTIONS = 6

#: Scope keys rendered as "label=value" lines, in a fixed order so the output is
#: byte-stable for the same input (determinism is what makes this testable and
#: keeps the summary from churn-diffing on every regeneration). Order comes from
#: ``SCOPE_KEY_ORDER`` and labels from ``SCOPE_LABELS``, both in
#: ``models/session.py``; this service previously kept its own copy of the label
#: map, which drifted from the one used for the prompt block.


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _field(turn: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a turn that may be a dict row or a model.

    Required, not a nicety. ``refresh_summary`` passes rows straight from
    ``SessionRepository.list_recent_messages``, which returns **dicts**.
    ``getattr(dict_row, "role")`` returns ``None``, so a ``getattr``-based
    filter silently discarded every real row and ``build`` returned ``""`` —
    which the caller reads as "nothing to say" and skips the upsert. The summary
    was therefore never written in production, while every unit test passed
    because they handed in ``ConversationMessage`` models instead of dicts.

    Kept as one helper so the next field read cannot reintroduce the split.
    """
    if isinstance(turn, dict):
        return turn.get(name, default)
    return getattr(turn, name, default)


class SessionSummaryService:
    """Deterministic summary renderer. No I/O, no model, no evidence input."""

    @staticmethod
    def build(
        messages: Iterable[Any],
        *,
        max_chars: int = 1200,
    ) -> str:
        """Render the rolling conversation summary.

        Args:
            messages: Stored turns in chronological order. Duck-typed on
                ``role`` / ``content`` / ``applied_filters`` so both repository
                row dicts and ``ConversationMessage`` models work. Each turn's
                own ``applied_filters`` is read to recover the scope that turn
                actually used, most-recent-value-wins.
            max_chars: Hard cap; the result is truncated from the tail, which
                drops the oldest enumerated questions rather than cutting a
                question mid-word at the top of the list.

        Scope is derived from the turns rather than accepted as a second
        argument, so there is exactly one source of it. An earlier draft took
        ``applied_filters`` as a parameter alongside per-turn values; that
        created two inputs the caller had to keep in agreement, and a
        disagreement would have silently produced a summary describing a scope
        the conversation never had.

        Returns:
            A conversation-oriented summary. Empty string when there is nothing
            to say (no turns and no scope) — the caller then skips the upsert
            rather than storing a blank row, which the CHECK constraint would
            reject anyway.
        """
        rows = [
            m
            for m in messages
            if _field(m, "role") in ("user", "assistant")
        ]
        applied_filters = merge_applied_filters(
            [_field(m, "applied_filters") or {} for m in rows]
        )

        user_turns = [m for m in rows if _field(m, "role") == "user"]
        if not user_turns and not applied_filters:
            return ""

        lines: list[str] = []

        # 1. Topic line — the newest user question is the closest thing the
        #    conversation has to a current statement of intent.
        if user_turns:
            latest = _truncate(
                _field(user_turns[-1], "content", "") or "", MAX_QUESTION_CHARS
            )
            if latest:
                lines.append(f"Topik saat ini: {latest}")

        # 2. Scope line — what the conversation is scoped to.
        scope_parts = render_scope_parts(applied_filters or {})
        if scope_parts:
            lines.append("Cakupan: " + "; ".join(scope_parts))

        # 3. Prior questions, newest first, so truncation drops the oldest.
        questions = [
            _truncate(_field(m, "content", "") or "", MAX_QUESTION_CHARS)
            for m in reversed(user_turns[:-1] if len(user_turns) > 1 else [])
        ]
        questions = [q for q in questions if q][:MAX_QUESTIONS]
        if questions:
            lines.append("Pertanyaan sebelumnya:")
            lines.extend(f"- {q}" for q in questions)

        # 4. Open thread, stated as an expectation rather than a fact. This is
        #    the line that tells the next turn what the user is likely to ask,
        #    and it is phrased as a question so it can never be quoted as an
        #    assertion by a downstream reader.
        if lines:
            lines.append("Percakapan masih terbuka; jawaban berikutnya harus "
                         "diambil ulang dari basis data bibliometrik, bukan dari "
                         "ringkasan ini.")

        summary = "\n".join(lines).strip()
        if len(summary) > max_chars:
            summary = summary[:max_chars].rstrip()
        return summary

    @staticmethod
    def title_from_question(
        question: str,
        *,
        max_chars: int = 80,
    ) -> str:
        """Derive a session title from the first user question.

        Deliberately a truncation of the question, NOT a summarisation of it.
        An LLM-generated title ("AI Research Indonesia — 1,284 publications") is
        exactly the failure this feature exists to prevent: the number would sit
        in the sessions list forever, out of context, looking authoritative and
        never being revalidated. A trimmed question is always defensible because
        it is literally what the user asked.
        """
        cleaned = " ".join((question or "").split())
        if not cleaned:
            return "Riset Baru"
        if len(cleaned) <= max_chars:
            return cleaned
        # Cut on a word boundary so the title does not end mid-token.
        clipped = cleaned[: max_chars - 1]
        if " " in clipped:
            clipped = clipped[: clipped.rfind(" ")]
        return clipped.rstrip() + "…"