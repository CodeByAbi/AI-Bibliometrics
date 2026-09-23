# AI-Bibliometrics — Research Intelligence Assistant

Sistem tanya-jawab bahasa natural di atas database 9 tabel publikasi ilmiah
(PostgreSQL / Supabase + pgvector). Pengguna non-teknis cukup bertanya dalam
bahasa natural — sistem menentukan apakah butuh query struktur (SQL), pencarian
semantik (vector), atau keduanya, lalu menjawab dengan **grounding ke data asli**
beserta sitasi publikasi — bukan halusinasi model.

> Status: **Planning Draft v1** (belum ada kode) · Last updated: 2026-09-21

## Masalah yang Diselesaikan

1. **Query agregat/analitik** ("siapa 5 author paling produktif tahun 2023?", "institusi mana yang paling banyak funding dari NIH?") — sebelumnya butuh SQL manual setiap kali.
2. **Query semantik/eksploratif** ("paper tentang stres oksidatif pada Wharton's jelly?") — butuh semantic search yang belum ada.
3. Tidak ada satu pintu masuk (chat UI) yang menggabungkan keduanya dan menjawab dengan grounding ke data asli.

## Keputusan Kunci (Hasil Planning)

| Area | Keputusan |
|---|---|
| Retrieval | **Hybrid** — text-to-SQL untuk query terstruktur/agregat, vector search (pgvector) untuk query semantik, gabungan keduanya untuk query campuran |
| Model | Self-hosted, **CPU-only** (tanpa budget GPU): `Qwen2.5-Coder-7B-Instruct` via Ollama untuk generasi, `BAAI/bge-m3` untuk embedding |
| Backend | Python + **FastAPI** (terpisah dari frontend) — ekosistem RAG/LLM/validasi SQL paling matang |
| Frontend | **Next.js / React** (clean white, dense, gaya Notion/Linear) |
| Database | Postgres + pgvector di **Supabase** (sudah ada, 9 tabel sudah dimuat & di-clean) |
| Akses | **Internal-only** untuk MVP — tim kecil terpercaya, bukan produk publik |

## Arsitektur (ringkas)

```
Next.js UI ──HTTPS──▶ Backend API (FastAPI)
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
  Router/Planner     Ollama (Qwen2.5-    Embedding svc
  (LLM call)         Coder-7B, local)    (bge-m3, local)
        │                 │                 │
        ▼                 ▼                 ▼
      Postgres (Supabase) — 9 tabel relasional + pgvector pada `chunks`
```

Semua panggilan model terjadi di backend — frontend tidak pernah bicara langsung
ke Ollama atau database.

**Pipeline pertanyaan:** routing (LLM klasifikasi) → text-to-SQL / vector RAG /
hybrid (template terparameterisasi) → validasi berlapis → eksekusi read-only →
answer synthesis dengan grounding + sitasi. Detail lengkap:
[`docs/05 Retrieval Rag Design.md`](docs/05%20Retrieval%20Rag%20Design.md).

## Tech Stack

| Layer | Pilihan |
|---|---|
| Database | PostgreSQL (Supabase) + pgvector |
| Backend | Python + FastAPI |
| LLM | Qwen2.5-Coder-7B-Instruct (GGUF Q4_K_M) via Ollama |
| Embedding | BAAI/bge-m3 (multilingual EN/ID, 1024 dimensi) |
| SQL validation | `sqlglot` (AST-level whitelist check) |
| Frontend | Next.js (React) — deploy ke Vercel |
| Backend deploy | Single VM/VPS, Docker Compose |

Rationale, perbandingan alternatif, dan trade-off setiap pilihan:
[`docs/09 Tech Stack.md`](docs/09%20Tech%20Stack.md).

## Status Implementasi & Blocker Kritis

- Database **sudah live** di Supabase, 9 tabel sudah dimuat, cleaning sudah diterapkan.
- **Blocker keras:** kolom vector `chunks.embedding` **masih kosong** — tanpa ini
  jalur semantic/hybrid tidak berfungsi sama sekali (Task 1 di implementation plan).
- **Task 0 wajib lebih dulu:** skema di dokumen adalah rekonstruksi dari deskripsi
  cleaning, belum diverifikasi terhadap `information_schema` asli — skema yang salah
  di system prompt text-to-SQL adalah penyebab paling umum kegagalan.
- Urutan build lengkap (Task 0–11 hingga end-to-end verification):
  [`docs/10 Implementation Plan.md`](docs/10%20Implementation%20Plan.md).

## Struktur Repo

```
AI-Bibliometrics/
├── README.md          ← dokumen ini
├── docs/              ← seluruh dokumentasi planning
│   ├── 01 Prd.md                 # Produk: kenapa, scope in/out, success metrics
│   ├── 02 Srd.md                 # Requirements fungsional & non-fungsional
│   ├── 03 System Architecture.md # Komponen & alur data end-to-end
│   ├── 04 Database Schema.md     # Skema 9 tabel + aturan cleaning (acuan SQL prompt)
│   ├── 05 Retrieval Rag Design.md# Inti teknis: router, text-to-SQL, vector RAG, hybrid
│   ├── 06 Api Design.md          # Kontrak REST frontend ↔ backend
│   ├── 07 Ui Spec.md             # Spesifikasi UI anti-slop (state wajib, token desain)
│   ├── 08 Security.md            # Read-only DB role, validasi SQL berlapis
│   ├── 09 Tech Stack.md          # Stack final + rationale + trade-off
│   ├── 10 Implementation Plan.md # Urutan build Task 0–11
│   └── 11 Roadmap.md             # Yang sengaja di-defer dari MVP
└── (src/)             # belum ada — repositori belum berisi kode
```

Urutan baca disarankan: dokumen `01` → `11` (`docs/00 Readme.md` berisi ringkasan
dan urutan baca).

## Keamanan (prinsip inti)

- Backend **tidak pernah** connect ke Postgres dengan role admin — pakai role
  `app_readonly` (SELECT only) sebagai fallback fisik jika validasi SQL bobol.
- SQL hasil LLM selalu melewati **validasi berlapis** (parse `sqlglot` → cek
  statement SELECT → whitelist tabel/kolom → blacklist keyword destruktif → LIMIT
  enforce); guardrail ada di **kode, bukan di prompt semata**.
- Kredensial DB hanya di environment variable backend; frontend hanya tahu URL API.
- Ollama berjalan di jaringan internal (tidak expose ke publik).

Detail: [`docs/08 Security.md`](docs/08%20Security.md).

## Roadmap Pasca-MVP

Fase 2 — fuzzy entity resolution (rapidfuzz + alias mapping), eval harness (golden
query set + regression test). Fase 3 — upgrade GPU + model lebih besar (tanpa
perubahan arsitektur). Fase 4 — multi-user/auth bila akses meluas. Fase 5 —
ingestion publikasi baru. Fase 6 — multi-turn context:
[`docs/11 Roadmap.md`](docs/11%20Roadmap.md).

## Lisensi

Belum ditentukan.