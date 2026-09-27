# UI Spec — Anti-Slop, Clean White (Notion/Linear-style)

Status: Draft v3 | Last updated: 2026-09-23 | Architecture-review alignment: 2026-09-27 (docs-only)

> Kontrak API target: `POST /api/v1/ask` dengan envelope `request_id`/`status`/
> `route`/`answer`/`sources` (lihat 06 §1). Field v2 (`filters_ignored`,
> `unverified_citations`, `answered_via_fallback`, `needs_clarification`)
> dipertahankan maknanya di UI; raw graph query tidak pernah ditampilkan
> (hanya id templat + parameter — §3.3 tetap berlaku).

"Anti-slop" di sini artinya: tidak ada state yang dibiarkan ambigu, tidak ada
jawaban tanpa sumber yang terlihat, tidak ada UI yang berpura-pura "berhasil" saat
sebenarnya kosong/gagal. Setiap kemungkinan hasil dari backend (§06-api-design.md)
punya representasi visual yang eksplisit dan jujur — termasuk status
`needs_clarification` (baru di v3) dan flag `filters_ignored` /
`unverified_citations` / `answered_via_fallback`.

**Perubahan dari v1**: layout chat-bubble diganti dengan layout dense ala
Notion/Linear — alasan konkret ada di §0. Ini bukan sekadar reskin warna; struktur
informasi juga berubah karena bubble chat secara natural tidak cocok untuk
menampilkan tabel ranking, SQL block, dan daftar sitasi dengan rapat.

## 0. Kenapa Notion/Linear, Bukan Chat-Bubble

Keputusan style: **clean white, dense, functional** (bukan gaya percakapan ala
ChatGPT/Claude). Alasan ini perlu eksplisit karena dua gaya itu punya trade-off
berbeda, dan pilihan di sini bukan sekadar selera warna:

- Sebagian besar output sistem ini adalah **data terstruktur** (ranking author,
  tabel agregasi, SQL query, daftar sitasi dengan metadata) — bubble chat yang
  sempit dan berpadding besar justru menyulitkan pembacaan tabel/kode di
  dalamnya. Layout dense dengan lebar penuh lebih cocok untuk ini.
- Ini internal tool untuk tim kecil, dipakai berulang untuk kerja analitik —
  bukan produk consumer yang perlu terasa "hangat"/conversational. Prioritasnya
  scanability, bukan personality.
- Border tipis + whitespace terukur (bukan shadow/bubble besar) lebih mudah
  dipertahankan konsisten di light mode murni, dan tidak butuh dark mode variant
  yang rumit untuk MVP.

## 1. Design Tokens

### 1.1 Warna (light mode saja untuk MVP — tidak ada dark mode di scope ini)

| Token | Hex | Pemakaian |
|---|---|---|
| `bg-base` | `#FFFFFF` | Background utama, selalu putih murni |
| `bg-subtle` | `#F7F7F5` | Panel sekunder (sidebar riwayat, blok SQL) |
| `bg-hover` | `#F1F1EF` | Hover state baris/item interaktif |
| `border-default` | `#E9E9E7` | Semua border/divider — tipis, 1px |
| `border-strong` | `#D9D9D6` | Border elemen fokus/aktif |
| `text-primary` | `#171717` | Teks utama, hampir hitam bukan pure black |
| `text-secondary` | `#6B6B68` | Metadata, label, timestamp |
| `text-tertiary` | `#9B9B98` | Placeholder, disabled |
| `accent` | `#2563EB` | Satu warna aksen: link, tombol primer, fokus ring — dipakai hemat |
| `signal-warn` | `#B45309` | Error state (teks/ikon, bukan background block penuh) |
| `signal-warn-bg` | `#FEF3E2` | Background tipis untuk banner/bubble error |
| `signal-neutral-bg` | `#F7F7F5` | Background untuk state not_found (netral, bukan warning) |

Prinsip: **tidak ada warna dekoratif.** Setiap warna di luar grayscale punya
fungsi (accent = aksi/link, warn = error). Ini konsisten dengan prinsip anti-slop
di §4 lama — dipertahankan, hanya token-nya sekarang eksplisit.

### 1.2 Tipografi

