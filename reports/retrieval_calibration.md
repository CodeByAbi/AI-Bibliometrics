# VectorRoute Retrieval Calibration (P1)

Recalibration of the `VectorRoute` cosine-similarity gate from **0.65** to **0.48**, selected from
measurement against a labelled benchmark rather than from a single observed score range.

- Date: 2026-10-04
- Scope: vector retrieval quality only. No architecture, model, evidence-layer, synthesizer,
  citation-verification, or SQLRoute change.
- Artifacts: `tests/fixtures/retrieval_benchmark_v1.json`, `scripts/bench_retrieval.py`,
  `reports/retrieval_benchmark/`, `tests/fixtures/retrieval_baseline_v1.json`,
  `tests/integration/test_retrieval_benchmark.py`

---

## 1. Current implementation (as audited)

| Property | Value |
|---|---|
| Vector statement | `vector_retriever.py` — CTE `ann_candidates` → `scored_chunks` → `gate_stats` → `LEFT JOIN LATERAL` |
| ANN window | `ann_window(top_k)` = `min(max(top_k × 25, 100), 2000)` = 200 at `top_k=8` |
| Similarity | `1 - (embedding OPERATOR(extensions.<=>) $1::extensions.vector)` |
| Gate | `(1 - distance) >= $3`, enforced by PostgreSQL (inclusive) |
| Dedup | `DISTINCT ON (publication_id)`, best (minimum-distance) chunk per publication |
| Result limit | `LIMIT $4`, applied **after** gate and dedup, on distinct publications |
| Threshold | 0.65 → **0.48** (was a module constant, already env-overridable) |
| Top-K | 8 → **8** (was a hardcoded module constant, now `VECTOR_TOP_K`) |
| Embedding | `BAAI/bge-m3`, 1024-d, `normalize_embeddings=False`, local SentenceTransformer |
| HNSW | `idx_chunks_embedding_hnsw` on `chunks.embedding`, `vector_cosine_ops`, `m=16`, `ef_construction=64`; `hnsw.ef_search = 100` set per connection |
| Evidence handoff | `EvidenceUnifier.from_vector` → `EvidenceRanker` → `EvidenceObject` / `EvidenceItem` / `SourceItem` |
| `not_found` | `_vector_zero_evidence_class` splits `no_candidates` from `below_vector_threshold`; `EmbeddingError`→503 and `DBTimeoutError` propagate as errors |

## 2. Similarity-semantics verification (no bug found)

`docs/05` specifies cosine **similarity**. The code computes distance via pgvector `<=>`
(cosine distance = 1 − cosine similarity) and gates on `1 - distance >= threshold`.

Verified this is **not** inverted: the projection (`similarity_score`), the gate predicate, and
`EvidenceRanker.calculate_vector_confidence` all consume similarity, never distance. Stored
embeddings are unnormalized (`normalize_embeddings=False`) but the cosine operator normalizes
internally, so that is not a factor. **No change was required in Phase 1.**

## 3. HNSW verification

`EXPLAIN (ANALYZE, BUFFERS)` on the **exact statement the request path executes** (obtained from
`VectorRetriever.build_query`, not a paraphrase):

```
Sort  (actual time=18.159..18.163 rows=1)
  CTE ann_candidates
    ->  Limit  (actual rows=40)
          ->  Sort  (Sort Key: ((c.embedding <=> '[...]'::vector)))
                ->  Hash Join
                      ->  Seq Scan on chunks c   (actual rows=40)
Execution Time: 18.322 ms
```

**Result: the HNSW index exists and is correct, but the planner does not use it at this corpus
size.** `chunks` holds 40 rows, for which a sequential scan plus sort is genuinely cheaper than
any index probe; the ANN window (200) also exceeds the table, so every chunk is a candidate.

This is recorded as a **known limitation, not a defect**. No planner hint was added: forcing
`enable_seqscan=off` at 40 rows would optimize nothing and would misrepresent production
behaviour. HNSW usage must be re-verified at production corpus size; the existing executable check
is `tests/integration/test_vector_live_hnsw.py`.

A methodological benefit of the small corpus: because the ANN window (200) exceeds the table (40),
**ANN recall is 100%**, so the sweep isolates threshold effects with no ANN-truncation confound.
That property disappears once the corpus grows.

## 4. Root cause of the retrieval misses

`0.65` sits far above the score range bge-m3 assigns to natural-language questions against this
corpus. Measured background similarity across **759 cross-publication chunk pairs** (an
"unrelated pair" proxy):

