# Retrieval Diagnostic Baseline

Measured on the reference deployment, 2026-10-04. Every number here was observed,
not estimated. Supersedes the estimates previously carried in `docs/05` and
`reports/fase7_closeout.md`, several of which were wrong about cause rather than
merely imprecise.

## Corpus

| | |
|---|---|
| publications | 20 (all `year = 2025`) |
| chunks | 40, all 1024-dim (`BAAI/bge-m3`) |
| authors / institutions | 138 / 107 |
| topics / topic_evolution / researcher_expertise | 5 / 25 / 140 |
| runtime DB role | `app_readonly` (SELECT only; every write class blocked) |

Because every publication is 2025, any year-scoped question outside 2025 is
`DATABASE_EMPTY`. That is a property of the prototype dataset, not a retrieval
fault, and it is why the year-fidelity defect below was so easy to miss.

## Latency

| stage | measured |
|---|---|
| deterministic SQL template | 84-95 ms end to end |
| SQL server-side execution | 0.653 ms (`EXPLAIN ANALYZE`) |
| Supabase RTT, per round trip | ~23 ms |
| embedding model load (cold) | 10.4-185.8 s |
| embedding (warm) | 154-185 ms |
| per-request `set_config('hnsw.ef_search')` | 29-54 ms, **no effect on any plan** |
| pool release path (`reset="light"`) | 21.8-29.9 ms of unattributed round trips |

## Ollama generation, measured

`qwen2.5-coder:7b-instruct`, 4.36 GB, CPU only:

| | |
|---|---|
| generation | 5.7-7.3 tok/s |
| prompt eval | ~70 tok/s |
| **model load** | **69 s** |
| real synthesis prompt | 1359 prompt tokens + 128 generated |

    warm  ->  22 s
    cold  ->  91 s   (22 s + the 69 s load)

The synthesis budget is dominated by **whether the model is resident**, not by
generation. Ollama's default 5-minute `keep_alive` evicts it between requests, so
in practice every call was cold.

### Dead ends, recorded because the reasoning error is the reusable part

| budget | outcome |
|---|---|
| 8 s (original) | 0% success — cannot emit a single token |
| 90 s | 0% success — estimated 512 tok ≈ 87 s, plus the 69 s cold load |
| 60 s | 0% success in practice — covers warm (22 s) but not cold (91 s) |
| **120 s + 128 tokens** | **100% success, verified from a deliberately evicted model: 89.8 s wall, `llm_calls=1`, `fallback_rate=0.0`** |

Sizing a generation budget without measuring the model load produces a setting
that looks generous and fails every time. Raising Ollama's `KEEP_ALIVE` makes
synthesis ~22 s instead of ~91 s; that is a server-side setting, documented here
rather than applied by the application.

## Vector cosine calibration

Labelled probe distribution for the chunk-level gate:

| probe class | cosine |
|---|---|
| off-topic | 0.4067 |
| natural-language topical | 0.5552 - 0.6080 |
| near-verbatim title | 0.6674 |
| verbatim title | 0.8191 |

The canonical 0.65 sat inside the natural-language band, so ordinary topical
questions were rejected while only near-verbatim titles passed. The gate is now
**0.48** (`VECTOR_COSINE_THRESHOLD`), with margin on both sides.

## Topic centroid cosine is not discriminative

Measured against `topics.representation_vector` (deterministic across repeats):

| probe class | cosine |
|---|---|
| exact canonical names | 0.4698 - 0.5946 |
| close paraphrases | 0.4698 - 0.5807 |
| clearly off-topic | 0.3403 - 0.5047 |

The populations overlap completely. Two consequences:

* It cannot select a topic. The exact name "Phytochemicals & Molecular Docking"
  scores **0.5807 against Microbiology & Food Biotechnology** — higher than
  against its own centroid. The former `>= 0.50` fallback selected the wrong
  topic.