| Token | Font | Pemakaian |
|---|---|---|
| `font-sans` | Inter / system-ui fallback | Semua teks UI, jawaban, label |
| `font-mono` | JetBrains Mono / ui-monospace fallback | SQL block, angka tabular (citation count, tahun, ranking), DOI/ID |
| `text-base` | 14px / line-height 1.5 | Body teks default — **lebih kecil dari chat-app biasa (16px)**, karena ini dense tool, bukan conversational reading |
| `text-sm` | 13px | Metadata, badge, label sumber |
| `text-xs` | 12px | Timestamp, developer mode debug info |
| `text-lg` | 16px | Judul jawaban/pertanyaan utama saja |

### 1.3 Spacing & Struktur

- Unit dasar 4px (4/8/12/16/24/32).
- **Tidak ada rounded-corner besar / shadow drop besar** ala card modern —
  border 1px solid, radius kecil (4-6px) konsisten dengan estetika Notion/Linear
  (flat, bukan neumorphic/elevated).
- Divider antar-item pakai `border-default` 1px, bukan gap besar dengan warna
  background berbeda — ini yang membuat layout terasa "dense" dan bukan bubble.

## 2. Layout (MVP — dua panel, bukan single chat column)

```
┌───────────────┬───────────────────────────────────────────────┐
│ Riwayat        │  Research Intelligence Assistant   [Dev mode ◯]│
│ pertanyaan     ├───────────────────────────────────────────────┤
│ (sesi ini)     │                                                │
│                │  Q  Siapa 5 penulis paling produktif tahun     │
│ ● Top 5 author │     2023?                                      │
│   2023         │  ────────────────────────────────────────────  │
│ ○ Paper stres   │  [Structured]                                  │
│   oksidatif...  │  5 penulis paling produktif tahun 2023:       │
│                │                                                │
│                │  #  Nama            Publikasi                  │
│                │  1  Jane Doe        12                          │
│                │  2  John Smith      9                           │
│                │  ...                                            │
│                │                                                │
│                │  ▸ Sumber (5)                                  │
│                │  ▸ SQL yang dijalankan   (dev mode only)        │
│                │                                                │
├───────────────┴───────────────────────────────────────────────┤
│  Tanyakan sesuatu tentang database publikasi...      [Kirim →] │
└─────────────────────────────────────────────────────────────────┘
```

- **Sidebar kiri** (fixed width ~240px, `bg-subtle`): daftar pertanyaan dalam
  sesi ini, klik untuk scroll ke jawaban terkait — bukan riwayat persisten lintas
  sesi (tetap sesuai batasan stateless di 06-api-design.md §4).
- **Panel kanan** (fluid width): pertanyaan (`Q`, prefix label bukan bubble) dan
  jawaban ditampilkan sebagai blok berurutan dipisah divider tipis, bukan bubble
  bergantian kiri-kanan. Ini yang paling membedakan dari v1.
- **Input bar** full-width di bawah, menempel ke panel kanan.
- Hasil tabular (ranking, daftar publikasi) dirender sebagai **tabel HTML asli**
  dengan `font-mono` untuk kolom angka — bukan markdown list di dalam bubble teks,
  supaya alignment kolom rapi (ini limitation nyata dari layout bubble v1 yang
  sekarang diperbaiki).

## 3. Komponen Wajib

### 3.1 Route Badge
Label kecil, bukan pill berwarna-warni: teks `[Structured]` / `[Semantic]` /
`[Hybrid]` / `[Relational]` (jalur ke-4, lihat 05 §6) dengan `text-secondary`,
border tipis `border-default`, tanpa fill warna — konsisten dengan prinsip "warna
hanya fungsional" di §1.1. Ini tetap wajib tampil di setiap jawaban (fungsinya sama
seperti v1: transparansi mekanisme, bukan hiasan).

### 3.2 Sumber (Sources)
- Collapsible section `▸ Sumber (N)`, default collapsed jika >3, expanded jika ≤3.
- Tiap sumber satu baris dense: judul (truncate dengan ellipsis + tooltip full
  text on hover), tahun (`font-mono`), DOI sebagai link `accent` color jika ada.
