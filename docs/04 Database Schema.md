# Database Schema — Canonical Reference

Status: Draft v1 (kolom direkonstruksi dari deskripsi cleaning yang sudah dilakukan —
**wajib diverifikasi terhadap `information_schema` Supabase yang sebenarnya sebelum
dipakai sebagai system prompt text-to-SQL**, lihat catatan di §5)
Last updated: 2026-09-21 | Architecture-review alignment: 2026-09-27 (docs-only)

## 0. Source of Truth (normatif)

```text
PostgreSQL
    ↓
Canonical source of truth

pgvector
    ↓
Derived semantic index

Knowledge Graph
    ↓
Derived relationship index
```

Graph/vector storage tidak pernah menjadi kanonis untuk metadata publikasi.
Sinkronisasi MVP bersifat batch (`Postgres → vector/graph update/rebuild`);
evolusi `batch → CDC/outbox` adalah future dan TIDAK diimplementasikan di MVP.

## 1. Prinsip Cleaning yang Sudah Diterapkan (recap, sudah live di data)

Ini bukan keputusan baru — ini rekap dari kerja yang sudah selesai, dicatat di sini
supaya jadi satu sumber kebenaran yang disinkronkan ke semua dokumen lain (terutama
05-retrieval-rag-design.md, karena LLM text-to-SQL perlu tahu kolom mana yang sudah
lowercase vs preserve-case saat generate `WHERE` clause).

| Kelas kolom | Perlakuan | Contoh kolom |
|---|---|---|
| Identifier (PK/FK) | Tidak disentuh | `*_id`, `doi`, `eid`, `issn`, `grant_number` |
| Display text / proper noun | Casing asli dipertahankan | `title`\*, `author_name`, `institution_name`, `funding_agency`, `reference_text`, `chunk_text` |
| Naratif/kategorikal | Lowercase | `abstract`, `funding_text`, `source_text`, `keyword`, `document_type`, `publication_stage`, `open_access`, `language_of_original_document`, `publisher`, `source`, `keyword_type`, `section`, `source_type`, `city`, `country` |
| Judul publikasi | Titlecase (library `titlecase` + exception list akronim) | `title` (khusus tabel `publications`) |
| Numerik | Tidak diproses sebagai string | `year`, `citation_count`, `author_order`, `reference_order`, `page_start`, `page_end`, `volume`, `issue`, `art_no` |
| Agregasi | Kolom tambahan lower+trim, kolom asli tetap ada | `author_name_normalized`, `institution_name_normalized`, `funding_agency_normalized` |

\* `title` general text di tabel lain (kalau ada) → lowercase; `title` khusus di
`publications` → titlecase. Ini nuance penting yang harus eksplisit di prompt SQL
generator supaya tidak salah asumsi.

**Implikasi penting untuk retrieval:**
- `WHERE keyword = 'something'` harus dibandingkan dengan string lowercase (karena
  `keyword` sudah dilowercase saat cleaning) — SQL generator harus tahu ini, bukan
  menebak.
- `WHERE author_name = 'Someone'` harus match casing asli — lebih aman pakai
  `ILIKE` atau join lewat `author_name_normalized` untuk pencarian yang toleran.
- `GROUP BY` untuk top-author/top-institution/top-funder **harus** pakai kolom
  `*_normalized`, bukan kolom asli — kalau tidak, hasil pecah karena variasi
  spasi/case (ini juga alasan kenapa fuzzy entity resolution di-flag sebagai risiko
  terpisah di PRD, karena `*_normalized` hanya menangani whitespace/case, bukan
  alias/singkatan nama).

## 2. Tabel & Relasi (ERD ringkas)

```
publications ──< pub_author >── authors
publications ──< pub_institution >── institutions
publications ──< keywords
publications ──< funding
publications ──< publication_references
publications ──< chunks
```

Semua relasi many-to-many (author↔publication, institution↔publication) melalui
tabel junction. `keywords`, `funding`, `publication_references`, `chunks` adalah
one-to-many dari `publications`.