* It cannot even decide corpus membership. "quantum computing" (0.5047) scores
  higher than the exact topic "Nanomaterials & Nanotechnology" (0.4698).

So centroid cosine is **diagnostic only**. Scope is decided by exact/normalized
name match; an unresolved topic returns `needs_clarification` with the real
topic list. No threshold is used, because no threshold separates these
populations.

An independent defect hid this one: the lookup referenced an unqualified
`$1::vector` and `<=>` while pgvector is installed in `extensions` and the pool
pins `search_path=public`, so it raised `UndefinedObjectError: type "vector" does
not exist` on every call. A broad `except Exception` logged that at DEBUG — below
default log level — so the entire semantic path was dead code and every
unresolved topic silently became a corpus-wide query.

## HNSW is not used at this corpus size

At 40 chunks the planner chooses `Seq Scan + Sort`; the HNSW index is never
consulted. The per-request `hnsw.ef_search` GUC that "tuned" it therefore cost
29-54 ms per query for no plan change. It is now a per-connection
`server_settings` entry, and the docs no longer claim the index serves retrieval.

## not_found taxonomy

| class | meaning |
|---|---|
| `entity_not_found` | a named author/institution matched zero records |
| `topic_unresolved` | topic-scoped question, no `topics.topic_name` matched |
| `topic_off_corpus` | superseded by `topic_unresolved`; no similarity split is sound |
| `empty_result_set` | rows returned, none survived the cosine gate |
| `no_candidates` | Gold/Hybrid tables returned nothing |
| `zero_aggregate` | a scalar COUNT evaluated to 0 — retrieval ran, found nothing |
| `entity_needs_clarification` | author name matched several people |

`zero_aggregate` is new. A `COUNT` of 0 previously became a confident evidence
object reading "total publikasi tercatat sebanyak 0", giving a not-found answer
the appearance of a measurement. It is also what masked the year bug: the wrong
predicate returned 21, a perfectly respectable-looking number, so nobody
questioned it.

## Read-only role: the real root cause

The runtime connected as `postgres`. Two independent facts combined:

1. `app_readonly` had `rolcanlogin = false`, so the documented role was unusable.
2. **RLS was enabled on all 15 `public` tables with zero policies.** RLS with no
   policy default-denies every role that is not the table owner. `app_readonly`
   saw **0 rows**; `postgres` saw 20 because it *owns* the tables and owners
   bypass their own RLS.

Fact 2 is why the deployment used `postgres`, and why switching `DB_URL` alone
would have been worse than the status quo: every request would have returned
200 OK with zero evidence, indefinitely.

Rejected alternatives: `BYPASSRLS` requires a superuser (the owner is not one);
`DISABLE ROW LEVEL SECURITY` would have converted the latent `anon`/`authenticated`
grants into live access to the corpus through the public API.

Adopted: migration 008 adds `FOR SELECT ... TO app_readonly` on every corpus
table. RLS stays on, the API roles stay denied, and a future accidental write
grant would still be blocked because a SELECT-only policy has no `WITH CHECK`.

Verified: SELECT returns 20/138/107/5/40 across the corpus tables;
INSERT, UPDATE, DELETE, TRUNCATE, DROP, CREATE, ALTER, CREATE INDEX and SET ROLE
all raise `InsufficientPrivilegeError`.

## Operational notes

* The Supabase **transaction** pooler derives its tenant from the username, so a
  custom role must be `app_readonly.<project-ref>`, not `app_readonly`. A bare
  `app_readonly` fails with `no tenant identifier provided`.
* `DB_URL_OWNER` must exist in `.env`. `scripts/db.get_db_connection()` prefers
  it and falls back to `DB_URL`, so without it admin scripts silently run as the
  runtime role and fail the first time they need to `GRANT`.
* `ALTER ROLE ... PASSWORD` cannot take a bound parameter; the password is
  composed with `psycopg.sql.Literal`. A DSN password must be URL-safe or
  percent-encoded, or the connection string will not parse.