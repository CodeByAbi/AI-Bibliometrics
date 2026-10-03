# Fase 8 Execution Plan - Gerbang Sign-Off MVP (+ Task 11 Frontend)

**Tanggal eksekusi:** 2026-10-03
**Mode:** BUILD
**Status dokumen:** `[IN PROGRESS]`
**Kedudukan:** artefak eksekusi turunan. Spec normatif tetap `docs/11 Roadmap.md` Fase 8 dan `docs/10 Implementation Plan.md` §1.1.

## 1. Vonis Awal (Status Gate 2026-10-03)

Task 12 (benchmark 12 kueri) **sudah** lolos 14/14 live dan 333 tests hijau (`reports/fase7_closeout.md` §3). Fase 8 karena itu **bukan fitur baru**, melainkan formalisasi bukti, coverage gate, baseline latensi, audit UI, dan pengecekan 24 item checklist AC yang masih `[ ]` di `docs/04`, `docs/05`, `docs/06`, `docs/07`, dan `docs/12`.

| Prasyarat | Status | Bukti |
|---|---|---|
| Fase 7 close-out | `READY WITH CONDITIONS` | `reports/fase7_closeout.md` §1 |
| Task 12 benchmark | 14/14 live | `reports/fase7_closeout.md` §3 |
| Checklist AC | 0 dari 24 tercentang | `docs/04:471`, `docs/05:312`, `docs/06:280`, `docs/12:240` |
| Coverage gate | belum pernah diukur | `requirements.txt:53` menunda `pytest-cov` ke Fase 8 |
| Baseline latensi | parsial, belum jadi artefak mandiri | `reports/fase7_closeout.md` §5 |
| Task 11 UI | ada di working tree, belum diaudit | `frontend/components/Workspace/` (22 file untracked) |

## 2. Keputusan Owner yang Dipatok (2026-10-03)

| ID | Keputusan | Konsekuensi eksekusi |
|---|---|---|
| **R1** | Migrasi `.env DB_URL` ke role `app_readonly` **TIDAK** dieksekusi agent; cukup prosedur dan verification step di `reports/fase8_signoff.md` sebagai tindakan pemilik | Tindakan DDL `CREATE ROLE app_readonly` + `GRANT SELECT` disiapkan terpisah bila diminta; `.env` tidak disentuh |
| **R2a** | Target `<200ms` nol-bukti **DIMAJUKAN**, bukan lagi deviasi diterima | Wajib R2a.1 (encoder tuning) + parity test; R2a.2 (re-scope per-route) hanya lewat checkpoint user; R2a.3 (pre-probe leksikal) **ditolak di Fase 8** |
| **R2b** | Sintesis LLM sekitar 14,7 detik CPU vs NFR 5-10 detik tetap **deviasi diterima** (butuh GPU, target Fase 10) | Dicatat di baseline latensi, bukan gate |
| **D1** | Sinkronisasi `docs/01` dan `docs/02` **masuk scope Fase 8** | Wajib dikerjakan pada workstream G |

## 3. Workstream A - Baseline Tooling (blocking)

| # | Item | Lokasi |
|---|---|---|
| A1 | Tambah `pytest-cov` ke dependency test (eksplisit ditunda ke Fase 8) | `requirements.txt:53` |
| A2 | Config coverage: `source=backend/app`, gate minimal 80% pada `router` / `retrievers` / `sql_security` / `synthesizer` | `pytest.ini` |
| A3 | `.gitignore` tambah `frontend/coverage/`, `.coverage`, `htmlcov/` karena `frontend/coverage/` saat ini untracked dan akan ikut ter-commit | `.gitignore:47` |

**Verifikasi:** `pytest --collect-only -q` tidak error dan `pytest --cov=backend/app --co` menghasilkan laporan coverage.

## 4. Workstream B - Verification Run (evidence)

| # | Perintah | Target |
|---|---|---|
| B1 | `pytest tests/unit tests/integration` | baseline sekitar 319 hijau satu run |
| B2 | `E2E_LIVE=1 pytest tests/e2e` | 14/14 (butuh DB dan Ollama hidup) |
| B3 | `pytest -v --cov=backend/app --cov-report=term-missing tests/` | tutup gap di bawah 80% pada modul gate dengan unit test terarah |
| B4 | Parity check distribusi embedding fallback `ollama` vs gate 0.65 | sisa `docs/09` TBD-5 |

## 5. Workstream C - R2a: Kejar `<200ms` Nol-Bukti

