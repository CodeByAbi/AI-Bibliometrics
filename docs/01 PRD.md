# PRD — Asisten Riset Intelijen (Basis Pengetahuan Publikasi)

**Versi Dokumen:** 3.6.2 (Consolidated Hybrid Master Blueprint — aturan bahasa: narasi Indonesia, teknis Inggris)  
**Tanggal Status:** 2026-09-27  
**Konteks Otoritatif:** Selaras dengan `README.md` dan `docs/01` hingga `docs/12`  

## 1. Latar Belakang

Data publikasi ilmiah prototipe (9 tabel relasional kanonikal — `publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks` — serta 2 **derived edge tables** `institution_collaboration` dan `author_collaboration`, lihat `docs/04 Database Schema.md`) **sudah tersedia dalam basis data PostgreSQL**.

Database PostgreSQL **sudah dibuat dan siap pakai**, sehingga tidak perlu dibuat ulang. Kredensial koneksi telah dikonfigurasi secara aman di internal environment dan tidak diekspos dalam dokumentasi. Basis data saat ini memuat **dataset prototipe kecil** yang menggunakan struktur sekitar 20 kolom (tepatnya 22 kolom metadata naskah pada `publications`) yang disiapkan khusus untuk **validasi end-to-end (E2E)**. Tujuannya adalah membuktikan seluruh rantai retrieval, routing, unifikasi bukti, sintesis, hingga UI berjalan sempurna pada dataset kecil sebelum melangkah ke ingestion dataset Scopus skala besar.

Dokumentasi membedakan secara tegas dua fase rekayasa:
1. **Fase Prototipe / Validasi E2E (Saat Ini)**: Menjalankan dan membuktikan alur fungsional penuh pada 9 tabel relasional kanonikal yang sudah ter-load di PostgreSQL.
2. **Large-Scale / Future Production Ingestion Phase (Future)**: Pipeline ingestion otomatis dari berkas mentah Scopus (Bronze), deduplikasi multi-tier, batch embedding skala besar, dan pemodelan Gold analytics lengkap.

**Status infrastruktur & data saat ini:**
- **Database:** PostgreSQL sudah aktif dan terisi dataset prototipe pada 9 tabel relasional kanonikal (`publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks`).
- **Cleaning & Cleaned Export — DONE:** Data Scopus sudah dibersihkan dan berhasil di-export sebagai 9 file `data/*_cleaned.csv`; tahap ini selesai dan bukan pending.
- **pgvector & Embedding (`chunks.embedding`):** Ekstensi `vector` dan kolom `chunks.embedding vector(1024)` beserta indeks HNSW berstatus **PLANNED / NOT YET IMPLEMENTED** (akan dieksekusi pada Task 1, `docs/10 Implementation Plan.md`).
- **Edge Tables (`institution_collaboration`, `author_collaboration`):** **PLANNED / NOT YET IMPLEMENTED** — akan dimaterialisasi secara idempoten dari tabel relasional junction (Task 8).
- **Gold Analytics (`topics`, `topic_evolution`, `researcher_expertise`):** **PLANNED / NOT YET IMPLEMENTED** — akan dikomputasi dari tabel Silver kanonikal (Task 8.5).

---

## 2. Masalah yang Diselesaikan

- Menjawab pertanyaan agregat/analitik (*"siapa 5 author paling produktif tahun 2023?"*, *"institusi mana yang paling banyak funding dari NIH?"*) butuh SQL manual setiap kali.
- Menjawab pertanyaan semantik/eksploratif (*"paper apa saja yang membahas stres oksidatif pada Wharton's jelly?"*) butuh full-text/semantic search yang belum ada.
- Tidak ada satu pintu masuk (chat/UI) yang menggabungkan dua kebutuhan itu sekaligus dan menjawab dengan grounding ke data asli (bukan jawaban umum dari pengetahuan model).

---

## 3. Tujuan (Fase MVP — End-to-End)

**Tujuan utama: sistem harus berjalan end-to-end.** Bukan optimal, bukan lengkap semua fitur — tetapi rantai penuh dari pertanyaan pengguna hingga jawaban yang benar dan ter-grounding harus berfungsi, bisa didemokan, dan bisa dipakai tim internal setiap hari.

