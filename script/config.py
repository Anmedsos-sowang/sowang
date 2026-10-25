"""
config.py - Semua konfigurasi pengumpulan data X API (Posts: Read).

Letakkan di scripts/ berdampingan dengan 01_collect_data.py.
Ubah bulan lewat CLI:  python scripts/01_collect_data.py --month 2025-11
"""
from pathlib import Path

# ---------------------------------------------------------------------
# Path (relatif terhadap root repo)
# ---------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"          # data asli API, jangan diedit manual
IDS_DIR = ROOT / "data"                  # post_ids_YYYY-MM.csv (aman di-commit)

# ---------------------------------------------------------------------
# Periode & target
# ---------------------------------------------------------------------
DEFAULT_MONTH = "2025-10"                # bulan default bila --month tidak diberikan
ALLOWED_MONTHS = ("2025-10", "2026-09")  # rentang penelitian (inklusif), untuk validasi
TARGET_PER_MONTH = 700
LANG = "id"                              # operator X: lang:id (BUKAN "in")
ACCEPTED_LANG_CODES = {"id", "in"}       # Operator query memakai "id", tetapi field `lang` di response X mengembalikan kode lama "in"
SEED = 2026                              # seed tetap -> pembagian sel reproducible

# ---------------------------------------------------------------------
# Endpoint & field (TANPA expansions -> tidak ada tagihan user/media)
# public_metrics ikut dalam objek post, tidak menambah biaya.
# ---------------------------------------------------------------------
ENDPOINT = "https://api.x.com/2/tweets/search/all"
TWEET_FIELDS = "created_at,lang,conversation_id,public_metrics"
MAX_RESULTS_CAP = 500                    # batas atas full-archive
MIN_RESULTS = 10                         # batas bawah full-archive

CSV_COLUMNS = [
    "month", "group", "keyword", "id", "created_at", "lang", "text",
    "conversation_id", "like_count", "reply_count", "repost_count", "collected_at",
]

# ---------------------------------------------------------------------
# Budget lokal (pengaman biaya; BUKAN batas billing resmi X)
# Tagihan dihitung per post yang DIKEMBALIKAN API, termasuk yang dibuang
# karena duplikat/kelebihan. Cek harga terbaru di Developer Console.
# ---------------------------------------------------------------------
PRICE_PER_POST = 0.005                   # USD per post read
BUDGET_USD = 3.90                        # script berhenti sebelum melewati ini
SPENT_BEFORE_USD = 0.0                   # pemakaian sebelumnya (bulan lain/uji coba), isi dari Console

# ---------------------------------------------------------------------
# Rate limit (docs X: full-archive per app = 1 req/detik, 300 req/15 menit)
# ---------------------------------------------------------------------
RATE_WINDOW_SEC = 15 * 60
MAX_REQ_PER_WINDOW = 250                 # margin aman di bawah 300
MIN_GAP_SEC = 1.1                        # >1 detik antar request
MAX_POSTS_PER_WINDOW = None              # # dimatikan: dokumentasi X hanya membatasi request (300/15 menit, 1/detik), bukan jumlah post

BACKOFF_BASE_SEC = 2.0                   # exponential backoff: base * 2^n (+ jitter)
BACKOFF_CAP_SEC = 300.0
MAX_RETRIES_5XX = 5                      # 5xx / error jaringan
MAX_RETRIES_429 = 8
HTTP_TIMEOUT = (10, 60)                  # (connect, read) detik
END_SAFETY_SEC = 60                      # end_time harus >=10 detik sebelum "sekarang"

# ---------------------------------------------------------------------
# Strategi sampling
# ---------------------------------------------------------------------
MIN_CELL_QUOTA = 10                      # kuota per sel >= 10 (= min max_results) -> tidak ada post terbuang
JITTER_MIN_FRACTION = 0.5                # kelompok "jitter_end": jam akhir sel acak di 50%-100% irisan
DEDUP_NORMALIZED_TEXT = True             # buang post berteks sama setelah URL/mention/tanda baca dibersihkan

# ---------------------------------------------------------------------
# Kelompok keyword
# URUTAN = PRIORITAS. Kelompok lebih awal "memiliki" post yang cocok dengan
# frasanya; kelompok berikutnya mengeksklusi frasa itu (-"frasa") di query,
# sehingga satu post tidak mungkin muncul di dua kelompok.
# Urutan: paling langka/spesifik dulu, "umum" paling akhir (menyerap sisa).
#
# PEMBAGIAN KUOTA (700/bulan), dirancang adil untuk ketiga RQ:
#   umum   350 (50%) -> RQ1: sampel "alami" percakapan parenting; dominan agar peta topik tidak
#                       didikte keyword gaya parenting. Juga menyerap kekurangan kelompok langka.
#   5 gaya 350 (50%) -> RQ2: tiap gaya punya ~50-100 post/bulan (gentle 98, otoriter 70,
#                       demokratis 70, permisif 63, lain 49) agar sentimen antar gaya/topik sebanding.
#   semua            -> RQ3: tersebar merata sepanjang bulan; pakai share YANG SAMA di setiap bulan
#                       supaya tren antar-bulan sebanding.
#
#   share      : proporsi target bulanan (None = sisa; juga menyerap kekurangan kelompok lain)
#   cells      : jumlah irisan waktu maksimal (None = sebanyak kuota/MIN_CELL_QUOTA)
#   jitter_end : True = jam akhir tiap irisan diacak (mengurangi bias "post terbaru dulu")
#                dan bagian sisa irisan dipakai sebagai cadangan (tanpa tumpang tindih)
# ---------------------------------------------------------------------
GROUPS = [
    {"name": "demokratis", "share": 0.10, "cells": None, "jitter_end": False,
     "query_terms": ["pola asuh demokratis", "authoritative parenting"]},
    {"name": "lain", "share": 0.07, "cells": None, "jitter_end": False,
     "query_terms": ["helicopter parenting", "attachment parenting", "intensive parenting"]},
    {"name": "permisif", "share": 0.09, "cells": None, "jitter_end": False,
     "query_terms": ["pola asuh permisif", "permissive parenting", "terlalu memanjakan anak"]},
    {"name": "otoriter", "share": 0.10, "cells": None, "jitter_end": False,
     "query_terms": ["pola asuh otoriter", "authoritarian parenting", "strict parenting", "voc parenting"]},
    {"name": "gentle", "share": 0.14, "cells": None, "jitter_end": False,
     "query_terms": ["gentle parenting", "positive parenting", "respectful parenting", "pola asuh lembut"]},
    {"name": "umum", "share": None, "cells": None, "jitter_end": True,
     "query_terms": ["pola asuh", "parenting"],
     "label_terms": ["pola asuh", "parenting", "tipe parenting"]},
]

for _g in GROUPS:
    _g.setdefault("label_terms", list(_g["query_terms"]))

ALL_LABEL_TERMS = sorted({t.lower() for g in GROUPS for t in g["label_terms"]})