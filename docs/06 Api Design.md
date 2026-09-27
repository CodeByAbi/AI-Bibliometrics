# API Design — Frontend ↔ Backend Contract

Status: Draft v2 | Last updated: 2026-09-23 | Architecture-review alignment: 2026-09-27 (docs-only)

## 0. Kontrak Target vs Kontrak Terdokumentasi v2

Kontrak target normatif: **`/api/v1`**. Kontrak `POST /api/query` di bawah
(§2–§5) adalah **desain v2 yang sudah ada, berstatus PLANNED dan SUPERSEDED** —
didokumentasikan sebagai referensi historis, bukan kontrak hidup kedua.
Tidak ada kode API di repositori (NOT IMPLEMENTED); tidak ada kontrak
`/api/query` yang berjalan untuk ditandai legacy-runtime. Implementasi wajib
mengikuti §1 (v1) dan memigrasikan field v2 yang masih valid
(`filters_ignored`, `answered_via_fallback`, `unverified_citations`,
`needs_clarification`) ke skema v1.

## 1. Target v1 Contract (normatif)

Base: **`/api/v1`**. Endpoint primer dan health:

```http
POST /api/v1/ask
GET /api/v1/health
```

Endpoint future (PLANNED, NOT IMPLEMENTED):

```http
POST /api/v1/ask/stream
GET /api/v1/papers/{publication_id}
GET /api/v1/authors/{author_id}
GET /api/v1/institutions/{institution_id}
GET /api/v1/graph/subgraph
```

### Request — `POST /api/v1/ask`

```json
{
  "question": "What are the main research topics related to generative AI?",
  "filters": {
    "year_start": 2020,
    "year_end": 2025
  }
}
```

Validasi (Pydantic, di boundary):

```text
question: required, string, min_length = 3, max_length = 1000, trim whitespace
filters: optional; malformed filters → 422 (rejected, bukan diabaikan diam-diam)
```

### Response — sukses (stabil)

```json
{
  "request_id": "uuid",
  "status": "ok",
  "route": "semantic",
  "answer": "...",
  "sources": [
    {
      "source_id": "...",
      "publication_id": "...",
      "title": "...",
      "year": 2024,
      "doi": "...",
      "snippet": "..."
    }
  ]
}
```

### Response — tidak ditemukan

```json
{
  "request_id": "uuid",
  "status": "not_found",
  "route": "semantic",
  "answer": "No matching evidence was found in the database.",
  "sources": []
}
```

Status yang didukung mengikuti implementasi aktual (saat ini NOT
IMPLEMENTED): `ok`, `not_found` (`insufficient_evidence` sebagai varian
yang diizinkan bila dikontrakkan), `needs_clarification` (warisan v2, §2),
`error` (+ `error_type`). HTTP: `200` untuk ok/not_found/needs_clarification;
`422` validasi/generation; `500` internal; `503` LLM/embedding unavailable/timeout.

### Observability — `request_id`

`request_id` di-generate server per request dan dipropagasikan ke: HTTP
response, logs, retrieval logs, LLM logs, errors. Field log terstruktur
minimum: `timestamp`, `request_id`, `route`, `status`, `latency_ms`,
`error_code` (+ field NFR4 di 02: entity-contract, hasil gate, fallback flag,
SQL, similarity, threshold hit/miss, latency per tahap). Metrik agregat yang
belum ada = PLANNED, bukan implemented.

### Health — `GET /api/v1/health`

Dependency checks (target): PostgreSQL, pgvector, LLM, embedding model,
graph store. Respons aman: tidak pernah memaparkan password, API keys,
connection strings, atau internal filesystem paths.

## 2. `POST /api/query` — DESAIN v2 (SUPERSEDED, referensi historis)

> Status: PLANNED di v2, **superseded oleh §1**. Jangan diimplementasikan
> sebagai kontrak kedua.

### Prinsip (v2 asli)

