# Fase 7 Close-out Report — Hybrid Multi-Rute & Sintesis Ter-grounding

**Tanggal eksekusi:** 2026-10-03
**Mode:** BUILD (eksekusi plan review Fase 7)
**Keputusan user:** B1 = integrasikan LLM Qwen (opt-in + fallback); peran DB = dokumentasikan + pertahankan `postgres` (risiko diterima).

## 1. Vonis

**`READY WITH CONDITIONS` → kondisi ditutup di bawah, kecuali 2 risiko yang diterima eksplisit (R1, R2). Fase 8 dapat dibuka.**

## 2. B2 — Verifikasi live dependency (SELESAI, dengan temuan)

Metode: `check_db_health()` + `asyncpg` read-only (`SELECT COUNT(*)`, `pg_roles`, `information_schema`).

| Item | Hasil |
|---|---|
| `status` / `role` | `connected` / `postgres` (owner, `rolsuper=false`) |
| Silver 9 tabel | READY (`publications:20, authors:138, institutions:107, keywords:344, funding:33, pub_author:138, pub_institution:108, publication_references:4120, chunks:40`) |
| Gold 3 tabel | READY (`topics:5, topic_evolution:25, researcher_expertise:140`) — menutup kontradiksi inter-doc B2 |
| Edge 2 tabel | `institution_collaboration:254, author_collaboration:484` |
| pgvector | `vector 0.8.2`, `chunks_null_emb=0`, HNSW aktif |
| `GRANT SELECT app_readonly` | Terkonfirmasi pada tabel Gold (dan Silver/Edge per health probe) |
| `statement_timeout` | Pool memaksa 10s via `server_settings`; default DB 2min (hanya berlaku di luar pool) |

**R1 (risiko diterima):** `.env DB_URL` memakai role `postgres`, BUKAN `app_readonly` — write-rejection TIDAK ditegakkan pada level role. Guard primer = AST whitelist + template terparameterisasi. Tindakan pemilik: ganti `.env` ke URL `app_readonly` lalu verifikasi ulang `/health.role`.

## 3. B3 — Reproduksi test suite (SELESAI)

| Run | Hasil |
|---|---|
| `tests/unit + tests/integration` | **319 passed** satu run (257 unit termasuk 13 baru `test_llm_synthesizer.py` + 4 baru `test_fase7_gaps.py`) |
| `tests/e2e` mock | 12 passed + 2 skipped (live-only) |
| `E2E_LIVE=1 tests/e2e` (DB + Ollama live) | **14 passed** — klaim "14/14 live E2E" TERREPRODUKSI |
| Koleksi total | **333 tests** (257 unit + 62 integration + 14 e2e; menggantikan klaim lama "324") |

Catatan: satu run penuh sempat menunjukkan 2 gagal flaky (`filters_ignored`, `zero_match_latency_guard` — keduanya live-DB + sensitif waktu, lolos isolasi); run penuh berikutnya 331 passed + 2 skipped (mock) dan 14/14 live E2E. Atribusi: pooler Supabase `ConnectionResetError` di bawah beban, bukan regresi kode (`llm_synthesis` default OFF = nol I/O tambahan).

## 4. B1 — Sintesis LLM Qwen terintegrasi (SELESAI)

Implementasi: `backend/app/services/synthesizer/llm.py` (`SYNTHESIS_SYSTEM_PROMPT` persis §6 docs/05, `build_synthesis_prompt`, `generate_synthesis_text` timeout `OLLAMA_TIMEOUT_S=8s`, `LlmAnswerSynthesizer.refine` + fallback deterministik), wiring 4 cabang `ask.py` via `_maybe_llm_refine`, kontrak aditif `AskRequest.llm_synthesis` + `DebugInfo.synthesis_backend`/`llm_synthesis_ms` (tercermin di `docs/06 §5`).

| Verifikasi | Hasil |
|---|---|
| Model `qwen2.5-coder:7b-instruct` di-pull lokal (4.7 GB) | OK |
| Latensi mentah CPU (64 token) | ~14.7 dtk → NFR 5–10 dtk TIDAK tercapai di CPU; LLM synthesis butuh GPU |
| Live `/ask` + `llm_synthesis=true` (Hybrid experts) | `synthesis_backend: deterministic-fallback`, `llm_synthesis_ms=8403`, jawaban deterministik valid, evidence utuh (10 objek) |
| Probe adversarial live (instruksi injeksi + klaim `99999` + sitasi fiktif dalam blok UNTRUSTED, 174 dtk generasi) | Model MENGABAIKAN injeksi (`99999`/`Obat Ajaib` tidak muncul); angka output identik evidence; `unverified=[]` |
| Unit (mocked) | 13/13 (`test_llm_synthesizer.py`): prompt §6, error paths, strip halusinasi, fallback, endpoint opt-in + fallback |

## 5. Benchmark NFR per-rute (live, warm kecuali dinyatakan)