| Statistic | Value |
|---|---|
| median | 0.4402 |
| p95 | 0.5438 |
| p99 | 0.5798 |
| max | 0.6005 |
| P(pair ≥ 0.50 / 0.55 / 0.60 / 0.65) | 19.4% / 3.7% / 0.13% / **0.00%** |

Same-publication chunk pairs (21 pairs): min **0.7371**, median 0.8517, max 0.9232.

So the corpus has a genuine separation gap of `0.6005 → 0.7371`, and `0.65` sits inside it —
high precision, but it rejects every query phrased in ordinary language. A live 11-query probe
confirmed the mechanism: exact title 0.7637 (passes), close title 0.6095, natural-language 0.5716,
paraphrase 0.5372, short 0.5353 (**all rejected**), while clean negatives topped out at 0.4580.

## 5. Benchmark dataset

`tests/fixtures/retrieval_benchmark_v1.json` — **94 queries**, graded relevance **0–3** per
`publication_id`, granularity **publication-level**.

| Category | n | Notes |
|---|---|---|
| `natural_language` | 15 | factual questions against corpus content |
| `paraphrase` | 10 | restatements of a corpus topic |
| `conceptual` | 10 | abstract/thematic, spans a publication cluster |
| `title_entity` | 20 | titles and title fragments from the corpus |
| `short` | 16 | 2–3 word keyword queries |
| `negative` | 15 | **strict absence** — every label grade 0, so any return is a false positive |
| `ambiguous` | 8 | broad multi-cluster intent, or labels contested on adjudication |

79 positive / 15 negative. Negatives were labelled only where the corpus truly contains nothing
(quantum computing, maritime logistics, photovoltaic efficiency, …).

**Adjudication.** Two queries were moved out of `negative` after review, with the reasoning
recorded in the fixture:

- `retrieval_076` "machine learning" → positive, `PUB000007: 2`. Deep reinforcement learning
  (traffic signal control) **is** machine learning, so this is a real semantic match, not corpus
  absence. It is also the hardest negative-adjacent case measured (0.4975).