Satu endpoint utama untuk MVP. Tidak perlu REST resource sprawling — ini bukan CRUD
app, ini satu use case (tanya → jawab). Backend FastAPI, response JSON (streaming
opsional untuk fase 2, MVP pakai response penuh setelah selesai supaya lebih
sederhana untuk didebug end-to-end dulu).

### Request (v2)
```json
{
  "question": "Siapa 5 penulis paling produktif tahun 2023?",
  "developer_mode": false
}
```
- `question` (string, required): pertanyaan bahasa natural.
- `developer_mode` (bool, optional, default false): jika true, response menyertakan
  SQL yang dieksekusi dan route yang dipilih (untuk debugging/transparansi UI,
  lihat 07-ui-spec.md).

### Response — Sukses
```json
{
  "status": "ok",
  "route": "structured",
  "answer": "5 penulis paling produktif tahun 2023 adalah: 1. Jane Doe (12 publikasi)...",
  "sources": [
    {
      "publication_id": "pub_00123",
      "title": "Oxidative Stress in Wharton's Jelly-Derived Cells",
      "year": 2023,
      "doi": "10.1016/xxxx",
      "similarity": null
    }
  ],
  "filters_ignored": [],
  "ambiguous_entity": false,
  "answered_via_fallback": false,
  "unverified_citations": [],
  "debug": {
    "sql_executed": "SELECT ... LIMIT 50",
    "route_reasoning": "Pertanyaan meminta ranking/agregasi berbasis count",
    "latency_ms": 8421
  }
}
```
- `route`: enum `structured` | `semantic` | `hybrid` | `relational` (jalur relational
  adalah jalur ke-4, knowledge-graph minimum surface — lihat 05 §6).
- `sources`: array, bisa kosong jika jalur structured murni (agregasi tanpa
  publikasi individual sebagai sumber langsung — di kasus ini `sql_executed` di
  debug berfungsi sebagai "sumber").
- `similarity`: hanya terisi untuk hasil dari jalur semantic/hybrid, `null` untuk
  hasil structured/relational murni.
- `filters_ignored`: field entity yang di-ekstrak router tapi tidak punya binding
  slot di route terpilih (lihat 05 §2.6). Tidak pernah di-drop diam-diam — dikirim
  ke LLM synthesis supaya jawaban menyebutkan keterbatasan.
- `ambiguous_entity`: `true` hanya untuk respons `needs_clarification`
  (lihat di bawah). Nilai `false` pada respons ok/not_found.
- `answered_via_fallback`: `true` jika route dijatuhkan ke fallback `semantic`
  karena parse/validasi router gagal (05 §2.5) — supaya log tidak memperlakukan
  tebakan fallback sebagai route yang benar.
- `unverified_citations`: daftar sitasi yang di-strip citation verifier dari jawaban
  karena tidak cocok dengan evidence yang benar-benar dikirim ke LLM (05 §7.2).
- `debug`: hanya muncul jika `developer_mode: true`.

### Response — Tidak Ditemukan
```json
{
  "status": "not_found",
  "route": "semantic",
  "answer": "Tidak ditemukan publikasi yang relevan dengan pertanyaan ini di database.",
  "sources": [],
  "filters_ignored": [],
  "ambiguous_entity": false,
  "answered_via_fallback": false,
  "unverified_citations": [],
  "debug": null
}
```
Status `not_found` dipakai untuk: tidak ada hasil di atas threshold (semantic),
SQL valid dengan 0 rows (structured, 05 §3.4), traversal kosong (relational,
05 §6.3), dan **entitas tidak dikenal** oleh entity resolution gate (05 §2.4 butir 3).
Utuh dari v1: dipisah dari `ok` supaya frontend menampilkan state berbeda visual
(lihat 07-ui-spec.md) — bukan dianggap error, ini jawaban valid yang jujur.