- Untuk hasil semantic/hybrid: kolom relevansi kecil di ujung kanan baris
  (`text-tertiary`, format "0.82" bukan bar visual — dense, bukan dekoratif).
- Jika `sources` kosong (structured murni): baris teks `text-secondary` kecil:
  "Hasil agregasi langsung dari database" — tetap wajib tampil, tidak dibiarkan
  kosong tanpa penjelasan (aturan ini tidak berubah dari v1).
- Untuk hasil relational: tiap baris hasil menampilkan provenance publikasi —
  badge kecil `via N publikasi` (`text-secondary`, `text-sm`) yang merujuk ke
  `via_publication_ids` dari edge (05 §6.2), bisa di-expand ke daftar
  judul+tahun+DOI. Ini grounding yang sama wajibnya seperti sumber semantic.

### 3.3 Developer Mode
Toggle switch kecil di header kanan (bukan checkbox besar) — konsisten dengan
pola toggle ala Linear settings. Saat aktif, tiap jawaban dapat section tambahan
`▸ SQL yang dijalankan`: block `font-mono` dengan `bg-subtle`, tanpa syntax
highlighting warna-warni (cukup monospace + border, syntax highlighting berwarna
kontradiktif dengan prinsip clean white minimal) — ditambah:
- untuk route structured/hybrid: baris `route_reasoning` dan SQL hasil validator;
- untuk route relational: **id templat** (T1/T2/T3/T4, lihat 05 §6.2) + parameter
  yang di-bind + depth hop — bukan SQL mentah (jalur relational tidak punya SQL
  hasil LLM, yang ditampilkan adalah penanda templat, transparansi tetap terjaga);
- untuk setiap jawaban: `latency_ms`, dan badge kecil `fallback` jika
  `answered_via_fallback: true` (supaya tebakan fallback tidak dikira route yang
  benar).

### 3.4 Tabel Hasil Terstruktur (baru di v2 — tidak eksplisit di v1)
- Header kolom `text-secondary`, `text-sm`, bottom border `border-strong`.
- Baris data: `text-primary`, kolom numerik rata kanan + `font-mono`.
- Hover baris: `bg-hover`.
- Maksimal ditampilkan sesuai `LIMIT` dari SQL (lihat 06-api-design.md) — jika
  hasil terpotong, baris terakhir menampilkan catatan kecil "menampilkan 50 dari
  N hasil" (`text-tertiary`) supaya user tahu ini bukan keseluruhan data.

## 4. States (semua wajib punya desain eksplisit)

### 4.1 Loading
- Input dan tombol kirim disabled selama request berlangsung.
- Bukan spinner generik — tampilkan label tahapan teks kecil yang berubah
  (`text-secondary`, `text-sm`): "Menentukan jenis pertanyaan..." → "Menyusun
  query..." / "Menelusuri relasi antarentitas..." (khusus route relational, diikuti
  id templat yang dipilih) → "Menyusun jawaban..." dengan elapsed-time counter di
  sampingnya (mis. "12s"). Karena CPU-only MVP bisa sampai ~15 detik (NFR1), diam
  total terasa seperti hang — counter eksplisit ini wajib, bukan opsional.
- Style loading tetap flat: garis progres tipis (`accent` color) di bawah
  header, bukan skeleton loader dekoratif besar.

### 4.2 Sukses (`status: ok`)
Seperti §2-3 — blok jawaban + route badge + tabel/teks + sources.
Tiga catatan append-only (baru di v3):
- Jika `filters_ignored` tidak kosong (field tanpa slot di route terpilih, 05 §2.6),
  tampilkan line `text-secondary` kecil di bawah jawaban: "Catatan: [field] tidak
  diterapkan pada filter kali ini." — disediakan oleh synthesis, bukan disisipkan
  frontend diam-diam.
- Jika `unverified_citations` tidak kosong, badge kecil netral `text-secondary`
  "N sitasi tidak dapat diverifikasi" dengan list judul yang di-strip (05 §7.2).
- Jika `answered_via_fallback: true`, route badge menampilkan `[Semantic*]` dan di
  developer mode ada label fallback (lihat §3.3).