Definisi "berjalan end-to-end" untuk MVP ini:
1. Pengguna mengetik pertanyaan berbahasa natural di UI web (Next.js).
2. Backend sistem menentukan apakah pertanyaan membutuhkan kueri terstruktur (`SQLRoute`), pencarian semantik (`VectorRoute`), jaringan kolaborasi (`GraphRoute`), atau analitik tren (`HybridRoute`).
3. Kueri dieksekusi ke PostgreSQL (jalur terstruktur langsung ke 9 tabel kanonikal; jalur semantik memanfaatkan `chunks.embedding` setelah Task 1 selesai).
4. Hasil (baris / chunk / metrik) disusun menjadi jawaban berbahasa natural oleh LLM, dengan sitasi ke publikasi asal (`[Title, Year, DOI]` atau `[Title, Year, no-doi]`) — bukan jawaban tanpa sumber.
5. Jawaban + sumber ditampilkan di UI dengan rapi, termasuk saat data kosong/tidak relevan (short-circuit deterministik).

---

## 4. Target Pengguna

Internal only untuk MVP — tim riset/analis kecil yang sudah dipercaya (lihat `docs/08 Security.md` untuk konsekuensi pilihan akses ini). Bukan produk publik, belum butuh multi-tenant auth yang kompleks.

---

## 5. Cakupan MVP (Termasuk)

- UI chat satu halaman (dense layout ala Notion/Linear).
- **Arsitektur target**: retrieval hybrid — Dynamic 4-Route: `SQLRoute` (Relasional Silver), `VectorRoute` (`chunks.embedding`), `GraphRoute` (derived edge tables `institution_collaboration` dan `author_collaboration`), dan `HybridRoute` (Gold Layer `topics`, `topic_evolution`, `researcher_expertise` + Silver).
- **Pipeline embedding untuk kolom `chunks`**: **DONE** (Task 1).
- Router: desain rules-first dengan fallback LLM (Qwen2.5-Coder-7B) beserta **typed entity-contract** dan **entity resolution gate**.
- Jawaban dengan sitasi formal terstandarisasi: `[Judul Publikasi, Tahun, DOI]` jika ada DOI, dan `[Judul Publikasi, Tahun, no-doi]` jika naskah tidak memiliki DOI.
- Akses DB read-only — sistem tidak pernah menulis/mengubah data lewat chat (peran `app_readonly`).
- LLM & embedding mandiri (self-hosted), khusus CPU (Ollama + Qwen2.5-Coder-7B, embedding `BAAI/bge-m3`, 1024 dimensi).
- **Normalisasi Casing & Pembersihan:** Aturan lowercase diterapkan secara kanonikal pada data tersimpan untuk field naratif/kategorikal serta kolom `*_normalized` guna mendukung `GROUP BY` dan pencarian eksak, sementara casing asli judul dan nama entitas dipertahankan untuk tampilan UI dan sitasi resmi.

---

## 6. Cakupan MVP (Dikecualikan — Ditunda ke Fase 2+)

- Resolusi entitas fuzzy lanjutan untuk penulis/institusi (tabel alias `rapidfuzz`).
- Auth multi-pengguna, keamanan tingkat baris (row-level security), pembatasan laju (rate limiting) per pengguna.
- Model berbasis GPU yang lebih besar/cepat.
- Riwayat percakapan persisten multi-sesi.
- Evaluasi otomatis (pengujian regresi perangkat kueri emas / golden query set).
- Pipeline ingestion otomatis untuk publikasi baru (saat ini data statis, load sekali).

---

## 7. Metrik Keberhasilan (MVP)

- Pertanyaan agregat sederhana (top N menurut hitungan/tahun/institusi) → jawaban benar, diverifikasi manual terhadap kueri SQL langsung.
- Pertanyaan semantik ("paper tentang X") → mengembalikan publikasi yang relevan secara topikal (8 naskah unik teratas).
- Pertanyaan relasional sederhana (kolaborasi institusi, co-author) → jawaban konsisten dengan tabel edge yang membawa provenance `via_publication_ids`.
- Entitas ambigu ("j. wang") → sistem meminta klarifikasi (`status: needs_clarification`).
- Setiap sitasi dalam jawaban cocok dengan sumber di respons (`CitationVerifier` lolos) — tidak ada sitasi karangan.
- Sistem tidak pernah mengeksekusi SQL destruktif.
- Sistem menyatakan `status: not_found` ketika data tidak ada, bukan mengarang.
- Latensi end-to-end per pertanyaan $\le 15$ detik untuk MVP khusus CPU.

---

## 8. Risiko Utama