### Response — Perlu Klarifikasi (ambiguitas entitas)
```json
{
  "status": "needs_clarification",
  "route": "hybrid",
  "answer": "Terdapat beberapa entitas yang cocok dengan 'j. wang'. Mana yang dimaksud?",
  "candidates": [
    {"entity_id": "auth_001", "name": "Wang, J.", "display_name": "...", "publication_count": 12},
    {"entity_id": "auth_002", "name": "Wang, J., possible variants", "publication_count": 4}
  ],
  "sources": [],
  "ambiguous_entity": true,
  "answered_via_fallback": false,
  "debug": null
}
```
Status `needs_clarification` dipakai ketika entity resolution gate menemukan
**>1 kandidat** untuk nama author/institusi dan sistem tidak boleh memilih diam-diam
(05 §2.4 butir 4). `candidates` berisi id canonical + nama display + ukuran corpus
per kandidat sebagai konteks keputusan. Frontend menampilkan list pilihan; pemilihan
kandidat untuk MVP cukup dikirim ulang sebagai pertanyaan yang menyebut nama
lengkap/display (multi-turn persisted ada di roadmap Fase 6).

### Response — Error
```json
{
  "status": "error",
  "error_type": "sql_generation_failed",
  "message": "Tidak bisa menyusun query yang valid untuk pertanyaan ini. Coba dengan kalimat yang lebih spesifik.",
  "debug": null
}
```
`error_type` enum: `sql_generation_failed`, `sql_validation_rejected`,
`relational_query_failed`, `entity_resolution_failed`, `router_failed`,
`db_timeout`, `embedding_failed`, `llm_unavailable`, `internal_error`.
- `relational_query_failed`: kegagalan eksekusi/pemformatan templat traversal
  relational (05 §6) — pesan manusiawi, tanpa raw query detail.
- `entity_resolution_failed`: kegagalan teknis gate entitas (bukan hasil 0/multi —
  itu `not_found`/`needs_clarification`).
- `router_failed`: LLM router tidak memproduksi output yang bisa diparse setelah
  fallback (kasus di luar §2.5).
Pesan yang dikirim ke user (`message`) selalu bahasa manusia, tidak pernah raw stack
trace/SQL error — detail teknis hanya ada di server log (lihat 08-security.md §log).

### HTTP Status Codes
- `200` untuk `status: ok`, `status: not_found`, dan `status: needs_clarification`
  (ketiganya jawaban valid).
- `422` untuk `status: error` dengan `error_type` terkait input/generation.
- `500` untuk `internal_error` murni (bug, bukan kegagalan pipeline yang diantisipasi).
- `503` jika Ollama/embedding service tidak bisa dihubungi (`llm_unavailable`).

## 3. `GET /api/health` (v2 — lihat target `GET /api/v1/health` di §1)

Health check sederhana untuk memastikan tiga dependency backend hidup:
```json
{
  "status": "ok",
  "checks": {
    "database": "ok",
    "ollama": "ok",
    "embedding_service": "ok"
  }
}
```
Dipakai untuk debugging manual dan (fase 2) monitoring otomatis.

## 4. Non-Goals API (MVP)

- Tidak ada endpoint auth/login (internal-only, lihat 08-security.md untuk model akses).
- Tidak ada endpoint riwayat percakapan (stateless per-request untuk MVP).
- Tidak ada streaming token-by-token (deferred — lihat 11-roadmap.md; MVP fokus
  end-to-end correctness dulu, bukan UX kecepatan-terasa).
- Tidak ada endpoint upload/ingest data baru (data load manual di luar app, lihat 10).

## 5. Error Handling Contract (ringkas, detail penuh di 08-security.md)

Backend **tidak pernah** meneruskan raw exception/SQL error ke response body yang
dikirim ke frontend. Semua exception ditangkap di boundary, dipetakan ke salah satu
`error_type` di atas, dan detail lengkap hanya masuk ke server-side log dengan
request ID yang bisa dikorelasikan kalau perlu debugging lebih dalam.
