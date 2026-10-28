# TK-Analisis-Parenting

Analisis dinamika diskusi gaya parenting di media sosial berbahasa Indonesia: pemetaan topik, sentimen, dan tren temporal.

Repo ini saat ini berisi tahap **pengumpulan data** dari X API v2 (Full-Archive Search, hanya *Posts: Read*).

## Pertanyaan Penelitian

1. **RQ1** - Apa saja topik utama terkait gaya parenting dalam diskusi pengguna?
2. **RQ2** - Bagaimana sentimen pengguna pada topik-topik tersebut?
3. **RQ3** - Bagaimana topik dan sentimen berubah dari waktu ke waktu?

## Struktur Repository

```
sowang/
├── data/
│   ├── raw/   
│   ├── intrerim/       
│   ├── processed/
│   ├── labeling/
│   ├── external/  
│   └── post_ids_YYYY-MM.csv     
├── logs/
├── notebook/
│   ├── 01_prepare_data.ipynb
│   ├── 02_labeling.ipynb
│   ├── 03_preprocess_1_3.ipynb
│   └── 03_preprocess_2.ipynb
├── scripts/
│   ├── config.py        
│   └── collect_data.py        
├── .gitignore
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
python script/collect_data.py --dry-run       # lihat rencana query dan sel, gratis
python script/collect_data.py --smoke-test    # uji token dan query, 10 post (~$0.05)
python script/collect_data.py                 # Oktober 2025, 700 post (default)
python script/collect_data.py --month 2025-11 # bulan lain (rentang 2025-10 s/d 2026-09)

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

## Alur Pengolahan Data

Setelah data dikumpulkan melalui X API, data diolah secara bertahap menggunakan notebook di folder `notebook/`. Proses ini mencakup persiapan data, pelabelan relevansi topik parenting, dan preprocessing teks sebelum digunakan untuk analisis lebih lanjut.

### Notebook

Pengolahan data dilakukan menggunakan notebook Jupyter (.ipynb) di folder notebook/. Setiap notebook dijalankan seperti notebook Python pada umumnya, mengikuti urutan tahapan pengolahan data yang telah disusun.

| Notebook | Deskripsi |
|---|---|
| `01_prepare_data.ipynb` | Menyiapkan dan memeriksa data hasil pengumpulan, termasuk pengecekan struktur data, duplikasi, dan kualitas data awal. |
| `02_labeling.ipynb` | Melakukan pelabelan untuk mengidentifikasi post yang relevan dengan topik parenting dan membedakannya dari post yang tidak relevan. Pada tahap akhir, dilakukan labeling manual terhadap sampel data untuk memperoleh data berlabel yang dapat digunakan sebagai acuan evaluasi. |
| `03_preprocess_1_3.ipynb` | Melakukan preprocessing teks untuk menyiapkan data bagi analisis pertanyaan penelitian 1 dan 3 terkait topic modeling dan temporal analysis. |
| `03_preprocess_2.ipynb` | Melanjutkan preprocessing teks untuk pertanyaan penelitian 2 terkait analisis sentimen. |

### Tahapan Pengolahan

1. **Data validation and cleaning** — Memeriksa kualitas data, menangani duplikasi, dan membersihkan teks dari elemen yang tidak diperlukan, seperti URL, mention, atau karakter tertentu, sesuai kebutuhan analisis.
2. **Labeling** — Mengidentifikasi relevansi post terhadap topik parenting. Post yang membahas pengasuhan secara substantif dipertahankan, sedangkan spam, promosi yang tidak relevan, penggunaan kata *parenting* secara nonliteral, dan konten lain yang tidak sesuai dapat dikeluarkan.
3. **Text preprocessing** — Menormalisasi teks, melakukan tokenisasi, menghapus stopwords, dan melakukan stemming bahasa Indonesia jika diperlukan. Tahapan diterapkan sesuai kebutuhan model dan tujuan analisis.
4. **Data siap analisis** — Menghasilkan data yang telah dibersihkan dan diproses untuk mendukung topic modeling (RQ1), analisis sentimen (RQ2), dan  analisis perubahan topik dan sentimen dari waktu ke waktu (RQ3).