# Tech Stack — Rekomendasi & Rationale

Status: Draft v2 | Last updated: 2026-09-23 | Architecture-review alignment: 2026-09-27 (docs-only)

Semua pilihan di sini dikunci oleh keputusan yang sudah diambil di sesi planning:
hybrid retrieval, self-hosted CPU-only model, frontend Next.js, akses internal-only.
Dokumen ini menjelaskan komponen konkret dan kenapa, termasuk trade-off yang
disadari (bukan pilihan "terbaik absolut" — pilihan yang realistis untuk constraint
yang ada).

## 1. Ringkasan Stack

| Layer | Pilihan | Alasan singkat |
|---|---|---|
| Database | PostgreSQL (Supabase) + pgvector | Sudah ada, sudah connect, tidak perlu migrasi |
| Backend | Python + FastAPI | Ekosistem RAG/LLM/SQL-validation paling matang |
| LLM (self-hosted) | Qwen2.5-Coder-7B-Instruct (GGUF Q4_K_M) via Ollama | Terbaik di kelas ukuran ini untuk SQL generation, jalan di CPU |
| Embedding | BAAI/bge-m3 (revision di-pin, lihat §3) | Multilingual (EN/ID), kualitas retrieval terbaik di kelasnya |
| SQL validation | `sqlglot` | Parser SQL Python yang robust untuk AST-level whitelist check |
| Frontend | Next.js (React) | Keputusan sudah diambil; fit natural dengan Vercel + Supabase |
| Frontend deploy | Vercel | Native untuk Next.js |
| Backend deploy | Single VM/VPS (Docker Compose) | Tidak perlu orkestrasi kompleks untuk MVP single-tenant |

## 2. LLM: Qwen2.5-Coder-7B-Instruct — Kenapa Ini, Bukan yang Lain

Constraint keras: CPU-only, tanpa budget GPU (keputusan eksplisit dari planning).
Ini membatasi pilihan model secara drastis — model 70B+ (Llama 3.1 70B, dst) tidak
realistis dijalankan dengan latency yang masih bisa dipakai untuk chat interaktif
di CPU biasa.

Di kelas model kecil (7-8B) yang CPU-viable:
- **Qwen2.5-Coder-7B-Instruct** dipilih karena dioptimalkan khusus untuk
  code/SQL generation — lebih kuat dari model general-purpose ukuran sama untuk
  tugas text-to-SQL, yang merupakan komponen paling berisiko-gagal di sistem ini.
- Alternatif yang dipertimbangkan dan ditolak:
  - **Llama 3.1 8B**: general-purpose, SQL generation lebih lemah dari Qwen-Coder
    di benchmark yang setara.
  - **Mistral 7B**: serupa, tidak sekuat Qwen-Coder untuk structured output/SQL.
  - **Phi-3/Phi-4 mini**: lebih ringan tapi konsistensi format output (JSON untuk
    router, SQL untuk generator) kurang reliable di pengalaman lapangan dibanding
    Qwen-Coder.

**Trade-off yang harus diterima, bukan diabaikan:**
- Latency per LLM call di CPU: kasarnya beberapa detik untuk prompt dengan skema
  penuh + beberapa ratus token output. Dengan 2 LLM call per request (router +
  SQL/synthesis, kadang 3 kalau hybrid), total end-to-end bisa mendekati batas
  15 detik di NFR1 — ini alasan kenapa UI loading state (07-ui-spec.md §3.1) harus
  serius menangani rentang waktu ini, bukan asumsi instant response.
- SQL generation dari model 7B punya error rate lebih tinggi dari model besar —
  ini alasan validasi berlapis di 08-security.md wajib ada, bukan nice-to-have.

**Serving via Ollama** (bukan raw `llama.cpp` manual, bukan vLLM): Ollama dipilih
karena kemudahan setup (satu binary, model pull dari registry), API HTTP yang
konsisten, dan dukungan quantization GGUF built-in. vLLM lebih optimal untuk
throughput tinggi tapi target use-case-nya GPU serving — tidak relevan untuk
CPU single-user internal tool ini.

## 3. Embedding: bge-m3 — Kenapa

- **Multilingual** — kritikal karena data (author/institution names, sebagian
  abstract) campur bahasa Inggris dan Indonesia; model embedding English-only
  (misal `all-MiniLM-L6-v2`) akan berkualitas rendah untuk teks Indonesia.
- Termasuk model embedding kualitas terbaik yang tersedia open-source saat ini,
  dan cukup ringan untuk dijalankan CPU untuk keperluan **batch indexing offline**
  (tidak time-critical) maupun **embed query online** (satu string pendek per
  request, jauh lebih ringan dari LLM generation).
- Dimensi output 1024 — dicatat di 04-database-schema.md untuk definisi kolom
  `vector(1024)`.