**Derivatif (bukan sumber data baru, dibangun dari tabel di atas — lihat §4.2):**
`pub_author` × `pub_institution` dimaterialisasi jadi dua edge table
`institution_collaboration` dan `author_collaboration` untuk jalur retrieval
`relational` (knowledge-graph minimum surface, keputusan 05-retrieval-rag-design.md
§6). Edge table ini tidak ditulis ulang dari data mentah — selalu bersumber dari
tabel junction yang sudah dimuat.

## 3. Definisi Tabel (direkonstruksi — verifikasi §5)

### 3.1 `publications` (tabel inti)
| Kolom | Tipe | Catatan |
|---|---|---|
| `publication_id` | PK, text/int | preserved |
| `title` | text | **titlecase** |
| `abstract` | text | lowercase |
| `doi` | text | preserved |
| `eid` | text | preserved |
| `year` | int | numerik |
| `citation_count` | int | numerik |
| `document_type` | text | lowercase |
| `publication_stage` | text | lowercase |
| `open_access` | text | lowercase |
| `language_of_original_document` | text | lowercase |
| `publisher` | text | lowercase |
| `source` | text | lowercase |
| `volume`, `issue`, `art_no`, `page_start`, `page_end` | numerik/text | tidak diproses |

### 3.2 `authors`
| Kolom | Tipe | Catatan |
|---|---|---|
| `author_id` | PK | preserved |
| `author_name` | text | preserved (display) |
| `author_name_normalized` | text | lower+trim, untuk agregasi/JOIN fuzzy |

### 3.3 `pub_author` (junction)
| Kolom | Catatan |
|---|---|
| `publication_id` | FK → publications |
| `author_id` | FK → authors |
| `author_order` | numerik |

### 3.4 `institutions`
| Kolom | Catatan |
|---|---|
| `institution_id` | PK, preserved |
| `institution_name` | preserved (display) |
| `institution_name_normalized` | lower+trim |
| `city` | lowercase |
| `country` | lowercase |

### 3.5 `pub_institution` (junction)
| Kolom | Catatan |
|---|---|
| `publication_id` | FK |
| `institution_id` | FK |

### 3.6 `keywords`
| Kolom | Catatan |
|---|---|
| `keyword_id` | PK, preserved |
| `publication_id` | FK |
| `keyword` | lowercase |
| `keyword_type` | lowercase (author keyword vs index keyword) |

### 3.7 `funding`
| Kolom | Catatan |
|---|---|
| `funding_id` | PK, preserved |
| `publication_id` | FK |
| `funding_agency` | preserved (display) |
| `funding_agency_normalized` | lower+trim |
| `grant_number` | preserved (identifier) |
| `funding_text` | lowercase |

### 3.8 `publication_references`
| Kolom | Catatan |
|---|---|
| `publication_id` | FK |
| `reference_order` | numerik |
| `reference_text` | preserved (raw citation string) |

### 3.9 `chunks` (kandidat vector search)
| Kolom | Catatan |
|---|---|
| `chunk_id` | PK, preserved |
| `publication_id` | FK |
| `chunk_text` | preserved (isi sesuai granularitas §catatan di bawah) |
| `embedding` | **BELUM ADA — harus ditambahkan**, `vector(1024)` (target: `BAAI/bge-m3`, HNSW, `vector_cosine_ops`) |
| `embedding_model` | **BELUM ADA — required future change** (metadata versi, lihat §7; JANGAN eksekusi perubahan skema di task docs-only ini) |
| `embedding_version` | **BELUM ADA — required future change** (lihat §7) |
| `embedding_dimension` | **BELUM ADA — required future change** (lihat §7) |
| `section` | lowercase (misal 'title' vs 'abstract' asal chunk) |

**Catatan granularitas (WAJIB diverifikasi, jangan diasumsikan):** dokumentasi
sebelumnya ambigu — `chunk_text` bisa berisi 1 chunk per publikasi (title+abstract
digabung) ATAU dipisah 2 row per publikasi (satu per `section`, misal `'title'` dan
`'abstract'`). Ini tidak bisa ditentukan tanpa cek ke data aktual. Query verifikasi
wajib dijalankan di Task 1 (10-implementation-plan.md) sebelum embedding:

