# PRD — Research Intelligence Assistant (Publications Knowledge Base)

Status: Draft v2 | Owner: Nouval | Last updated: 2026-09-23 | Architecture-review alignment: 2026-09-27 (docs-only, no runtime verification)

## 1. Latar Belakang

Data publikasi ilmiah (11 tabel: 9 relasional — `publications`, `authors`,
`institutions`, `keywords`, `funding`, `publication_references`, `chunks`, + 2
tabel junction — dan 2 **edge table derivatif** `institution_collaboration` /
`author_collaboration`, lihat 04 §4.2) — **data siap dalam database SQLite lokal**.
Saat ini data hanya bisa diakses lewat query SQL manual (tanpa embedding, tanpa edge tables).
Tidak ada cara bagi pengguna non-teknis untuk bertanya dalam bahasa natural dan
dapat jawaban yang akurat, bisa diverifikasi, dan berdasar pada data yang
benar-benar ada di database (bukan halusinasi model) — **fitur ini PLANNED,
belum terintegrasi** (lihat 10-implementation-plan.md untuk urutan build).

**Status infrastruktur:**
- Database: SQLite (9 tabel sumber terload, tanpa kolom embedding, tanpa edge tables)
- pgvector: BELUM terpasang — harus dibuat sebagai ekstensine + kolom `chunks.embedding`
- Edge tables (`institution_collaboration`, `author_collaboration`): BELUM dibuat
  — harus dibangun dari junction table (Task 8, 10-implementation-plan.md)
- Embedding pipeline untuk kolom `chunks`: BELUM ada — blocker keras (Task 1)
  sebelum jalur semantic/hybrid bisa jalan.

## 2. Masalah yang Diselesaikan

- Menjawab pertanyaan agregat/analitik ("siapa 5 author paling produktif tahun 2023?",
  "institusi mana yang paling banyak funding dari NIH?") butuh SQL manual setiap kali.
