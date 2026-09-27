# SRD — System Requirements Document

Status: Draft v2 | Last updated: 2026-09-23 | Architecture-review alignment: 2026-09-27 (docs-only)

> Kontrak API target: `POST /api/v1/ask` (lihat 06 §1). Seluruh requirement di
> bawah berstatus PLANNED / NOT IMPLEMENTED — tidak ada kode terverifikasi di repo.

## 1. Functional Requirements

### FR0 — Input Validation & Request Identity
- FR0.1: `question`: required, string, `min_length = 3`, `max_length = 1000`, trim whitespace.
- FR0.2: Malformed filters ditolak (422), bukan diabaikan diam-diam.
- FR0.3: Server men-generate `request_id` (uuid) per request dan mempropagasikannya
  ke HTTP response, logs, retrieval logs, LLM logs, dan errors.
- FR0.4: Log terstruktur minimum: `timestamp`, `request_id`, `route`, `status`,
  `latency_ms`, `error_code` (+ field NFR4).

### FR1 — Query Input
- FR1.1: Sistem menerima pertanyaan bahasa natural (Indonesia/Inggris campur, sesuai
  data yang juga multilingual) via UI web.
- FR1.2: Sistem tidak mensyaratkan sintaks khusus — user tidak perlu tahu nama tabel/kolom.