```sql
select count(*) as total_chunks,
       count(distinct publication_id) as total_pubs
from chunks;
```
- Rasio 1:1 → satu chunk per publikasi.
- Rasio 2:1 (atau >1) → chunk terpisah per section.

Implikasi ke retrieval: query vector search WAJIB dedup `publication_id` sebelum
`LIMIT` supaya `LIMIT 8` berarti 8 publikasi unik, bukan 8 baris chunk yang bisa
collapse jadi 4 publikasi (sudah diimplementasikan di 05 §4.1 via `distinct on`).

## 4. Perubahan Skema yang Dibutuhkan (2 fitur: vector + relational)

### 4.1 Vector Search (embedding semantik)

Ini task konkret, bukan sekadar desain — masuk ke 10-implementation-plan.md sebagai
task wajib sebelum retrieval semantic bisa jalan:

```sql
-- Aktifkan extension (biasanya sudah aktif di Supabase)
create extension if not exists vector;

-- Tambah kolom embedding. Dimensi mengikuti model embedding yang dipilih.
-- bge-m3 -> 1024 dimensi.
alter table chunks add column embedding vector(1024);

-- Index untuk similarity search (pilih setelah tahu skala data;
-- IVFFlat butuh ANALYZE setelah data terisi, HNSW lebih baru & tanpa perlu training step)
create index on chunks using hnsw (embedding vector_cosine_ops);
```

Catatan: HNSW direkomendasikan dibanding IVFFlat untuk dataset skala kecil-menengah
karena tidak perlu tuning `lists` dan query time lebih stabil dari awal.

### 4.2 Edge Tables — Relational Retrieval (Knowledge-Graph Minimum Surface)

Keputusan P0.1 (05-retrieval-rag-design.md §6 / 11-roadmap.md): knowledge graph
**in-scope MVP** dalam bentuk minimum surface — **bukan graph DB terpisah**,
melainkan dua edge table yang dimaterialisasi dari junction table yang sudah ada.
Data statis MVP → dibangun sekali lewat build script (idempotent: truncate + insert
ulang), bukan view yang recompute tiap query.

```sql
-- Edge 1: kolaborasi antar-institusi (undirected, canonical a < b)
create table if not exists institution_collaboration (
    institution_a int not null references institutions(institution_id),
    institution_b int not null references institutions(institution_id),
    weight           int      not null,  -- jumlah publikasi bersama
    via_publication_ids text[] not null,  -- publikasi bukti (provenance, telusuri balik ke NFR2)
    primary key (institution_a, institution_b),
    check (institution_a < institution_b)  -- kunci canonical, cegah duplikat (A,B)/(B,A)
);

-- Edge 2: co-authorship antar-penulis
create table if not exists author_collaboration (
    author_a int not null references authors(author_id),
    author_b int not null references authors(author_id),
    weight           int      not null,
    via_publication_ids text[] not null,
    primary key (author_a, author_b),
    check (author_a < author_b)
);

-- Index lookup "semua partner dari entitas X" (a atau b sebagai origin)
create index on institution_collaboration (institution_a);
create index on institution_collaboration (institution_b);
create index on author_collaboration (author_a);
create index on author_collaboration (author_b);
```

Rebuild (idempotent, dijalankan build script — contoh untuk institution, pola sama
untuk author dari `pub_author`):

```sql
truncate institution_collaboration;

insert into institution_collaboration (institution_a, institution_b, weight, via_publication_ids)
select pi_a.institution_id, pi_b.institution_id,
       count(distinct pi_a.publication_id),
       array_agg(distinct pi_a.publication_id)
from pub_institution pi_a
join pub_institution pi_b
     on pi_b.publication_id = pi_a.publication_id
    and pi_b.institution_id > pi_a.institution_id   -- pasangan canonical (a < b)
group by pi_a.institution_id, pi_b.institution_id;
```

Catatan implementasi:
- Tipe PK (`int` di contoh) disesuaikan dengan tipe `*_id` aktual hasil Task 0
  (04 §5) — jangan copy contoh mentah lolos verifikasi Task 0.