| Rute | Wall / internal | Target | Status |
|---|---|---|---|
| SQL agregat | 414ms / 312ms (retrieval 55ms) | ≤500ms | PASS |
| Vector warm | 274ms / 246ms (embedding 193ms) | ≤1.5s | PASS |
| Vector cold (load model lokal ~14s + encode) | 22.1s | ≤1.5s | FAIL dingin / KNOWN (cold-start satu kali) |
| Hybrid trends | 119ms / 89ms | ≤1.0s | PASS |
| Hybrid experts | 254ms / 224ms | ≤1.0s | PASS |
| Zero-evidence Vector (`not_found`) | 274ms / 246ms | <200ms | MARGINAL (terikat embedding; target <200ms belum tercapai) |
| Graph→klarifikasi (2 kandidat "Universitas Indonesia") | 122ms | ≤500ms | PASS (entity gate bekerja) |

**R2 (risiko diterima):** target `<200ms` zero-evidence dan NFR sintesis LLM 5–10 dtk tidak tercapai di hardware CPU ini; keduanya dicatat sebagai baseline Fase 8, bukan gate.

Temuan perilaku D1-b terkonfirmasi live: `topic_name` tak dikenal dipetakan centroid-fallback ke topik terdekat (20 evidence, `status: ok`, dilabeli "topik terkait") — `HybridRoute` praktis tidak pernah `not_found` pada filter `topic_name`. Didokumentasikan di `docs/05 §5.4` + dikunci via test.

## 6. Resolusi kontrak F-1–F-9

| ID | Status |
|---|---|
| F-1 Gold PLANNED vs DONE | DITUTUP — `docs/03` + `docs/12` bump v3.7.1, Gold DONE terverifikasi live |
| F-2 LLM vs deterministik | DITUTUP via B1-b (opt-in + fallback); `docs/05 §6` + `docs/11 §Fase 7` direvisi |
| F-3 `FilterParams.keyword` | DITUTUP — masuk `docs/06 §5.1` |
| F-4 `DebugInfo.embedding_backend` + baru `synthesis_backend` | DITUTUP — masuk `docs/06 §5.2` |
| F-5 `insufficient_evidence` | DITUTUP — dipetakan ke `not_found`, tidak ada status kelima |
| F-6 "fallback LLM" router | DITUTUP — diklarifikasi sebagai fallback rute |
| F-7 "satu kueri" Hybrid | DITUTUP — narasi diganti multi-templat + whitelist Pydantic |
| F-8 status Fase 3 `[CURRENT]` | DITUTUP — Fase 3 → `[DONE — VERIFIED]` |
| F-9 header `docs/06` stale Phase 2 | DITUTUP — header v3.7.1 sinkron Fase 7 close-out |

## 7. Definition of Done Fase 7 — status akhir

- [x] B1 diputuskan dan terdokumentasi (opsi LLM opt-in terintegrasi + fallback)
- [x] B2 terbukti (counts Gold/Edge/Silver + health + grants; write-ditolak TIDAK berlaku untuk role postgres — R1)
- [x] B3 terbukti (319 + 12/2 mock + 14/14 live; angka aktual menggantikan klaim)
- [x] Mismatch F-1–F-9 tertutup (`docs/03`, `docs/05`, `docs/06`, `docs/10`, `docs/11`, `docs/12`)
- [x] Prioritas routing + contoh Graph-menang-atas-SQL terdokumentasi (`docs/05 §3`) + test
- [x] Perilaku TOPIC_TRENDS-tanpa-sources terdokumentasi (`docs/05 §5.4`) + test
- [x] Test gaps MEDIUM tertutup (`test_fase7_gaps.py`: 503 endpoint, tren-tanpa-sources, multi-intent, fallback Hybrid)
- [x] Benchmark NFR terukur (baseline di §5; 2 deviasi dicatat R2)
- [x] Tidak ada jalur Raw Retrieval → LLM (prompt LLM SELALU via `to_untrusted_evidence_block`; verifier mekanis pasca-LLM)
- [x] 100% guardrail SQL lulus; 0 sitasi tak terverifikasi mencapai `AskResponse` final pada benchmark E2E
- [→] Gate review Fase 8 DAPAT DIBUKA (Fase 8: verifikasi formal + baseline latensi + sign-off MVP)

## 8. Sisa untuk Fase 8 / pemilik

1. R1: migrasi `.env` ke role `app_readonly` + verifikasi ulang `role` via `/health` (pemilik, kredensial).
2. R2: target `<200ms` zero-evidence + sintesis LLM GPU (Fase 8/10).
3. Stale di luar cakupan (tidak disentuh): `docs/01`, `docs/02`, `docs/04`, `docs/07`, `docs/08`, `docs/09` masih menyatakan PLANNED/PENDING untuk komponen yang sudah DONE.
4. Working tree memuat perubahan tak-tercommit dari sesi lain (unifier.py, answer.py, frontend/, dll.) — di luar cakupan close-out ini; jangan di-merge tanpa review pemilik.