- Menjawab pertanyaan semantik/eksploratif ("paper apa saja yang membahas stres
  oksidatif pada Wharton's jelly?") butuh full-text/semantic search yang belum ada.
- Tidak ada satu pintu masuk (chat/UI) yang menggabungkan dua kebutuhan itu sekaligus
  dan menjawab dengan grounding ke data asli (bukan jawaban umum dari pengetahuan model).

## 3. Goal (Fase MVP — End-to-End)

**Goal utama: sistem harus jalan end-to-end.** Bukan optimal, bukan lengkap semua
fitur — tapi rantai penuh dari pertanyaan pengguna sampai jawaban yang benar dan
grounded harus berfungsi, bisa didemokan, dan bisa dipakai tim internal setiap hari.

Definisi "jalan end to end" untuk MVP ini (target architecture, not yet implemented):

1. User mengetik pertanyaan bahasa natural di UI web (Next.js).
2. Sistem backend menentukan apakah pertanyaan butuh query terstruktur (SQL)
   atau pencarian semantik (vector), atau keduanya — **router belum terintegrasi**.
3. Query dieksekusi ke PostgreSQL/pgvector (target) atau SQLite (current —
   belum ada pgvector/embedding) — **retrieval belum terintegrasi**.
4. Hasil (rows / chunks) disusun jadi jawaban natural language oleh LLM, dengan
   sitasi ke publikasi asal (judul, tahun, DOI) — bukan jawaban tanpa sumber.
5. Jawaban + sumber ditampilkan di UI dengan rapi, termasuk saat data kosong/tidak
   relevan (tidak mengarang jawaban).

## 4. Target Pengguna

Internal only untuk MVP — tim riset/analis kecil yang sudah dipercaya (lihat 08-security.md
untuk konsekuensi pilihan akses ini). Bukan produk publik, belum butuh multi-tenant
auth yang kompleks.

## 5. Scope MVP (In)

- Chat UI satu halaman (single-turn atau multi-turn ringan, tanpa riwayat kompleks).
- **Target architecture**: hybrid retrieval — text-to-SQL untuk query terstruktur/agregat,
  vector search (target: pgvector + kolom `chunks.embedding`) untuk query semantik,
  gabungan keduanya untuk query campuran, dan relational (target: edge tables
  institution_collaboration / author_collaboration) untuk pertanyaan kolaborasi
  antar entitas — keputusan P0.1: knowledge graph in-scope MVP dalam bentuk minimum,
  namun **belum dibangun** (lihat 04 §4.2 / 10-implementation-plan.md Task 8).
- **Embedding pipeline untuk kolom `chunks`**: BELUM Ada — ini blocker keras
  (lihat 10-implementation-plan.md Task 1). **Sebelum task ini selesai, jalur
  semantic dan hybrid tidak berfungsi sama sekali.**
- Router: desain LLM-based (Qwen2.5-Coder-7B) dengan **entity-contract typed**
  dan **entity resolution gate** untuk memilih jalur SQL vs vector vs relational
  vs gabungan terstruktur+semantik — **design sudah ada tapi belum diintegrasi ke
  backend API** (lihat 10-implementation-plan.md Task 4).
- Jawaban dengan sitasi (judul publikasi, tahun, author, DOI bila ada).
- Read-only DB access — sistem tidak pernah menulis/mengubah data lewat chat.
- LLM & embedding self-hosted, CPU-only (Ollama + Qwen2.5-Coder-7B, embedding target
  BAAI/bge-m3, 1024 dims) — lihat 09-tech-stack.md untuk rationale dan trade-off performa.
  **Catatan**: embedding model dan diintegrasi BELUM terintegrasi (config.py
  masih menggunakan all-MiniLM-L6-v2, lihat 09-tech-stack.md untuk target).

## 6. Scope MVP (Out — Deferred ke Fase 2+)

- Fuzzy entity resolution lanjutan untuk author/institution (**alias semantik** —
  lebih dari sekadar lower+trim `*_normalized` dan lebih dari gate exact/substring
  yang sudah ada di MVP). Entity gate minimum (0/multi/1 kandidat + `needs_clarification`)
  ada di MVP (05 §2.4); fuzzy matching "MIT" vs "Massachusetts Institute of
  Technology" tetap di-defer ke roadmap §2.1, di-flag sebagai risiko kualitas agregasi.
- Multi-user auth, row-level security, rate limiting per user.
- GPU-backed model yang lebih besar/cepat.
- Riwayat percakapan persisten, multi-session, sharing hasil.
- Evaluasi otomatis (golden query set, regression testing jawaban).
- Ingestion pipeline otomatis untuk publikasi baru (saat ini data statis, load sekali).

## 7. Success Metrics (MVP)

Kualitatif dulu karena ini fase "jalan", bukan fase "optimal":
- Pertanyaan agregat sederhana (top N by count/year/institution) → jawaban benar,
  diverifikasi manual terhadap query SQL langsung.
- Pertanyaan semantik ("paper tentang X") → mengembalikan publikasi yang secara
  topikal relevan (judul relevance check manual terhadap top-5 hasil).
- Pertanyaan relational sederhana (kolaborasi institusi, co-author) → jawaban
  konsisten dengan edge table (dicek manual), membawa provenance publikasi.
- Entitas ambigu ("j. wang") → sistem meminta klarifikasi/`needs_clarification`
  atau menyebut ambiguitas eksplisit, bukan memilih kandidat secara diam-diam.
- Setiap sitasi dalam jawaban cocok dengan sources di response (citation verifier
  lolos) — tidak ada [judul, tahun] karangan.
- Sistem tidak pernah mengeksekusi SQL destruktif (DROP/DELETE/UPDATE/INSERT) —
  ini hard requirement, bukan target, lihat 08-security.md.
- Sistem menyatakan "tidak ditemukan" ketika data memang tidak ada, bukan mengarang.
- End-to-end latency per pertanyaan dalam rentang wajar untuk internal tool (target
  kasar: <15s untuk CPU-only MVP; dicatat sebagai batasan yang diketahui, bukan bug).

## 8. Risiko Utama (lihat detail di dokumen terkait)

| Risiko | Dampak | Mitigasi awal |
|---|---|---|
| Text-to-SQL salah generate kolom/tabel (halusinasi skema) | Jawaban salah/error | Prompt dengan skema eksplisit + validasi SQL sebelum eksekusi (05, 08) |
| Model CPU-only lambat/kurang akurat | UX lambat, SQL error rate lebih tinggi | Model kecil terbaik di kelasnya (Qwen2.5-Coder-7B), guardrail retry (09, 05) |
| Agregasi author/institution pecah karena variasi nama | Angka top-N salah | Didokumentasikan sebagai known limitation, fuzzy resolution di-defer (04, 11) |
| Kolom `chunks` belum punya embedding | **Vector search tidak berfungsi sama sekali** (bukan hanya risk, tapi blocker
   saat ini) — Task #0 wajib di implementation plan sebelum fitur lain (10) |
| SQL injection via prompt injection | Kebocoran/kerusakan data | Read-only DB role + SQL allowlist parser (08) |

## 9. Dependensi Dokumen Lain

- Requirement detail teknis → `02-SRD.md`
- Arsitektur & alur data → `03-system-architecture.md`
- Skema final → `04-database-schema.md`
- Desain retrieval hybrid → `05-retrieval-rag-design.md`
- Kontrak API → `06-api-design.md`
- Spesifikasi UI → `07-ui-spec.md`
- Keamanan → `08-security.md`
- Stack & rationale → `09-tech-stack.md`
- Urutan build → `10-implementation-plan.md`
- Roadmap pasca-MVP → `11-roadmap.md`