- `retrieval_083` "artificial intelligence" → positive, `PUB000007: 2`, `PUB000005: 2`,
  `PUB000009: 1`. Only 3 of 20 publications are AI-adjacent, so a naive corpus read would call it
  negative; all three are genuinely AI systems. Contrast `retrieval_075` ("large language model
  reasoning"), which the corpus genuinely does not cover and is a true negative.

`granularity_note`: chunk-level labels were not produced. With 40 chunks over 20 publications they
would be near-duplicates of publication labels, so retrieved chunk scores are attributed to their
parent publication via the existing `DISTINCT ON (publication_id)`.

## 6. Baseline at 0.65

| Metric | Value |
|---|---|
| nDCG@8 | 0.3991 |
| Precision@8 | 0.4051 |
| Hit@8 | 0.4051 |
| MRR@8 | 0.4051 |
| Recall@8 (diagnostic) | 0.3956 (ceiling 0.9861) |
| Negative FP rate | 0.0000 (0/15) |
| Mean publications returned (positives) | **0.41** |

**The gate answered `not_found` for 59.5% of queries the corpus could answer**, returning on
average 0.41 publications per answerable query. Its precision was high only because it mostly
returned nothing.

## 7. Threshold sweep

Full table in `reports/retrieval_benchmark/sweep_v2.md` (16 candidates, `top_k=8`).

| Threshold | nDCG@8 | Precision@8 | Hit@8 | MRR@8 | Recall@8 (ceil) | Neg FP | max corpus top (neg) |
|---|---|---|---|---|---|---|---|
| 0.44 | 0.8982 | 0.5329 | 0.9747 | 0.9536 | 0.8722 (0.9861) | **0.4667 (7/15)** | 0.4793 |
| 0.45 | 0.8876 | 0.6135 | 0.9747 | 0.9536 | 0.8598 (0.9861) | **0.4000 (6/15)** | 0.4793 |
| 0.46 | 0.8680 | 0.6785 | 0.9620 | 0.9409 | 0.8412 (0.9861) | **0.2000 (3/15)** | 0.4793 |
| 0.47 | 0.8514 | 0.7024 | 0.9367 | 0.9219 | 0.8254 (0.9861) | **0.1333 (2/15)** | 0.4793 |
| **0.48** | **0.8257** | 0.7060 | 0.8861 | 0.8713 | 0.8043 (0.9861) | **0.0000 (0/15)** | 0.4793 |
| 0.49 | 0.7992 | 0.7155 | 0.8481 | 0.8418 | 0.7718 (0.9861) | 0.0000 (0/15) | 0.4793 |
| 0.50 | 0.7779 | 0.7271 | 0.8228 | 0.8228 | 0.7451 (0.9861) | 0.0000 (0/15) | 0.4793 |
| 0.55 | 0.6237 | 0.6392 | 0.6582 | 0.6582 | 0.5970 (0.9861) | 0.0000 (0/15) | 0.4793 |
| 0.60 | 0.5423 | 0.5570 | 0.5570 | 0.5570 | 0.5348 (0.9861) | 0.0000 (0/15) | 0.4793 |
| 0.65 (baseline) | 0.3991 | 0.4051 | 0.4051 | 0.4051 | 0.3956 (0.9861) | 0.0000 (0/15) | 0.4793 |
| 0.70 | 0.3165 | 0.3165 | 0.3165 | 0.3165 | 0.3165 (0.9861) | 0.0000 (0/15) | 0.4793 |
| 0.75 | 0.2405 | 0.2405 | 0.2405 | 0.2405 | 0.2405 (0.9861) | 0.0000 (0/15) | 0.4793 |

The `max corpus top (neg)` column is the highest **pre-gate** score reached by any strict-absence
negative. It is constant at **0.4793**, which is the mechanism behind the whole table: the negative
false-positive boundary is a property of the corpus and the query set, not of the gate, and any
gate at or above 0.4793 cannot admit a negative.

### Per-category nDCG@8 / Hit@8

| Threshold | natural_language | paraphrase | conceptual | title_entity | short | ambiguous | negative (FP) |
|---|---|---|---|---|---|---|---|
| 0.48 | 0.941 / 1.00 | 0.898 / 1.00 | 0.534 / 0.60 | 1.000 / 1.00 | 0.889 / 0.94 | 0.323 / 0.50 | 0.00 |
| 0.50 | 0.902 / 0.93 | 0.858 / 0.90 | 0.534 / 0.60 | 1.000 / 1.00 | 0.824 / 0.88 | 0.103 / 0.25 | 0.00 |
| 0.55 | 0.902 / 0.93 | 0.685 / 0.80 | 0.246 / 0.30 | 1.000 / 1.00 | 0.403 / 0.44 | 0.000 / 0.00 | 0.00 |
| 0.60 | 0.902 / 0.93 | 0.370 / 0.40 | 0.100 / 0.10 | 1.000 / 1.00 | 0.288 / 0.31 | 0.000 / 0.00 | 0.00 |
| 0.65 | 0.635 / 0.67 | 0.000 / 0.00 | 0.000 / 0.00 | 1.000 / 1.00 | 0.125 / 0.12 | 0.000 / 0.00 | 0.00 |

`title_entity` is the only category that worked at 0.65 — which is exactly the reported symptom:
only verbatim title lookups returned evidence.

## 8. Selected threshold: 0.48

The rule was fixed **before** the sweep ran, to prevent post-hoc rationalization:

1. Discard any gate whose negative false-positive rate > 0.
2. Among survivors, take the highest nDCG@8.
3. Tie-break within Δ0.02 nDCG toward the **higher** gate.
4. Flag any category regressing in Hit@8 against the 0.65 baseline, even if the global nDCG improves.

Outcome, verbatim from the run:

> Rule 1 kept `['0.48','0.49','0.50','0.51','0.52','0.53','0.54','0.55','0.60','0.65','0.70','0.75']`
> (negative FP rate == 0). Rule 2 best nDCG@K = 0.8257. Rule 3 tie-break within 0.02 nDCG over
> `['0.48']` selected the highest gate, **0.48** (nDCG@K 0.8257, FP rate 0.0000).

Rule 4: **no category regressed.** Every category improved or held.

**Why 0.48.** It is the highest-recall gate that admits zero strict-absence negatives. It is not an
interpolation of observed scores — it is exactly the measured negative ceiling (0.4793), so the
no-fabrication property holds by construction rather than by luck. Above it, recall falls away
steeply (0.50 → −0.048 nDCG; 0.55 → −0.202 nDCG) while precision barely moves, because the gate
begins cutting true positives without buying meaningful precision. Below it, negatives leak
immediately (0.47 → 2/15).

**Alternative considered: 0.50.** The nearest gate with a non-zero margin, at −0.048 nDCG@8 and
−0.063 Hit@8. Defensible if margin against unseen negatives is preferred over prototype recall.
The pre-registered rule selected 0.48; this is recorded so the trade-off is visible rather than
implicit.

## 9. Before / after

| Metric | Before (0.65) | After (0.48) | Delta |
|---|---|---|---|
| nDCG@8 | 0.3991 | 0.8257 | **+106.9%** |
| Precision@8 | 0.4051 | 0.7060 | +74.3% |
| Hit@8 | 0.4051 | 0.8861 | **+118.7%** |
| MRR@8 | 0.4051 | 0.8713 | +115.1% |
| Recall@8 (diagnostic) | 0.3956 | 0.8043 | +103.3% |
| Negative FP rate | 0.0000 | 0.0000 | unchanged (0/15) |
| Mean returned (positives) | 0.41 | 1.80 | +339% |

| Category | nDCG@8 before | nDCG@8 after | Hit@8 before | Hit@8 after |
|---|---|---|---|---|
| natural_language | 0.635 | 0.941 | 0.67 | 1.00 |
| paraphrase | 0.000 | 0.898 | 0.00 | 1.00 |
| conceptual | 0.000 | 0.534 | 0.00 | 0.60 |
| title_entity | 1.000 | 1.000 | 1.00 | 1.00 |
| short | 0.125 | 0.889 | 0.12 | 0.94 |
| ambiguous | 0.000 | 0.323 | 0.00 | 0.50 |
| negative (FP rate) | 0.000 | 0.000 | — | — |

Retrieval quality improved materially on every answerable category, with **zero** regression in
negative-query behaviour. Recall@8 is reported as a diagnostic only: with `top_k=8` over a
20-publication corpus its per-query ceiling is `min(8, |relevant|) / |relevant|` (0.9861 mean
across this benchmark), so it cannot reach 1.0 by construction.

## 10. Files changed

**New**

| File | Purpose |
|---|---|
| `tests/fixtures/retrieval_benchmark_v1.json` | 94 labelled queries, graded 0–3, adjudication notes |
| `tests/fixtures/retrieval_baseline_v1.json` | Committed regression floor + 0.65 historical baseline |
| `scripts/bench_retrieval.py` | Sweep harness (ruff + mypy clean) |
| `tests/integration/test_retrieval_benchmark.py` | Live regression gate, 12 checks |
| `reports/retrieval_benchmark/corpus_digest.md` | 20 titles + 40 chunk excerpts (labeling reference) |
| `reports/retrieval_benchmark/sweep_v1.md` / `.json` | Coarse sweep (7 candidates) |
| `reports/retrieval_benchmark/sweep_v2.md` / `.json` | Refined sweep (16 candidates) + per-query raw results |
| `reports/retrieval_calibration.md` | This report |

**Modified**

| File | Change |
|---|---|
| `backend/app/core/config.py` | `vector_cosine_threshold` default 0.65 → 0.48 + rewritten rationale; **new** `vector_top_k` setting |
| `backend/app/services/retrievers/vector_retriever.py` | New `resolve_operating_point` classmethod (settings-backed, range-checked); top-K now from `Settings`; `top_k` added to diagnostics; threshold docs updated |
| `.env.example` | **Added `VECTOR_COSINE_THRESHOLD`** (the setting existed but was never documented) and `VECTOR_TOP_K` |
| `tests/unit/test_vector_retriever.py` | Boundary test parametrised at gate ∓ε against the deployed gate (was hardcoded 0.65); `resolve_operating_point` coverage; configurable top-K test |
| `tests/integration/test_ask_endpoint.py` | Split the conflated test: xanthine query now asserts evidence (was wrongly asserting `not_found`); a strict-absence query now covers the `not_found` path |
| `tests/e2e/test_e2e_12_queries.py` | Q04/Q05 flipped `not_found` → evidence, with benchmark-measured mock scores; stale Ollama-parity comment corrected |
| `docs/05`, `docs/11`, `docs/06`, `README.md` | Threshold, prototype-calibrated marker, version bumps |

## 11. Tests executed

```
pytest -q                                             620 passed, 41 skipped
E2E_LIVE=1 pytest tests/e2e -q                        14 passed
pytest tests/integration/test_retrieval_benchmark.py  12 passed
pytest tests/unit/test_vector_retriever.py
       tests/unit/test_evidence.py                     58 passed  (was 49)
ruff check backend scripts tests --select E9,F63,F7,F82,F821,F811   all checks passed
mypy scripts/bench_retrieval.py                       clean
```

The 41 skips are pre-existing live-DB / session-role suites (no `DB_URL_SESSION`, no E2E marker).
The new benchmark gate skips without a database **and** without a usable embedding backend, because
a skipped retrieval gate proves nothing.

## 12. HNSW verification result

See §3. Index present and correctly defined; **not selected by the planner at 40 rows**
(`Seq Scan + Sort`, 18.3 ms). Recorded as a known limitation; no planner hint added.

## 13. Observability

`VectorRetrievalResult.diagnostics` now also carries `top_k`. Existing fields already cover
`threshold`, `ann_candidate_window`, `candidate_rows`, `rows_after_threshold`,
`unique_publications`, `top_similarity`, `top_similarity_delta`, `vector_query_ms`,
`embedding_backend`, `embedding_ms`, `embedding_model`, `filters_ignored`. Structured logs already
distinguish `threshold_gate=hit` / `miss` with a `gate_verdict` of `no_candidates` vs
`below_threshold`. No chunk text is logged; scores stay server-side as aggregates.

## 14. Known limitations

1. **The selected gate has ~zero margin against this negative set.** 0.4793 is the measured maximum
   for 15 negatives; 0.48 clears it by 0.0007. It is fitted to the observed ceiling, so a single
   harder negative — or any corpus growth — can push that ceiling above 0.48 and admit false
   positives. `0.50` is the nearest gate with measurable margin (0.0207) at a cost of −0.048 nDCG@8.
   Treat 0.48 as a boundary, not a centre.
2. **Prototype-calibrated.** 20 publications / 40 chunks. 94 queries over 20 publications means
   correlated evaluation data: one publication is legitimately relevant to many queries, so this
   does not provide the statistical diversity of 94 independent documents.
3. **Recall@8 is structurally capped** by `top_k=8` against a 20-publication corpus
   (per-query ceiling `min(8,|relevant|)/|relevant|`). nDCG@8 is the primary aggregate for this
   reason; Recall@8 is a diagnostic only.
4. **Publication-level labels.** Chunk-level relevance was not labelled.
5. **Embedding-backend coupling.** Calibration is pinned to `embedding_backend=local` (HF
   SentenceTransformer). The open fase-8 item B4 records that the Ollama bge-m3 fallback does not
   produce identical vectors. Every artifact records the backend and the regression gate asserts it,
   but a backend change invalidates the numbers.
6. **ANN recall is currently untested by this benchmark** — at 40 chunks the ANN window exceeds
   the table, so 100% of chunks are candidates. ANN truncation loss becomes a real confounder once
   the corpus exceeds the 200-row window.
7. **HNSW remains unexercised** at this corpus size (§3).
8. **`ambiguous` queries stay weak** (nDCG 0.323, Hit@8 0.50). Broad single-word intents
   ("health", "plants", "technology") remain near-chance against a 20-publication corpus; this is a
   corpus-coverage limit, not a gate limit, and would not improve by moving the threshold.

## 15. Recommended next retrieval improvements

1. **Re-run `scripts/bench_retrieval.py` after production-scale ingestion** and treat the resulting
   gate as authoritative. This supersedes the prototype value; the regression gate and committed
   baseline must be regenerated in the same change.
2. **Expand the negative set** to at least 50–100 strict-absence queries, and prefer a gate that
   clears the negative ceiling by a stated margin rather than landing on it. This is the single
   highest-value change to the reliability of the calibration.
3. **Re-verify HNSW at scale** (`tests/integration/test_vector_live_hnsw.py`) once `chunks` exceeds
   the planner's sequential-scan breakpoint, and re-tune `hnsw.ef_search` and the ANN
   overfetch multiplier, which are currently sized by reasoning rather than measurement.
4. **Consider chunk-level labels** once the corpus justifies it; publication-level dedup currently
   discards intra-publication evidence that a chunk-level benchmark could score.
5. **Close fase-8 B4** (embedding-backend parity) so the gate is valid on both the local and Ollama
   paths, or pin one backend explicitly and fail loudly on the other.
6. **Route broad/ambiguous intent to `HybridRoute`** rather than widening the gate for it: the
   `ambiguous` ceiling is a coverage problem, and threshold tuning cannot fix it.

---

## Evaluation limitation

> The current benchmark operates on a prototype corpus of approximately 20 publications
> (40 chunks). Consequently, document-level recall is constrained by the top-k=8 retrieval limit and
> corpus size, while multiple queries may share the same relevant publications. nDCG@8 is therefore
> used as the primary ranking-quality metric. Threshold performance is considered
> prototype-calibrated and must be revalidated after production-scale corpus expansion.