- **Versi di-pin (reproducibility, bukan tersirat):** commit/revision model
  `BAAI/bge-m3` dan versi `sentence-transformers` dicatat eksplisit di
  `requirements-dev` / Docker image saat setup Task 1 (10-implementation-plan.md).
  Alasan: hasil embedding harus deterministik antar re-run — tanpa pin, update
  library bisa mengubah nilai embedding secara halus dan diam-diam mengubah
  ranking retrieval.
- Batch size embedding di-pin juga (32-64) dan dicatat, karena ukuran batch
  memengaruhi hasil (~ tidak identik) pada model tertentu dan dipakai untuk
  reproducibility index.

## 4. Backend: Python/FastAPI — Kenapa Terpisah dari Next.js

Dijelaskan juga di 03-system-architecture.md §5. Ringkas: ekosistem SQL parsing
(`sqlglot`), embedding (`sentence-transformers`), dan orchestration LLM jauh lebih
matang di Python. FastAPI dipilih dibanding Flask/Django karena async-native
(penting untuk I/O-bound calls ke Ollama dan Postgres) dan validasi request/response
otomatis lewat Pydantic — cocok dengan kontrak API di 06-api-design.md yang butuh
schema ketat (JSON response terstruktur).

## 5. Kenapa Bukan Framework RAG Siap Pakai (LangChain/LlamaIndex)

Dipertimbangkan tapi **tidak direkomendasikan sebagai dependency wajib** untuk
MVP ini: abstraksi framework tersebut menambah lapisan indirection yang menyulitkan
debug saat model kecil (7B CPU) sudah punya failure rate lebih tinggi dari model
besar yang biasanya jadi target design framework tersebut. Untuk MVP dengan pipeline
yang cukup dijelaskan (router → SQL/vector → synthesis, tiga prompt eksplisit),
memanggil Ollama langsung lewat HTTP client dan pgvector langsung lewat SQL lebih
mudah dipahami, di-debug, dan dikontrol perilakunya secara presisi (terutama untuk
validasi SQL yang harus custom, bukan generic). Ini bisa direvisit di fase 2 kalau
kompleksitas orchestration bertambah signifikan.

## 6. Local Dev & Deployment

- **Dev lokal**: Docker Compose dengan service `backend`, `ollama` (model di-pull
  saat build/first-run), koneksi ke Supabase (bukan local Postgres, supaya dev
  selalu tersinkron dengan data real).
- **Production MVP**: satu VPS (spesifikasi minimum disarankan: 8 vCPU, 16GB+ RAM
  — model 7B quantized butuh sekitar 5-6GB RAM untuk load, sisanya untuk headroom
  request concurrent dan embedding model) menjalankan Docker Compose yang sama.
  Provider spesifik tidak dikunci di dokumen ini (Hetzner/DigitalOcean/dst semua
  viable untuk CPU compute biasa) — keputusan provider bisa berdasar harga saat
  provisioning.
- **Asumsi konkurensi (eksplisit, sinkron NFR1 di 02-srd.md):** VPS 8 vCPU/16GB ini
  didesain untuk **1-2 request bersamaan dalam budget latency 15 detik**. Angka
  headroom di atas dipilih dengan asumsi ini; konkurensi lebih tinggi dari itu
  (queueing, underpredict backpressure) adalah **di luar target MVP**, bukan bug —
  diukur saat Task 12 baseline dan dipakai sebagai input roadmap Fase 3 (GPU).
- **Frontend**: Vercel, environment variable menunjuk ke URL backend API.

## 7. Upgrade Path ke GPU (Fase 2 — dicatat di roadmap)

Karena semua model dipanggil lewat Ollama dengan interface HTTP yang sama, upgrade
ke model lebih besar (misal Qwen2.5-Coder-32B atau model 70B) saat budget GPU
tersedia **tidak mengubah arsitektur** — hanya ganti nama model yang di-pull dan
di-serve, dan pindahkan Ollama ke instance dengan GPU. Backend, API contract, dan
frontend tidak perlu berubah. Ini keuntungan konkret dari keputusan self-hosted via
Ollama dibanding hard-code ke SDK vendor tertentu.

## 8. Graph Technology Decision (normatif, Decision: Pending)

Inspeksi repositori 2026-09-27: **tidak ada graph backend yang terdeteksi**
(tidak ada kode, skema graph mandiri, atau dependensi graph). Minimum surface
saat ini = edge tables Postgres (§4.2 di 04 — desain ada, tabel belum dibuat).

```text
Decision: Pending
Reason: insufficient repository/infrastructure evidence
Candidates: Apache AGE, Kùzu
Blocking decision: graph backend selection
Next decision required: pilih AGE vs Kùzu; catat reason, integration method,
  local development setup, data synchronization approach, dan current status
```

Neo4j/Memgraph TIDAK diperkenalkan sebagai kandidat tanpa bukti repositori.
Roadmap Fase 2 (11 §2.4) mengevaluasi apakah edge-table + recursive CTE cukup
atau graph DB benar-benar dibutuhkan — berdasar benchmark, bukan asumsi.