### FR2 — Query Routing
- FR2.1: Sistem mengklasifikasikan setiap pertanyaan ke salah satu dari: `structured`
  (butuh SQL/agregasi), `semantic` (butuh vector search), `hybrid` (butuh keduanya,
  misal "paper tentang X yang ditulis institusi Indonesia setelah 2020"), atau
  `relational` (butuh traversal relasi antar-entitas, misal "institusi mana yang
  berkolaborasi dengan peneliti AI?").
- FR2.2: Klasifikasi dilakukan otomatis (lihat 05-retrieval-rag-design.md §2 untuk
  metode), **belum terintegrasi ke backend API** (Task 4, implementation plan).
- FR2.3: Output router berupa typed entity-contract (bukan dict bebas) yang
  divalidasi skema, termasuk operator filter yang di-enumerasi (lihat 05 §2.3).
- FR2.4: Nama author/institusi dari pertanyaan WAJIB melalui entity resolution gate
  (05 §2.4): 0 kandidat → `not_found`; >1 kandidat → `needs_clarification` dengan
  daftar kandidat; 1 kandidat → binding ke id canonical. Tidak ada binding string mentah.

### FR3 — Structured Path (Text-to-SQL)
- FR3.1: Sistem generate SQL SELECT read-only dari pertanyaan + skema database.
- FR3.2: SQL divalidasi (parse, whitelist keyword, whitelist tabel/kolom) sebelum eksekusi.
- FR3.3: SQL yang gagal validasi tidak dieksekusi; sistem retry generate atau fallback
  ke pesan "tidak bisa menjawab pertanyaan ini secara terstruktur".
- FR3.4: Hasil query dibatasi row count. LIMIT 50 wajib disisipkan untuk query
  **non-agregat**; agregat tunggal (`SELECT COUNT(*) ...`) tidak boleh diberi LIMIT
  (limiting sampel lalu melaporkannya sebagai total adalah jawaban salah).
- FR3.5: Pertanyaan dengan maksud aggregat (berapa/jumlah/total/rata-rata/terbanyak)
  WAJIB menghasilkan query yang mengandung fungsi agregat atau `GROUP BY`; listing
  mentah untuk pertanyaan aggregat ditolak validator (05 §3.2 butir 5).
- FR3.6: `COUNT` yang melibatkan join junction table WAJIB `COUNT(DISTINCT
  publication_id)` untuk mencegah double-count akibat fan-out (05 §3.2 butir 6).

### FR4 — Semantic Path (Vector RAG)
- FR4.1: Pertanyaan di-embed dengan model embedding yang sama dengan yang dipakai
  saat indexing `chunks` — **belum terintegrasi; embedding pipeline belum dibuat** (Task 1).
- FR4.2: Similarity search (cosine/pgvector `<=>`) mengembalikan top-K chunks paling relevan — **target pgvector, current SQLite tanpa vector**.
- FR4.3: Chunks dikaitkan kembali ke `publication_id` asal untuk metadata sitasi.
- FR4.4: Hasil vector search di-dedup by `publication_id` SEBELUM LIMIT — `LIMIT K`
  berarti K publikasi unik, bukan K baris chunk (05 §4.1) — **belum terverifikasi karena embedding kosong**.
- FR4.5: Tidak ada hasil di atas threshold → `not_found`, top-K tidak dipaksakan.

### FR5 — Answer Synthesis
- FR5.1: LLM menyusun jawaban natural language dari hasil FR3/FR4/FR7 — tidak
  pernah dari pengetahuan internal model tanpa grounding data.
- FR5.2: Jawaban menyertakan sitasi minimal: judul publikasi, tahun, dan identifier
  (DOI/EID bila tersedia) — konsisten dengan 05 §7.1 dan 06.
- FR5.3: Bila hasil kosong (semua jalur), sistem menjawab eksplisit "tidak ditemukan
  di database", tidak mengarang jawaban plausible; sintesis LLM tidak dipanggil untuk
  kasus 0 rows (05 §7.4).
- FR5.4: Setiap sitasi dalam jawaban diverifikasi pencocokannya terhadap evidence
  yang benar-benar dikirim ke LLM; sitasi tak cocok di-strip dan ditandai
  `unverified_citations` (05 §7.2), tidak diteruskan mentah.

### FR6 — UI Display
- FR6.1: Menampilkan jawaban + daftar sumber terpisah dan jelas (bukan dicampur dalam
  prosa tanpa penanda).
- FR6.2: Menampilkan state loading, error, dan empty-result secara eksplisit
  (lihat 07-ui-spec.md).
- FR6.3 (opsional MVP): menampilkan SQL yang dieksekusi untuk transparansi/debugging
  (mode "developer view").

### FR7 — Relational Path (Knowledge-Graph Minimum Surface)
- FR7.1: Pertanyaan relasional (kolaborasi, co-author, koneksi antar entitas)
  diklasifikasikan ke route `relational` (FR2.1) — **edge tables belum dibuat** (Task 8).
- FR7.2: Retrieval relational dieksekusi lewat **templat traversal terparameterisasi**
  atas edge table `institution_collaboration` / `author_collaboration` (04 §4.2) —
  **belum dibuat**, bukan SQL hasil LLM. Traversal depth di-whitelist (1–3 hop) dan hasil di-LIMIT
  (05 §6.2–6.3).
- FR7.3: Setiap hasil relational membawa provenance `via_publication_ids` yang bisa
  ditelusuri ke publikasi bukti — grounding berlaku sama seperti jalur lain (FR5.1).
- FR7.4: Validasi jalur relational setara jalur structured: timeout, read-only role,
  `search_path` terkunci, entity gate (FR2.4).

## 2. Non-Functional Requirements

### NFR1 — Performance
- Target latency end-to-end per query: < 15 detik pada CPU-only MVP (dicatat sebagai
  batasan yang disengaja, bukan target akhir — lihat roadmap GPU).
- **Asumsi konkurensi (eksplisit, bukan tersirat):** MVP menargetkan 1–2 request
  bersamaan di dalam budget 15 detik pada VM 8 vCPU/16GB (09-tech-stack.md §6).
  Konkurensi yang lebih tinggi dari itu bukan target MVP — efeknya (queueing/backpressure,
  prediksi antrian) dicatat sebagai risiko, bukan janji.
- Embedding indexing berjalan sebagai batch job offline, tidak real-time, sehingga
  tidak terikat target latency interaktif.

### NFR2 — Correctness / Groundedness
- Tidak ada jawaban yang tidak bisa ditelusuri balik ke row/chunk sumber.
- SQL yang dieksekusi harus selalu read-only (`SELECT` only, role DB tanpa hak
  write/DDL — lihat 08-security.md).

### NFR3 — Reliability
- Kegagalan satu jalur (misal SQL generation gagal) tidak boleh membuat seluruh
  request gagal total tanpa pesan — selalu ada fallback response yang informatif.

### NFR4 — Observability (minimal untuk MVP)
- Setiap query dicatat (log) dengan field **konkret, bukan deskripsi kabur**:
  `request_id`, pertanyaan asli, route terpilih, entity-contract hasil router
  (termasuk `filters_ignored`), hasil entity resolution gate (match/multi/not-found
  + kandidat), `answered_via_fallback`, SQL yang dieksekusi (jika ada), similarity
  score hasil, jumlah kandidat sebelum filter dan sesudah filter, threshold hit/miss,
  waktu eksekusi per tahap, dan sukses/gagal.
- Log ini dipakai untuk debugging dan sebagai basis dataset evaluasi di fase pasca-MVP
  (lihat 11-roadmap.md §2.2). Field di atas menjamin log bisa di-replay menjadi
  golden query set — bukan sekadar log keberhasilan.

### NFR5 — Security
- Kredensial DB dan API key tidak pernah ada di kode frontend/client-side.
- Lihat 08-security.md untuk requirement lengkap.

### NFR6 — Maintainability
- Skema database, prompt template, dan router logic didokumentasikan dan versioned
  di repo (bukan hardcode tersebar).
- Perubahan skema tabel harus disertai update ke system prompt text-to-SQL (skema
  adalah sumber kebenaran tunggal — lihat 04-database-schema.md).

## 3. Constraints

- C1: Tidak ada budget GPU untuk fase MVP → LLM dan embedding harus CPU-capable.
- C2: Database sudah ada di Supabase (Postgres + pgvector) — tidak migrasi ke sistem lain.
- C3: 9 tabel sumber + 2 **edge table derivatif** (04 §4.2) dan aturan cleaning yang
  sudah diterapkan adalah given, tidak diubah ulang di fase ini kecuali untuk menambah
  kolom vector/index dan membangun edge table dari junction table yang sudah ada.
- C4: Akses internal-only — tidak perlu compliance/privacy publik tingkat tinggi,
  tapi tetap butuh proteksi credential dasar.
- C5: Frontend Next.js + React, backend perlu bahasa yang punya ekosistem LLM/RAG
  matang (lihat 09-tech-stack.md — direkomendasikan Python untuk backend AI, terpisah
  dari Next.js API routes yang tipis).

## 4. Assumptions

- A1: Volume data (jumlah publikasi/chunks) dalam skala yang masih wajar untuk
  pgvector tanpa index khusus (HNSW/IVFFlat) di awal — akan dievaluasi ulang jika
  jumlah chunks > ~100K.
- A2: Tim pengguna internal tidak akan mencoba secara aktif membobol sistem (bukan
  asumsi keamanan penuh, tetap ada guardrail, tapi threat model bukan adversarial
  publik — lihat 08-security.md untuk batas asumsi ini).
- A3: Bahasa pertanyaan mayoritas Inggris/Indonesia, sesuai bahasa data.