- `via_publication_ids` array menjaga **provenance**: setiap edge bisa ditelusuri ke
  publikasi bukti, memenuhi NFR2 (groundedness) tanpa khawatir edge "mengarang".
- Traversal multi-hop (path, depth >1) memakai recursive CTE di query level,
  dengan depth di-whitelist (1–3), bukan ditulis bebas (lihat 05 §6.3).
- Query relational dijalankan sebagai **templat terparameterisasi** + validasi
  (05 §6.3), bukan SQL hasil LLM — tidak menambah permukaan injeksi baru di atas
  jalur structured yang sudah dilindungi validator.

## 6. Graph Construction — Postgres → Graph Store (normatif, NOT IMPLEMENTED)

Pipeline yang dimaksud (target):

```text
PostgreSQL
    ↓
Graph Extraction
    ↓
Node Mapping
    ↓
Relationship Mapping
    ↓
Graph Store
```

Pemetaan minimum (node/relasi target MVP):

```text
authors             → Author nodes
publications        → Publication nodes
institutions        → Institution nodes
keywords            → Keyword nodes
funding             → Funder nodes

pub_author          → AUTHORED edges
pub_institution     → AFFILIATED_WITH edges
publication_references → CITES edges (TIDAK di-MVP — string mentah belum di-resolve, §3.8)
keywords            → HAS_KEYWORD edges
funding             → FUNDED_BY edges
```

Prinsip mengikat: **`PostgreSQL = canonical source; Graph = derived index`.**
Implementasi minimum saat ini = edge tables §4.2 (belum dibuat); graph store
mandiri (Apache AGE / Kùzu) = decision pending (09 §8). Teknologi graph di
luar kandidat tersebut (mis. Neo4j/Memgraph) TIDAK didokumentasikan sebagai
opsi tanpa bukti repositori.

## 7. Embedding Pipeline & Versioning (normatif, NOT IMPLEMENTED)

Batch pipeline yang dimaksud:

```text
PostgreSQL publications/chunks → Chunk preparation → Embedding model →
1024-dimensional vector → chunks.embedding
```

Persyaratan: mendukung new records, failed records, reprocessing;
idempotency (`Do not regenerate valid embeddings unnecessarily`).
Metadata wajib per baris: `embedding_model`, `embedding_version`,
`embedding_dimension`. Perubahan skema mengikuti konvensi migrasi proyek
yang ada; karena task ini docs-only, penambahan kolom di §3.9 didokumentasikan
sebagai **required future change**, bukan dieksekusi di sini.

## 8. TODO Verifikasi (wajib sebelum dipakai sebagai system prompt)

Dokumen ini direkonstruksi dari deskripsi cleaning yang diberikan, bukan dari
introspeksi langsung ke database. Sebelum dipakai sebagai skema acuan untuk
text-to-SQL prompt (§05), jalankan dan tempel hasilnya ke sini:

```sql
select table_name, column_name, data_type
from information_schema.columns
where table_schema = 'public'
order by table_name, ordinal_position;
```

Perbedaan nama kolom/tipe antara dokumen ini dan hasil query di atas harus
direkonsiliasi — skema yang salah di system prompt adalah penyebab paling umum
text-to-SQL menghasilkan query yang gagal atau salah.

**Checklist setelah §4 selesai dieksekusi** (task di 10-implementation-plan.md):
- [ ] Kolom `chunks.embedding` ada dan terisi (Task 1) — termuat di query di atas.
- [ ] `institution_collaboration` dan `author_collaboration` terbuat dan terisi
      (Task 8) — juga tampil di `information_schema.columns`.
- [ ] Role `app_readonly` (08-security.md §1.1) punya `SELECT` pada edge table dan
      kolom baru ini — **jalankan ulang grant setelah membuat tabel baru**, karena
      `alter default privileges` tidak selalu mencakup tabel yang dibuat oleh role
      berbeda.
- [ ] Granularitas `chunks` diketahui (count vs count(distinct publication_id))
      — hasilnya dicatat di §3.9.
