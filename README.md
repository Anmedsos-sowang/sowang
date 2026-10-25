# TK-Analisis-Parenting

Analisis dinamika diskusi gaya parenting di media sosial berbahasa Indonesia: pemetaan topik, sentimen, dan tren temporal.

Repo ini saat ini berisi tahap **pengumpulan data** dari X API v2 (Full-Archive Search, hanya *Posts: Read*).

## Pertanyaan Penelitian

1. **RQ1** - Apa saja topik utama terkait gaya parenting dalam diskusi pengguna?
2. **RQ2** - Bagaimana sentimen pengguna pada topik-topik tersebut?
3. **RQ3** - Bagaimana topik dan sentimen berubah dari waktu ke waktu?

## Struktur Repo

```
TK-Analisis-Parenting/
├── data/
│   ├── raw/                      # data asli API (jangan diedit manual, di-gitignore)
│   │   ├── posts_YYYY-MM.csv
│   │   └── checkpoint_YYYY-MM.json
│   ├── processed/
│   ├── labeled/
│   └── post_ids_YYYY-MM.csv      # ID post saja (boleh di-commit)
├── scripts/
│   ├── config.py         # semua konfigurasi pengumpulan data
│   └── 01_collect_data.py        # script pengambilan data
├── .env.example
└── requirements.txt
```

## Persiapan

```bash
pip install -r requirements.txt
cp .env.example .env              # lalu isi X_BEARER_TOKEN
```

Tambahkan ke `.gitignore`:

```
.env
data/raw/
```

## Cara Menjalankan

Jalankan dari root repo.

```bash
python scripts/01_collect_data.py --dry-run       # lihat rencana query dan sel, gratis
python scripts/01_collect_data.py --smoke-test    # uji token dan query, 10 post (~$0.05)
python scripts/01_collect_data.py                 # Oktober 2025, 700 post (default)
python scripts/01_collect_data.py --month 2025-11 # bulan lain (rentang 2025-10 s/d 2026-09)

python -u script/collect_data.py --month 2025-10 --yes | tee logs/run_2025-10.txt  # versi lengkap: sekaligus simpan log hasil run, tinggal ganti bulan & tahun
```

Opsi lain: `--target N` (jumlah post), `--yes` (lewati konfirmasi `YA`).

Jika proses terhenti (error, budget habis, Ctrl+C), jalankan perintah yang **sama** lagi. Script melanjutkan dari checkpoint.

## Strategi Pengumpulan

| Aspek | Strategi |
|---|---|
| Target | 700 post unik per bulan, `lang:id`, tanpa retweet |
| Pembagian keyword | 6 kelompok dengan prioritas; kelompok berikutnya mengeksklusi frasa kelompok sebelumnya (`-"frasa"`) sehingga tidak ada post di dua kelompok |
| Sebaran waktu | Bulan dipotong menjadi irisan waktu; kuota dibagi rata, jam akhir sel kelompok Umum diacak (seed tetap) |
| Duplikasi | Dedup berdasarkan ID dan teks yang dinormalisasi (URL, mention, tanda baca dibuang) |
| Kekurangan kuota | Kelompok langka yang kurang diisi oleh Umum, lalu oleh sisa irisan waktu yang belum dicari |
| Rate limit | 1 request per detik, 300 request per 15 menit per app; saat 429 menunggu `Retry-After`, lalu `x-rate-limit-reset`, lalu exponential backoff dengan jitter |
| Biaya | $0,005 per post yang dikembalikan API; budget lokal `BUDGET_USD` (default $5,00) dan konfirmasi `YA` sebelum request berbayar |
| Data minimal | Tanpa `expansions` (tidak ada tagihan user/media); tidak menyimpan author |

### Pembagian kuota per bulan

| Kelompok | Kuota | Keyword | Peran |
|---|---|---|---|
| Umum | 350 | pola asuh, parenting, tipe parenting | RQ1 |
| Gentle / positive | 98 | gentle parenting, positive parenting, respectful parenting, pola asuh lembut | RQ2 |
| Otoriter / strict | 70 | pola asuh otoriter, authoritarian parenting, strict parenting, voc parenting | RQ2 |
| Demokratis / authoritative | 70 | pola asuh demokratis, authoritative parenting | RQ2 |
| Permisif | 63 | pola asuh permisif, permissive parenting, terlalu memanjakan anak | RQ2 |
| Lain | 49 | helicopter parenting, attachment parenting, intensive parenting | RQ2 |

Gunakan pembagian yang **sama** di semua bulan agar tren antar-bulan (RQ3) sebanding. Bila diubah, ubah `share` di `config.py` untuk seluruh bulan.

Urutan prioritas kelompok (dari yang pertama memiliki post): demokratis, lain, permisif, otoriter, gentle, umum.

## Kolom Data (`posts_YYYY-MM.csv`)

| Kolom | Keterangan |
|---|---|
| `month` | Bulan pengambilan (YYYY-MM) |
| `group` | Kelompok keyword tempat post diambil |
| `keyword` | Keyword yang muncul di teks (dilabeli lokal, gratis) |
| `id` | ID post |
| `created_at` | Waktu pembuatan post (UTC) |
| `lang` | Bahasa post (`id`) |
| `text` | Teks post |
| `conversation_id` | ID percakapan |
| `like_count`, `reply_count`, `repost_count` | Metrik publik saat pengambilan |
| `collected_at` | Waktu pengambilan data (UTC) |

## Catatan dan Keterbatasan

- Di dalam tiap sel waktu, API mengembalikan post terbaru lebih dulu, jadi sampel tidak sepenuhnya acak.
- Post yang memuat frasa dari dua kelompok masuk ke kelompok dengan prioritas lebih awal.
- Kelompok langka (mis. demokratis, permisif, lain) bisa kurang dari kuota pada bulan tertentu. Komposisi sebenarnya ada di log akhir dan kolom `group`.
- Untuk topic modeling (RQ1), kelompok gaya sengaja diambil lebih banyak dari proporsi aslinya; laporkan juga hasil kelompok Umum saja sebagai pembanding.
- Isi `SPENT_BEFORE_USD` di config dengan pemakaian terbaru dari Developer Console sebelum menjalankan bulan berikutnya.
- Jangan commit data mentah; cukup bagikan `data/post_ids_YYYY-MM.csv`.