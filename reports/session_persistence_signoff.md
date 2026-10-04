# Session Persistence — Sign-off Evidence

**Scope**: Session persistence (application domain) separated from the bibliometric
canonical source of truth. Implements the Session Isolation Invariant
(docs/03 §0.3 #5) and AC-SESSION-1 … AC-SESSION-16.

**Branch**: `feat/session-persistence` (from `develop`)
**Migration**: `database/migrations/005_session_persistence_schema.sql`

---

## 1. Status: CODE COMPLETE, FEATURE DISABLED PENDING OWNER ACTION

The implementation is complete and verified. **The feature is not yet active**,
because it requires a database credential only the owner can create.

| Step | Command | State |
|---|---|---|
| 1 | `python scripts/migrate.py up` | **PENDING** — owner action (DDL) |
| 2 | `python scripts/grant_session_role.py` | **PENDING** — owner action (ACL) |
| 3 | `ALTER ROLE app_session LOGIN PASSWORD '...'` | **PENDING** — owner action (credential) |
| 4 | set `DB_URL_SESSION` in `.env` | **PENDING** — owner action |

Until step 4, the documented and tested behaviour is:

- `GET/POST/DELETE /api/v1/sessions` → `503 session_store_unavailable`
- `POST /api/v1/ask` **with** `session_id` → `503 session_store_unavailable`
- `POST /api/v1/ask` **without** `session_id` → `200`, fully stateless, unchanged

---

## 2. What was verified, and what was NOT

The distinction matters, so it is stated plainly.

### Verified (executed in this session)

| Check | Result |
|---|---|
| Migration 005 executes on the live server | **PASS** — 3 tables, 3 PKs, 3 indexes created |
| `app` → `public` foreign keys after migration | **0** (invariant holds) |
| `ON DELETE` action on both session FKs | `c` = CASCADE, both target `research_sessions` only |
| Bibliometric metric columns in `app` | **0** |
| Rollback left no residue | **PASS** — schema `app` absent afterwards |
| `scripts/migrate.py status` | `applied: 5 pending: 1 drifted: 0` — 005 detected, no drift |
| `scripts/migrate.py up --dry-run` | Plans to apply 005 only |
| `scripts/grant_session_role.py --check-only` | Reports schema absent; exits 0 |
| Full pytest suite | **567 passed, 41 skipped, 0 failed** |
| New session unit tests | **98 passed** (80 + 18 transaction regression) |
| Session integration tests | **39 skipped** (see §3) |
| ruff, session files | **All checks passed** |
| ruff, repo total | 1240 → 1249 (+9, all `Optional[...]`/`Dict` style in `models/ask.py`; 4 pre-existing findings fixed along the way) |
| mypy, session modules | **0 errors** |
| mypy, `verify_schema.py` | 11 at HEAD → **11 now** (0 introduced) |

The migration was validated by executing the real DDL inside a transaction that
was then rolled back, so syntax, constraint names, FK targets, index definitions
and CHECK expressions are all confirmed without persisting anything.

**The transaction boundary is now covered by a database-free regression test**
(`tests/unit/test_session_transaction.py`), verified to fail against the pre-fix
shape. That closes the one defect in §5.1 which no other test could see.

### NOT verified (blocked on owner action)

The 39 session integration tests have **never executed**. They skip, loudly and
with an actionable message. Therefore the following remain **unproven at
runtime**, even though the code paths are unit-tested and reviewed:

- AC-SESSION-1/2/3/4 — session CRUD against a live session store
- AC-SESSION-5/6/8/10 — `session_id` round-trip, scope inheritance, stale-answer
  re-query, concurrent turns
- AC-SESSION-7/9/11/12/13/14/15/16 — isolation and grounding, against a live corpus

**This is the accepted bootstrap state, not a pass.** See §3.

---

## 3. Why the integration tests SKIP rather than PASS

Per the explicit decision that a skipped session test must never be mistaken for
a verified one:

```
DB_URL_SESSION absent
        ↓
session_integration tests
        ↓
SKIP with explicit reason (visible under `pytest -rs`)
        ↓
overall result reports the session suite UNEXECUTED
```

The skip message names all four remediation steps. The group carries the marker
`session_integration`, so a session-enabled CI job can require that it actually
ran rather than merely not failing:

```bash
pytest -m session_integration -v      # must show PASSED, not SKIPPED
```

Observed skip output:

```
SKIPPED [1] tests\integration\test_sessions_endpoint.py:291: DB_URL_SESSION is not
configured. Session persistence cannot run, so this acceptance test is UNVERIFIED,
not passing. Owner setup: (1) python scripts/migrate.py up
(2) python scripts/grant_session_role.py (3) add DB_URL_SESSION for role
app_session to .env
```

---

## 4. Test coverage against the spec's 12 acceptance tests

| Spec test | Test | Where |
|---|---|---|
| 1 Create session → 201 + UUID | `TestCreateSession` | `test_sessions_endpoint.py` |
| 2 Ask persists both turns | `TestAskPersistsTurns` | `test_sessions_endpoint.py` |
| 3 History chronological | `TestSessionHistory` | `test_sessions_endpoint.py` |
| 4 Recent sessions sorted | `TestRecentSessions` | `test_sessions_endpoint.py` |
| 5 Delete cascades only app tables | `test_delete_cascades_messages_and_summary_only` | `test_session_isolation.py` |
| 6 Corpus counts unchanged | `test_corpus_counts_unchanged_across_full_lifecycle` | `test_session_isolation.py` |
| 7 Summary is not evidence | `TestSummaryIsNotEvidence` | `test_session_isolation.py` |
| 8 Prior answer not authoritative | `TestPreviousAnswerIsNotAuthoritative` | `test_session_isolation.py` |
| 9 Deletion isolation | same as 5, plus `test_delete_does_not_touch_other_sessions` | `test_session_isolation.py` |
| 10 Concurrent messages | `TestConcurrency` | `test_session_isolation.py` |
| 11 Invalid session → 404, no biblio query | `TestInvalidSessionNeverTouchesRetrieval` | `test_session_isolation.py` |
| 12 Retrieval failure persistence | `TestRetrievalFailure` | `test_session_isolation.py` |

Test 11 is asserted in its strongest form: `get_pool` and `SqlRetriever.retrieve`
are patched to raise if called, and the 404 must still be returned — so "no
bibliometric query happened" is proven by construction, not inferred.

Tests 7 and 8 plant a hostile summary (`"Dataset memiliki 999999 publications"`)
and a hostile prior assistant answer (`"Total publikasi = 100"`), then assert the
answer equals the value measured statelessly from the corpus.

---

## 5. Defects found and fixed during implementation

Recorded because they are the kind that survive review and fail in production.

1. **The session transaction was decorative — zero statements ran inside it.**
   Found by code review *after* the implementation was reported complete, and it
   is the most serious defect in this change.

   `SessionRepository` held the `Pool`, so every method called
   `pool.fetchrow(...)`, which acquires its own connection. Meanwhile
   `SessionService` opened `conn.transaction()` on a *different* connection:

   ```
   acquire -> conn#1
   txn:BEGIN on conn#1
   fetchrow -> pool.fetchrow-internal (NO ACTIVE TXN)
   fetchval  -> pool.fetchval-internal (NO ACTIVE TXN)
   txn:COMMIT on conn#1

   statements executed INSIDE the transaction : 0
   statements executed OUTSIDE the transaction: 2
   ```

   The transaction committed an empty scope while appearing to cover the writes.
   A crash between the INSERT and the UPDATE left a persisted turn with a stale
   `last_message_at`. Every other test passed, because from the caller's point of
   view a transaction *was* opened.

   **Fixed** by making `SessionRepository` connection-bound (`__init__(conn)`)
   and moving the pool into `SessionService`, so the service picks the
   connection, opens the transaction on it, and hands that same connection to the
   repository. Dispatch is now unconditional — no per-method pool-vs-conn branch.
   The two near-identical `record_*_message` methods collapsed into one
   `_persist_turn`, which is now the only place a transaction is opened.

   **Regression test:** `tests/unit/test_session_transaction.py` (18 tests, no
   database required). It asserts on *dispatch*, not outcomes, because dispatch
   is what was wrong. Verified to fail against the pre-fix shape: **8 of 18 fail**,
   including both behavioural tests.

   The first version of that test was itself defective: it asserted only that
   *no* statement ran outside the transaction, which passes vacuously when
   nothing runs at all — and nothing ran, because the service swallowed the
   resulting `AttributeError`. Fixed by also asserting the expected statement
   count. A negative-only assertion in a best-effort code path is not a test.

2. **Hash-seed-dependent "deterministic" output.** `SessionSummaryService` iterated
   `SCOPE_INHERITABLE_KEYS`, a `frozenset`, to render the scope line. Frozenset
   iteration order for strings depends on `PYTHONHASHSEED`, so two processes
   regenerating the same summary produced *different strings* — defeating the
   documented determinism and churning the upsert. Every in-process determinism
   test passed, because a frozenset iterates consistently within one process.
   Fixed by introducing `SCOPE_KEY_ORDER` (a tuple) and iterating that.
   Pinned by `test_scope_order_survives_a_different_hash_seed`, which renders
   under three seeds in a subprocess and requires byte-identical output.

3. **Duplicated scope source of truth.** `SessionSummaryService.build()` originally
   accepted `applied_filters` as a parameter *and* read per-turn
   `applied_filters`. Two inputs the caller had to keep in agreement, with a silent
   wrong-scope failure mode. Fixed by removing the parameter and deriving scope from
   the turns, which are the single source. Pinned by
   `test_scope_is_derived_not_supplied_twice`.

4. **`--check-only` crashed in the pre-migration state.** `grant_session_role.py`
   called `has_schema_privilege(..., 'app', ...)` before checking whether schema
   `app` existed, raising `FATAL: schema "app" does not exist` — exactly when the
   command is most needed. Fixed to detect and report the absent schema as a note.

5. **CORS would have blocked `DELETE` from a browser.** `allow_methods` was
   `["GET", "POST", "OPTIONS"]`, so the new `DELETE /api/v1/sessions/{id}` would
   have failed preflight. `"DELETE"` added.

6. **mypy false positives from a loop-variable name collision.** The app-schema
   audit reused `expected_cols`, which the pre-existing Silver Gate 1 loop binds to
   a `dict[str, str]` in the same function scope, producing two bogus errors in
   code this change did not touch. Renamed.

---

## 5a. Findings from the code review

Raised by review after the implementation above. **All Required findings are now
fixed**; the two Optional ones remain open by choice.

### Required — fixed

**R0 / defect 1 above (transaction).** See §5.1.

**R5 (was not in the review — found while fixing R2).** The summary feature had
**never worked in production**. `SessionRepository` returns **dicts**, but
`SessionSummaryService.build()` read turns with `getattr(turn, "role", None)`.
On a dict that is always `None`, so every row was filtered out, `build()`
returned `""`, and `refresh_summary` read that as "nothing to say" and skipped
the upsert. No summary was ever written.

```
repository rows (dicts)  -> getattr(row, "role") is None  -> rows = []
                         -> build() == ""                -> upsert skipped
```

It survived review because every unit test passed `ConversationMessage` models
rather than repository rows, and the integration test seeded summaries with raw
SQL, bypassing the code path entirely. The docstring had claimed dict rows
worked.

Fixed with a `_field(turn, name)` accessor that handles both shapes, so the
split cannot be reintroduced by the next field read. Regression tests:
`TestSummaryBuildHandlesRepositoryRows`, including a dict-vs-model equality test.

**R1 — duplicated scope renderer.** `_render_scope` (summary service) and
`_render_scope_parts` (session service) were the same function with the label map
copied into both. A label edited in one place would silently not appear in the
other. Both now call one `render_scope_parts()` in `models/session.py`, next to
`SCOPE_KEY_ORDER` and `SCOPE_LABELS`, with an import-time assert that the label
map covers exactly the inherit-able keys. Guarded structurally by
`TestScopeRenderingIsNotDuplicated`, which AST-counts renderers so a second copy
fails even while the two still agree.

**R2 — unbounded summary refresh.** `refresh_summary` called
`list_messages(session_id)` with **no LIMIT**, so every session-aware request
pulled the whole transcript into memory and into the renderer, and per-request
cost grew without bound as the conversation did. It now reads
`list_recent_messages(limit=settings.session_recent_messages_limit)` — the same
window `load_context` uses, so summary scope and context scope cannot disagree.
`messages_covered` is documented as the window size, not the session total.

**R3 — `status='failed'` was never written.** The literal existed in migration
005, in its CHECK constraint, and in its column comment, and no code path wrote
it. A turn whose answer never generated was indistinguishable from one that
never happened. `ask_question` now wraps the pipeline call and records a failed
assistant turn before re-raising.

Two details that matter:

- The stored content is a fixed string (`FAILED_TURN_ANSWER`), **never
  `str(exc)`**. `GET /sessions/{id}` returns stored content verbatim, so an
  exception message would surface SQL, connection and host detail to whoever
  reads the session. `request_id` on the same row is the correlation key.
- The failure write is wrapped in its own `try/except`. `SessionService` already
  swallows its own storage errors, but relying on that here would make the
  handler's correctness depend on an implementation detail three layers down.

**R4 — `use_session_context=false` still injected the transcript.** The flag
gated filter inheritance but `render_conversation_block()` ran unconditionally,
so the prior transcript still reached the optional LLM narration step. A caller
asserting "treat this standalone" got global scope but local narration. Both
effects now live in the same `if`, deliberately — splitting them would permit the
meaningless combination. Persistance is unaffected: opting out of the prompt is
not opting out of history.

### Optional — open

| Finding | Why it is still open |
|---|---|
| `archive_session` / `set_session_status` are dead — no endpoint exposes the transition, yet `status` has a CHECK constraint, a partial index, and a list filter | Needs a product decision: add `PATCH /sessions/{id}`, or drop the archived state. Not a code-quality fix. |
| `_stamp_total` is called at every stage boundary | Left in place deliberately. Each call recomputes from `start_time`, so `total_ms` is correct on every early return. Documented rather than "deduplicated" — removing them would make `latency_breakdown_ms` wrong on short-circuit paths. |

### Also fixed in this pass

- `_run_ask_pipeline` took a `Request` it never read. Removed, so the signature
  no longer implies per-request state is available inside the pipeline.

---

## 6. Known open items (not introduced here)

- **R1 (pre-existing, still open).** `.env` `DB_URL` points at role `postgres`
  (owner), not `app_readonly` — documented in docs/08 §1.1. Adding a write path
  makes this more consequential, which is exactly why the session write path uses
  a separate DSN rather than reusing `DB_URL`. R1 itself is unchanged and remains
  an owner action.
- **Frontend "Recent Sessions" UI.** Not implemented. Out of the agreed scope
  (backend + migration + tests + docs). `frontend/lib/api.ts` is untouched, so the
  existing 2-panel UI and `postAsk()` are unaffected.
- **Rate-limit sharing.** `/api/v1/sessions` shares the 60 rpm per-IP budget with
  `/api/v1/ask`. No code change; a UI should fetch on mount and after mutations
  rather than poll. Documented in docs/06 §6.2.2.
- **Two unrelated `Noqa`/encoding incidents during authoring** are not code
  defects; the affected files were verified clean afterwards by an explicit
  BOM/mojibake scan.