### 4.3 Tidak Ditemukan (`status: not_found`)
- Blok jawaban dengan `signal-neutral-bg` background tipis (bukan putih polos,
  supaya berbeda visual dari jawaban normal — tapi juga bukan warna warning),
  border kiri 2px `border-strong`.
- Teks eksplisit: "Tidak ditemukan publikasi yang relevan di database untuk
  pertanyaan ini."
- **Tidak** boleh terlihat identik dengan blok jawaban sukses — ini pelanggaran
  anti-slop paling umum di RAG app (jawaban kosong dibungkus seolah lengkap),
  prinsip ini tidak berubah dari v1.

### 4.4 Error (`status: error`)
- Blok dengan `signal-warn-bg` background, border kiri 2px `signal-warn`, ikon
  kecil warning (bukan merah solid besar — tetap dalam batas "clean").
- Pesan human-readable dari `message` (06-api-design.md) — raw error tidak
  pernah tampil ke user biasa; di developer mode, `error_type` + request ID
  ditampilkan sebagai baris `font-mono text-xs` di bawah pesan.
- Tombol teks kecil "Coba lagi" (`accent` color, bukan button besar berwarna
  solid) yang mengirim ulang pertanyaan yang sama.

### 4.5 Empty Input
- Tombol kirim disabled (opacity turun, tidak ada warna) jika input kosong/
  whitespace — validasi client-side.

### 4.6 Backend Unreachable
- Banner tipis full-width di paling atas (di atas header, bukan di dalam
  panel), `signal-warn-bg`, teks singkat: "Tidak bisa terhubung ke server —
  periksa koneksi atau coba lagi nanti." Persisten selama `/api/health` gagal.

### 4.7 Perlu Klarifikasi (`status: needs_clarification`) — baru di v3
- Blok jawaban `bg-subtle` dengan border kiri 2px `accent` (beda dari
  not_found/error — ini bukan kegagalan, tapi sistem menahan diri karena tidak
  bisa memilih entitas diam-diam, lihat 05 §2.4).
- Teks pertanyaan klarifikasi dari backend, lalu daftar kandidat sebagai **list
  pilihan**: nama display + ukuran corpus per kandidat, satu klik untuk memilih.
  Pilihan MVP dikirim ulang sebagai pertanyaan baru yang menyebut nama lengkap
  kandidat (multi-turn persisted ada di roadmap Fase 6).
- Tidak ada tombol "pilih otomatis" — memilih salah satu tanpa konfirmasi adalah
  perilaku yang sengaja dilarang di level desain 05 §2.4.

## 5. Prinsip Visual (ringkas)

- **Tidak ada** gradient, shadow besar, warna dekoratif, atau ilustrasi — flat,
  border-based, grayscale-dominan dengan satu accent color dipakai hemat.
- Densitas di atas breathing room besar: padding antar-elemen cukup untuk
  keterbacaan, tidak berlebihan (bedanya dengan chat-app consumer yang biasanya
  sengaja lapang).
- `font-mono` konsisten untuk semua angka tabular dan kode/SQL — ini aturan
  keras, bukan preferensi, karena alignment kolom penting untuk tool analitik.
- Semua elemen interaktif (expand sources, toggle dev mode, tombol retry, baris
  riwayat di sidebar) punya state hover (`bg-hover`) dan focus ring (`accent`,
  2px) yang jelas.

## 6. Aksesibilitas Minimal

- Kontras teks memenuhi WCAG AA (`text-primary` di atas `bg-base` = kontras
  tinggi by design; `text-secondary`/`text-tertiary` diuji tidak turun di bawah
  AA untuk teks kecil).
- Status (error/not_found/route badge) dibedakan lewat teks + border, tidak
  hanya warna — penting karena palet sengaja minim warna, jangan sampai satu-
  satunya pembeda adalah warna yang mirip untuk user color blindness.
- Loading state punya `aria-live` region.
- Kontras `signal-warn` (#B45309) di atas `signal-warn-bg` (#FEF3E2) dan
  `accent` (#2563EB) di atas `bg-base` sudah di rasio AA untuk teks normal —
  perlu diverifikasi ulang dengan contrast checker saat implementasi final,
  dicatat di sini supaya tidak lolos tanpa dicek.