**Temuan baseline:** `not_found` via `VectorRoute` berbobot 246ms total, dengan 193ms di antaranya `model.encode` bge-m3 CPU pada kueri baru. Sudah ada query-embedding cache (`_QUERY_CACHE`, TTL 3600 detik, 256 entri, `backend/app/services/embedding.py:25-46`) sehingga kueri berulang tidak membayar encode; angka 246ms adalah **cold path**.

### Urutan Risiko Sedikit ke Banyak

| Opsi | Nature | Risiko | Status |
|---|---|---|---|
| **R2a.1** | Tuning encoder: `torch.inference_mode()` plus `set_num_threads` ke vCPU; target encode di bawah 100ms sehingga total sekitar 150ms | **Nol** - wajib dikunci parity test (cosine sekitar 1.0 vs encoder pra-perubahan) | **EKSEKUSI** |
| **R2a.2** | Re-scope `AC-RAG-4` jadi per-route: SQL, Graph, dan Hybrid sudah 119-122ms (hijau), sedangkan `VectorRoute` punya batas bawah terikat encode | Rendah, tapi **mengubah kriteria penerimaan** | **CHECKPOINT USER** bila R2a.1 gagal |
| **R2a.3** | Pre-probe leksikal (`tsvector`/ILIKE) sebelum embedding sehingga `not_found` tanpa encode | **Tinggi** - korpus prototipe hanya 40 chunk dan 20 publikasi sehingga gate leksikal tidak dapat divalidasi; false-negative merusak nilai semantik `VectorRoute` | **DITOLAK di Fase 8**; item FTS Hybrid `docs/11` §5 menjadi Fase 9 |

### Langkah

1. C1 - Profil `generate_query_embedding`: pisahkan amortized model-load vs encode.
2. C2 - Implement R2a.1 plus parity test (nilai vektor sebelum vs sesudah).
3. C3 - Ukur ulang cold dan warm pada kasus nol-bukti.
4. C4 - Bila `<200ms` tercapai, `AC-RAG-4` hijau tanpa perubahan kriteria. Masih di atas 200ms, **berhenti dan minta keputusan** (R2a.2 atau tunda ke Fase 9).

## 6. Workstream D - Baseline Latensi Formal

1. D1 - Catat spek environment: vCPU/RAM, versi Python, region DB dan pooler, versi Ollama plus model.
2. D2 - Breakdown per-route dari `debug` (`developer_mode=true`): `validation_ms`, `routing_ms`, `retrieval_ms`, `unification_ms`, `synthesis_ms`, `verification_ms`, `total_ms`; skenario warm dan cold.
3. D3 - Tulis `reports/fase8_latency_baseline.md`: tabel target vs terukur vs verdict NFR1 (`<15 detik`, `docs/02 SRD.md` NFR1).
4. D4 - Formalisasi deviasi: R1 (prosedur `app_readonly`), R2b (LLM CPU), plus deviasi `AC-RAG-4` bila masih ada.

## 7. Workstream E - Task 11 Frontend (audit, close, commit)

Implementasi sudah ada di working tree: `frontend/components/Workspace/` (sekitar 2.600 baris) plus test vitest (sekitar 900 baris), seluruhnya **untracked**. Audit terhadap `docs/07 Ui Spec.md` §4 sampai §6:

| AC | Komponen | Titik audit |
|---|---|---|
| `AC-UI-1` two-panel dense | `WorkspaceCanvas.tsx`, `Workspace.tsx` | apakah tetap terbaca Notion/Linear, bukan dekoratif |
| `AC-UI-2` evidence cards | `EvidenceCard.tsx` and `ProvenanceRail.tsx` | field `claim`/`metric`/`value`/`period`/`confidence` plus klik untuk highlight sumber |
| `AC-UI-3` route badge | `StateBar.tsx` | 4 route plus indikator `answered_via_fallback` |
| `AC-UI-4` loading | `PipelineTrail.tsx` | tahapan dinamis plus elapsed counter |
| `AC-UI-5` klarifikasi | `ClarifyPanel.tsx` | kartu kandidat entitas interaktif |
| `AC-UI-6` not_found | `NotFoundPanel.tsx` | banner netral, tanpa halusinasi LLM |
| `AC-UI-7` error | `ErrorBoundary.tsx` and `lib/errors.ts` | tanpa raw SQL atau traceback; `sql_executed` hanya Dev-Mode |

Langkah:

1. E1 - Audit 7 AC di atas terhadap kode on-disk.
2. E2 - Tutup gap. Bila implementasi menyimpang dari spec, **update `docs/07`** dan jangan karang desain baru.
3. E3 - `npm run verify` (lint, typecheck, vitest) dan `npm run build`.
4. E4 - Smoke live ke backend untuk 7 status: SQL ok, Vector ok, Graph ok, Hybrid ok, `needs_clarification`, `not_found`, dan error atau 503.
5. E5 - Tulis keputusan `docs/09` TBD-6 (stack CSS dan versi Node) berbasis `frontend/package.json` on-disk.
6. E6 - Commit bercabang `feat/task-11-frontend`, dipecah logis.

## 8. Workstream F - Checklist AC (24 item)

| Kelompok | Lokasi | Metode verifikasi |
|---|---|---|
| `AC-DB-1..9` | `docs/04 Database Schema.md:471` | `python scripts/verify_schema.py` lalu `reports/schema_audit.md` |
| `AC-RAG-1..5` | `docs/05 Retrieval Rag Design.md:312` | nama test dan benchmark E2E |
| `AC-API-1..4` | `docs/06 Api Design.md:280` | integration test endpoint |
| `AC-PIPE-1..6` | `docs/12 Data Pipeline.md:240` | artefak pipeline dan count live DB |
| `AC-UI-1..7` | `docs/07 Ui Spec.md:144` | audit workstream E |

**Aturan pencentangan:** sebuah AC hanya dicentang bila ada pointer bukti konkret (nama test, baris laporan, atau query live). AC yang gagal **tetap dicentang dengan catatan deviasi tertulis**, bukan senyap dihapus atau dipalsukan.

## 9. Workstream G - Rekonsiliasi Dokumen dan Laporan Sign-Off

| # | Item | Lokasi |
|---|---|---|
| G1 | `docs/10` §0 masih kontradiktif sendiri: baris Task 4/5/6/7/8-retriever `[IMPLEMENTED - VERIFICATION PENDING]` vs tracker yang sudah DONE, serta heading Task 12 vs baris matriks | `docs/10:31-37, 42, 349` |
| G2 | `docs/11` baris status Fase 4/5/6 masih `IMPLEMENTED - VERIFICATION PENDING`; label "Fase 8 / Fase 11" keliru karena UI adalah Task 11, bukan Fase 11 | `docs/11:19, 20, 23, 303` |
| G3 | `docs/01` dan `docs/02` masuk scope (keputusan D1): lift status stale dan TBD yang sudah diputuskan | `docs/01`, `docs/02` |
| G4 | Sync marker stale di `docs/03:12`, `docs/05:193`, `docs/06:11`, `docs/07:10`, `docs/09`, dan `docs/12:99` | - |
| G5 | Tulis `reports/fase8_signoff.md`: vonis, angka coverage, tabel latensi, tally AC, deviasi R1 dan R2 | `reports/` |
| G6 | Bump versi `v3.7.2` ke `v3.8.0` (dan minor untuk docs 07, 08, 09) plus baris riwayat di tiap dokumen | `docs/03` sampai `docs/12`, `README.md` |

## 10. Commit Plan (Conventional Commits)

```text
chore(tests): add pytest-cov and coverage gate config
chore(docker): ignore frontend coverage artifacts
perf(embedding): tune cpu encoder for zero-evidence latency gate
test(retriever): close coverage gaps on router, retrievers, security
feat(frontend): pecah logis Task 11
docs: sign off fase 8 MVP gate with coverage and latency evidence
```

## 11. Perintah Verifikasi Akhir

```bash
pytest tests/unit tests/integration
E2E_LIVE=1 pytest tests/e2e
pytest -v --cov=backend/app --cov-report=term-missing tests/
python scripts/verify_schema.py
ruff check backend/ scripts/ tests/
mypy backend/ scripts/
cd frontend && npm run verify && npm run build
```

## 12. Risiko Residual Terdaftar

| ID | Risiko | Mitigasi |
|---|---|---|
| K1 | Working tree dirty luas: 14 file backend dan 20 file frontend termodifikasi, 22 file `Workspace/` untracked | Tidak di-merge tanpa review pemilik; `reports/fase7_closeout.md` §8 item 4 sudah memperingatkan hal ini |
| K2 | Dua test flaky pada full-load live-DB (`filters_ignored`, `zero_match_latency_guard`) | Atribusi pooler Supabase `ConnectionResetError`, bukan regresi; verifikasi isolasi bila muncul ulang |
| K3 | R2a.1 mungkin tidak cukup untuk `<200ms` | Checkpoint user sebelum mengubah kriteria (`AC-RAG-4`); R2a.3 ditolak di Fase 8 |
| K4 | Audit UI mungkin menemukan divergensi spec dan implementasi | `docs/07` di-update sebagai sumber kebenaran; tidak ada desain baru tanpa persetujuan |