| Risiko | Dampak | Mitigasi awal |
|---|---|---|
| Text-to-SQL salah membuat (generate) kolom/tabel | Jawaban salah/error | Prompt dengan skema eksplisit 9 tabel kanonikal + validasi AST `sqlglot` (`docs/05`, `docs/08`) |
| Model khusus CPU lambat/kurang akurat | UX lambat, tingkat error SQL lebih tinggi | Model kecil terbaik di kelasnya (Qwen2.5-Coder-7B), percobaan ulang (retry) guardrail (`docs/09`, `docs/05`) |
| Agregasi author/institution pecah karena variasi nama | Angka top-N salah | Didokumentasikan sebagai keterbatasan yang diketahui (known limitation), kolom `*_normalized` digunakan, resolusi fuzzy ditunda (defer) (`docs/04`, `docs/11`) |
| Kolom `chunks` belum punya embedding | Pencarian vector tidak berfungsi | Task 1 wajib di implementation plan sebelum rute semantik aktif (`docs/10 Implementation Plan.md`) |
| Injeksi SQL via injeksi prompt (prompt injection) | Kebocoran/kerusakan data | Peran DB read-only `app_readonly` + parser allowlist AST (`docs/08`) |

---

## 9. Matriks Konsistensi Keputusan (Lintas Dokumen)

| Area Keputusan | Keputusan Kanonikal | Dokumen Terkait | Status |
|---|---|---|---|
| **Database** | PostgreSQL 15+ (sudah dibuat & siap pakai, kredensial internal aman) | `01`, `02`, `03`, `04`, `08`, `09`, `10`, `11` | ALIGNED |
| **Vector Storage** | `pgvector` HNSW (`m=16, ef_construction=64`, `vector_cosine_ops`) pada `chunks.embedding vector(1024)` (DONE, Task 1) | `02`, `03`, `04`, `05`, `09`, `10`, `12` | ALIGNED |
| **Konvensi penamaan** | 9 tabel relasional kanonikal standar: `publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks` | `01`, `02`, `03`, `04`, `05`, `06`, `10`, `11`, `12` | ALIGNED |
| **Pembersihan data (cleaning)** | Bronze → Silver via script Python — **DONE** (hasil pembersihan ter-export di `data/*_cleaned.csv`, 9 file; sudah ter-load di 9 tabel Silver) | `01`, `04`, `10`, `12` | ALIGNED |
| **Normalisasi lowercase** | Narasi & kategorikal (`abstract`, `keyword`, `country`, dll.) disimpan full lowercase; tampilan & ID asli dipertahankan; kolom `*_normalized` (`author_name_normalized`, `institution_name_normalized`, `funding_agency_normalized`) disimpan lowercase+trim+strip-punct untuk agregasi/pencarian | `01`, `02`, `04`, `05`, `12` | ALIGNED |
| **Chunking** | Granularitas abstrak per publikasi pada tabel `chunks`, field `chunk_text`, `section = 'title_abstract'` | `03`, `04`, `05`, `12` | ALIGNED |
| **Embedding** | `BAAI/bge-m3` (1024-dim, Float32) via `sentence-transformers`, batch 32–64, CPU-optimized, input `Title: {title}\nAbstract: {abstract}` (DONE, Task 1) | `01`, `02`, `03`, `04`, `05`, `09`, `10`, `12` | ALIGNED |
| **Retrieval** | Dynamic 4-Route: `SQLRoute` (Silver), `VectorRoute` (`chunks.embedding`), `GraphRoute` (Derived Edge T1–T4), `HybridRoute` (Gold Analytics + Silver) | `01`, `02`, `03`, `05`, `06`, `10`, `11` | ALIGNED |
| **Vector Similarity Gate** | Cosine similarity threshold dikunci deterministik $\ge 0.48$ untuk model `BAAI/bge-m3` (dikalibrasi dari probe berlabel: off-topic 0.4067, natural-language topical 0.5552-0.6080, near-verbatim 0.6674 — ambang lama 0.65 berada DI DALAM rentang kueri topikal sehingga menolak pertanyaan topikal ordinary); kueri di bawah ambang → short-circuit ke `status: not_found` | `02`, `03`, `05`, `06` | ALIGNED |
| **Format sitasi** | Standar deterministik 3-elemen: `[Judul, Tahun, DOI]` jika ada DOI, dan `[Judul, Tahun, no-doi]` jika naskah tanpa DOI | `01`, `05`, `06`, `07` | ALIGNED |
| **Graph Engine Strategy** | MVP dikunci menggunakan parameterized PostgreSQL Recursive CTE (T1–T4); evaluasi pasca-MVP menggunakan Apache AGE pada Fase 9 | `03`, `04`, `09`, `11` | ALIGNED |
| **Konteks RAG** | Pembingkaian `UNTRUSTED DATA`, LLM murni menyintesis narasi & memvalidasi `EvidenceObject`, short-circuit deterministik pada 0 bukti, `CitationVerifier` post-hoc | `02`, `03`, `05`, `06`, `07`, `08` | ALIGNED |
| **Kontrak API** | `POST /api/v1/ask` (`AskRequest` & `AskResponse` dengan `evidence_objects`) + `GET /api/v1/health`. Endpoint `/api/query` resmi SUPERSEDED | `02`, `03`, `05`, `06`, `07`, `10`, `11` | ALIGNED |
| **Dataset prototipe** | Dataset prototipe kecil (~20 publikasi, 40 chunk, 138 author, 107 institusi, 22 kolom naskah) untuk validasi end-to-end lengkap | `01`, `02`, `03`, `04`, `10`, `11`, `12` | ALIGNED |
| **Dataset skala produksi** | Target masa depan untuk ingestion Scopus skala besar (>100K publikasi) dengan pipeline batch otomatis, deduplikasi multi-tier, dan worker async | `01`, `02`, `03`, `04`, `11`, `12` | ALIGNED |

