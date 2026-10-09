# Latensi LLM — Fase 2: Verifikasi Pasca-Implementasi

**Tanggal:** 2026-10-08 · **Branch:** `research/llm-latency`
**Lanjutan dari:** `reports/llm-latency-research.md` (DIBEKUKAN — jangan edit;
semua entri baru masuk file ini).
**Target (dikunci user):** semantic < 1 dtk, hybrid < 0,5 dtk, sintesis < 60 dtk,
jawaban tetap tidak halu (0 sitasi tak terverifikasi).

## F2.1 Env test + sweep penuh

* Install: `psycopg[binary]==3.3.6` (sesuai `requirements-dev.txt:22`),
  lalu `prometheus_client`, `networkx`, `scipy` yang kurang satu per satu
  saat koleksi (`test_synthesis_observability`, `test_visualize_graph`).
* **Hasil: `tests/unit` 649 passed** (dengan conftest normal).
* Pelajaran: 2 kegagalan awal adalah artefak harness, bukan regresi —
  (a) observability gagal karena run `--noconftest` mematikan fixture reset
  stats global; hijau (19 passed) dengan conftest; (b) visualize butuh
  `scipy` (dependensi `requirements-dev`, belum di `.venv`).

## F2.2 Cooldown-ukur vs target (sistem sepi, pull selesai)

| Target | Hasil | Verdict |
|---|---|---|
| Semantic < 1 dtk | `embedding_ms` **13,5 / 20,2 / 23,1 dtk** (2 sesi ukur, backend `local`) | TIDAK TERCAPAI |
| Hybrid < 0,5 dtk | COMBINED `hybrid_retrieval_ms` **189 ms** (tanpa embedding); dengan embedding ikut varians encode | TERCAPAI (jalur non-embedding) |
| Sintesis < 60 dtk | `llm_synthesis_ms` **120,1 dtk → timeout → fallback** | TIDAK TERCAPAI |
| Anti-halu | `unverified_citations == []` di semua respons ukur; gate & verifier tak tersentuh fix mana pun | TERJAGA |

## F2.3 Temuan akar: host undersized (bukan kode)

* Host: **Intel i3-10110U, 2 core** (4 thread HT), load **77%** saat ukur.
  Klaim "150–450 ms warm" di `embedding.py:28` berasal dari hardware lain —
  di box ini lantai encode settled ±0,6–1,2 dtk dan spike 13–23 dtk saat
  host sibuk. Varians 40× antar sesi = bukti kontensi, bukan bug.
* Konsekuensi OMP: `OMP_NUM_THREADS=4` regresi (lapor §8); revert ke 1
  sudah benar untuk host 2-core berbagi dengan Ollama 3,9 GB resident.
* Sintesis: 1,37 tok/s terukur → 128 token butuh ±93 dtk + prompt eval —
  target < 60 dtk **mustahil** di box ini tanpa (a) model ≤ 1,5B,
  (b) GPU, atau (c) API hosted. Bukan kegagalan implementasi P5.

## F2.4 Keputusan yang tersisa untuk user

1. Terima lantai hardware ini (semantic ±1–2 dtk settled, sintesis
   fallback-dominated) atau pilih (a)/(b)/(c) di atas.
2. PR ke `develop` via cherry-pick 6 commit fix (branch
   `fix/llm-latency-p1-p7`); logo PR #19 + merge main disengaja
   tidak dibawa.
3. Streaming SSE (butuh frontend) — ya/tidak.

## F2.5 Verifikasi tambahan (2026-10-09, branch `fix/llm-latency-p1-p7`)

* Prewarm SEHAT: health `:8000` → `local_model_state=loaded`;
  Qwen 7B resident; `hf-cache` utuh; `EMBEDDING_PREWARM` default-true.
* Gate-label sinkron: `ab840c7` (0.65 → 0.48, 7 baris) + verify trio
  hijau (lint, typecheck, 218/218 vitest).
* Timeout Q04 terbukti cold-vs-abort (8 dtk vs cold 10–20 dtk);
  retry teks identik = cache-hit. Varian 25-dtk ditolak (merusak
  desain 503 terstruktur); pilihan = prewarm saja.
* Anti-halu tetap terjaga: tidak ada fix menyentuh verifier;
  `synthesis.fallback_rate=1.0` (2 timeout + 1 unreachable) =
  fallback bekerja sesuai desain.
* Verdict F2.2 tidak berubah: target non-embedding tercapai;
  encode + sintesis menunggu keputusan hardware/model (F2.4 #1).