---

## 10. Keputusan Arsitektur Kanonikal

1. **No-DOI Citation Decision:**
   - *Keputusan:* Format sitasi inline menggunakan pola baku `[Judul, Tahun, DOI]` jika DOI tersedia, dan `[Judul, Tahun, no-doi]` jika publikasi tidak memiliki DOI. Pola ini menjamin regex parser `CitationVerifier` dan parser frontend bekerja deterministik tanpa salah tafsir koma.
2. **Cosine Similarity Threshold Decision (`VectorRoute`):**
   - *Keputusan:* Nilai cosine similarity threshold dikunci pada $\ge 0.48$ untuk model `BAAI/bge-m3`, dipilih dari benchmark berlabel (lihat `reports/retrieval_diagnostic_baseline.md`). Kueri dengan nilai $< 0.48$ langsung diarahkan ke `status: not_found`.
3. **Post-MVP Graph Engine Decision:**
   - *Keputusan:* MVP menggunakan Recursive CTE Terparameterisasi PostgreSQL (Templat T1–T4) pada tabel edge `institution_collaboration` dan `author_collaboration`. Untuk fase pasca-MVP (Fase 9), sistem menetapkan **Apache AGE** sebagai target evaluasi utama karena terintegrasi langsung sebagai ekstensi PostgreSQL tanpa memerlukan infrastruktur instance database graf terpisah.

---

## 11. Riwayat Perubahan

| Dokumen | Perubahan | Alasan |
|---|---|---|
| `docs/01 PRD.md` v3.6.2 | Aturan bahasa: narasi Indonesia, teknis Inggris (`derived edge tables`, `Gold Layer`, `typed entity-contract`, `entity resolution gate`, `Dynamic 4-Route`, dll) | Tanpa duplikasi bilingual; perbaiki terjemahan literal yang aneh |
| `docs/01 PRD.md` v3.6.0 | Sinkronisasi Bahasa Indonesia; tanpa perubahan keputusan teknis | Penyelarasan bahasa 2026-09-27 |
| `docs/01 PRD.md` v3.5.0 | Menandai cleaning + cleaned export sebagai DONE; menegaskan vector storage PENDING | Sinkronisasi progress aktual 2026-09-27 |
| `docs/01 PRD.md` v3.4.0 | Mengembalikan nama tabel kanonikal menjadi standar tanpa akhiran `_cleaned` (`publications`, `authors`, dll.) | Penyelarasan format penamaan sesuai instruksi project |
| `docs/01 PRD.md` v3.4.0 | Mengunci keputusan format sitasi (`no-doi`), threshold kosinus $\ge 0.65$, dan strategi graf Apache AGE | Menutup seluruh open decision menjadi keputusan kanonikal |
| `docs/01 PRD.md` v3.4.0 | Memperbarui Matriks Konsistensi Keputusan dan Riwayat Perubahan | Menjamin konsistensi dokumentasi di seluruh repository |
