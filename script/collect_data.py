from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import random
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

import config as C  # scripts/ otomatis ada di sys.path saat dijalankan langsung


# =====================================================================
# Util
# =====================================================================
def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def split_even(total: int, k: int) -> list[int]:
    base, extra = divmod(total, k)
    return [base + (1 if i < extra else 0) for i in range(k)]


def quote(term: str) -> str:
    return f'"{term}"' if " " in term else term


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """[awal bulan, awal bulan berikutnya) dalam UTC; end_time X bersifat eksklusif."""
    y, m = map(int, month.split("-"))
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc)
    return start, end


def validate_month(month: str) -> None:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise SystemExit(f"Format bulan salah: {month!r} (pakai YYYY-MM)")
    lo, hi = C.ALLOWED_MONTHS
    if not (lo <= month <= hi):
        raise SystemExit(f"Bulan {month} di luar rentang penelitian {lo} s/d {hi}")


_URL = re.compile(r"https?://\S+")
_MENTION = re.compile(r"@\w+")


def norm_text(text: str) -> str:
    """Kunci dedup teks: huruf kecil, tanpa URL/mention/tanda baca, spasi dirapikan."""
    t = _MENTION.sub(" ", _URL.sub(" ", text.lower()))
    return re.sub(r"\W+", " ", t).strip()


def label_keywords(text: str) -> str:
    """Label lokal (gratis): keyword yang muncul di teks, kecuali yang hanya bagian
    dari frasa lebih spesifik (mis. 'parenting' di dalam 'gentle parenting')."""
    low = text.lower()
    hit = [t for t in C.ALL_LABEL_TERMS if t in low]
    specific = [t for t in hit if not any(t != o and t in o for o in hit)]
    return " | ".join(specific) if specific else "(match_non_literal)"


# =====================================================================
# Query & rencana sel (kelompok x irisan waktu)
# =====================================================================
def build_query(idx: int) -> str:
    g = C.GROUPS[idx]
    inc = " OR ".join(quote(t) for t in g["query_terms"])
    excl = [t for prev in C.GROUPS[:idx] for t in prev["query_terms"]]
    q = f"({inc}) lang:{C.LANG} -is:retweet"
    if excl:
        q += " " + " ".join(f"-{quote(t)}" for t in excl)
    if len(q) > 1024:
        raise ValueError(f"Query '{g['name']}' {len(q)} karakter, melebihi batas 1024")
    return q


@dataclass
class Cell:
    id: str
    group: str
    query: str
    start: datetime
    end: datetime
    quota: int


def group_quotas(target: int) -> dict[str, int]:
    """Kuota awal per kelompok; kelompok share=None mendapat sisa."""
    q = {g["name"]: round(g["share"] * target) for g in C.GROUPS if g["share"] is not None}
    rest = target - sum(q.values())
    if rest < 0:
        raise ValueError("Jumlah share kelompok melebihi 100%")
    for g in C.GROUPS:
        if g["share"] is None:
            q[g["name"]] = rest
    return q


def plan_group(idx: int, quota: int, start: datetime, end: datetime, month: str
               ) -> tuple[list[Cell], list[tuple[datetime, datetime]]]:
    """Bagi [start,end) jadi n irisan waktu sama panjang; kuota dibagi rata.
    n dipilih agar kuota per sel >= MIN_CELL_QUOTA (tidak ada post terbayar-terbuang).
    Bila jitter_end: jam akhir sel diacak; sisa irisan dikembalikan sebagai cadangan."""
    g = C.GROUPS[idx]
    if quota <= 0:
        return [], []
    n = max(1, quota // C.MIN_CELL_QUOTA)
    if g["cells"]:
        n = min(n, g["cells"])
    rng = random.Random(f"{C.SEED}|{month}|{g['name']}")
    span = (end - start) / n
    cells, spare = [], []
    for i, q in enumerate(split_even(quota, n)):
        s = (start + span * i).replace(microsecond=0)
        e = end if i == n - 1 else (start + span * (i + 1)).replace(microsecond=0)
        main_end = e
        if g["jitter_end"]:
            main_end = (s + (e - s) * rng.uniform(C.JITTER_MIN_FRACTION, 1.0)).replace(microsecond=0)
            if (e - main_end).total_seconds() >= 3600:
                spare.append((main_end, e))
        cells.append(Cell(f"{g['name']}|{iso(s)}|{iso(main_end)}", g["name"],
                          build_query(idx), s, main_end, q))
    return cells, spare


def spread_order(n: int) -> list[int]:
    """Urutan sel deterministik (bit-reversal): awalan urutan mana pun tersebar merata sepanjang bulan."""
    bits = max(1, (n - 1).bit_length())
    return [r for r in (int(format(i, f"0{bits}b")[::-1], 2) for i in range(1 << bits)) if r < n]


def plan_fill(idx: int, shortfall: int, spare: list[tuple[datetime, datetime]], month: str) -> list[Cell]:
    """Cadangan untuk kelompok umum: memakai SISA irisan yang belum pernah dicari
    (tidak tumpang tindih dengan sel utama -> tidak membayar post yang sama dua kali)."""
    if shortfall <= 0 or not spare:
        return []
    rng = random.Random(f"{C.SEED}|{month}|fill")
    n = max(1, min(len(spare), shortfall // C.MIN_CELL_QUOTA))
    chosen = sorted(rng.sample(spare, n))
    name = C.GROUPS[idx]["name"]
    return [Cell(f"{name}_fill|{iso(s)}|{iso(e)}", name, build_query(idx), s, e, q)
            for (s, e), q in zip(chosen, split_even(shortfall, n))]


# =====================================================================
# Rate limiting + HTTP client
# =====================================================================
class SlidingWindow:
    """Pembatas sisi-klien: maksimal `limit` unit dalam `window` detik terakhir."""

    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self.events: deque[tuple[float, int]] = deque()

    def acquire(self, cost: int = 1) -> None:
        cost = min(cost, self.limit)
        while True:
            now = time.time()
            while self.events and now - self.events[0][0] >= self.window:
                self.events.popleft()
            if sum(c for _, c in self.events) + cost <= self.limit:
                self.events.append((now, cost))
                return
            wait = self.window - (now - self.events[0][0]) + 0.5
            log(f"  [throttle] kuota lokal penuh, tunggu {wait:.0f} detik")
            time.sleep(max(wait, 0.5))

    def settle(self, actual: int) -> None:
        """Ganti biaya reservasi request terakhir dengan jumlah post yang benar-benar dikembalikan."""
        if self.events:
            ts, _ = self.events[-1]
            self.events[-1] = (ts, actual)


class FatalAPIError(RuntimeError):
    pass


class BudgetStop(RuntimeError):
    pass


def backoff_delay(attempt: int) -> float:
    """Exponential backoff + 'equal jitter': e = min(cap, base*2^n); tunggu e/2 + U(0, e/2).
    Jitter mencegah retry serempak; sebagian delay tetap dijamin."""
    e = min(C.BACKOFF_CAP_SEC, C.BACKOFF_BASE_SEC * (2 ** attempt))
    return e / 2 + random.uniform(0, e / 2)


class XClient:
    def __init__(self, token: str):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}",
                               "User-Agent": "parenting-research-fetch/2.0"})
        self.req_win = SlidingWindow(C.MAX_REQ_PER_WINDOW, C.RATE_WINDOW_SEC)
        self.post_win = (SlidingWindow(C.MAX_POSTS_PER_WINDOW, C.RATE_WINDOW_SEC)
                         if C.MAX_POSTS_PER_WINDOW else None)
        self.last_call = 0.0
        self.n_requests = 0
        self.last_headers: dict = {}

    def _pace(self, max_results: int) -> None:
        self.req_win.acquire(1)
        if self.post_win:
            self.post_win.acquire(max_results)
        gap = C.MIN_GAP_SEC - (time.time() - self.last_call)
        if gap > 0:
            time.sleep(gap)

    def _settle(self, actual: int) -> None:
        if self.post_win:
            self.post_win.settle(actual)

    @staticmethod
    def _reset_wait(headers) -> float | None:
        try:
            return max(0.0, int(headers["x-rate-limit-reset"]) - time.time()) + 1.0
        except (KeyError, ValueError):
            return None

    @staticmethod
    def _retry_after(headers) -> float | None:
        try:
            return float(headers["retry-after"]) + 1.0
        except (KeyError, ValueError):
            return None

    def get(self, params: dict) -> dict:
        n5xx = n429 = nnet = 0
        cap = C.RATE_WINDOW_SEC + 60
        while True:
            self._pace(int(params.get("max_results", C.MIN_RESULTS)))
            self.last_call = time.time()
            self.n_requests += 1
            try:
                r = self.s.get(C.ENDPOINT, params=params, timeout=C.HTTP_TIMEOUT)
            except (requests.Timeout, requests.ConnectionError) as e:
                nnet += 1
                self._settle(0)
                if nnet > C.MAX_RETRIES_5XX:
                    raise FatalAPIError(f"Jaringan gagal berulang: {e}")
                d = backoff_delay(nnet - 1)
                log(f"  [retry] error jaringan ({type(e).__name__}), tunggu {d:.0f}s")
                time.sleep(d)
                continue

            self.last_headers = dict(r.headers)

            if r.status_code == 200:
                body = r.json()
                self._settle(len(body.get("data", [])))
                # Pencegahan: bila sisa kuota 0, tidur sampai reset sebelum request berikutnya.
                if r.headers.get("x-rate-limit-remaining") == "0":
                    w = self._reset_wait(r.headers)
                    if w:
                        log(f"  [rate] sisa kuota 0, tunggu reset {w:.0f}s")
                        time.sleep(min(w, cap))
                return body

            if r.status_code == 429 or r.status_code >= 500:
                self._settle(0)

            if r.status_code == 429:
                # Batas penggunaan/kredit habis tidak akan pulih dengan menunggu.
                if "UsageCapExceeded" in r.text or "usage-capped" in r.text:
                    raise FatalAPIError(f"Batas penggunaan akun tercapai: {r.text[:300]}")
                n429 += 1
                if n429 > C.MAX_RETRIES_429:
                    raise FatalAPIError("429 terus-menerus, berhenti (checkpoint tersimpan).")
                # Prioritas: Retry-After > x-rate-limit-reset > exponential backoff.
                w, src = self._retry_after(r.headers), "Retry-After"
                if w is None:
                    w, src = self._reset_wait(r.headers), "x-rate-limit-reset"
                if w is None:
                    w, src = backoff_delay(n429 - 1), "exponential backoff"
                w = min(w, cap)
                log(f"  [429] tunggu {w:.0f}s ({src}), percobaan {n429}/{C.MAX_RETRIES_429}")
                time.sleep(w)
                continue

            if r.status_code >= 500:
                n5xx += 1
                if n5xx > C.MAX_RETRIES_5XX:
                    raise FatalAPIError(f"Server error {r.status_code} berulang.")
                d = backoff_delay(n5xx - 1)
                log(f"  [retry] {r.status_code}, tunggu {d:.0f}s ({n5xx}/{C.MAX_RETRIES_5XX})")
                time.sleep(d)
                continue

            # 400 (query salah) / 401 / 402 (kredit habis) / 403 / dll: jangan di-retry.
            raise FatalAPIError(f"HTTP {r.status_code}: {r.text[:500]}")


# =====================================================================
# Penyimpanan + checkpoint
# =====================================================================
class Store:
    def __init__(self, month: str):
        C.RAW_DIR.mkdir(parents=True, exist_ok=True)
        self.month = month
        self.csv_path = C.RAW_DIR / f"posts_{month}.csv"
        self.ck_path = C.RAW_DIR / f"checkpoint_{month}.json"
        self.ids: set[str] = set()
        self.texts: set[str] = set()
        self.by_group: dict[str, int] = {}
        if self.csv_path.exists():
            with open(self.csv_path, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    self.ids.add(row["id"])
                    k = norm_text(row["text"])
                    if k:
                        self.texts.add(k)
                    self.by_group[row["group"]] = self.by_group.get(row["group"], 0) + 1
        self.ck = {"meta": {}, "cells": {}}
        if self.ck_path.exists():
            self.ck = json.loads(self.ck_path.read_text(encoding="utf-8"))
        meta = self.ck["meta"]
        meta.setdefault("billed_posts", 0)
        meta.setdefault("stats", {"dup_id": 0, "dup_text": 0, "bad_lang": 0})

    @property
    def count(self) -> int:
        return len(self.ids)

    @property
    def billed(self) -> int:
        return self.ck["meta"]["billed_posts"]

    def save_ck(self) -> None:
        tmp = self.ck_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.ck, indent=1), encoding="utf-8")
        for attempt in range(8):
            try:
                tmp.replace(self.ck_path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.3 * (attempt + 1))   # tunggu kunci dilepas, lalu coba lagi

    def allowed_posts(self) -> int:
        """Sisa post yang masih boleh ditagih menurut budget lokal."""
        usable = C.BUDGET_USD - C.SPENT_BEFORE_USD
        return int(usable / C.PRICE_PER_POST + 1e-9) - self.billed

    def check(self, t: dict) -> str | None:
        """None bila post layak disimpan; selain itu alasan penolakan."""
        if t["id"] in self.ids:
            return "dup_id"
        if t.get("lang", C.LANG) not in C.ACCEPTED_LANG_CODES:
            return "bad_lang"
        if C.DEDUP_NORMALIZED_TEXT:
            k = norm_text(t["text"])
            if k and k in self.texts:
                return "dup_text"
        return None

    def register(self, t: dict, group: str) -> None:
        self.ids.add(t["id"])
        if C.DEDUP_NORMALIZED_TEXT:
            k = norm_text(t["text"])
            if k:
                self.texts.add(k)
        self.by_group[group] = self.by_group.get(group, 0) + 1

    def append(self, rows: list[dict]) -> None:
        if not rows:
            return
        new = not self.csv_path.exists()
        with open(self.csv_path, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=C.CSV_COLUMNS)
            if new:
                w.writeheader()
            w.writerows(rows)


def to_row(t: dict, group: str, month: str) -> dict:
    pm = t.get("public_metrics", {})
    return {
        "month": month,
        "group": group,
        "keyword": label_keywords(t["text"]),
        "id": t["id"],
        "created_at": t.get("created_at", ""),
        "lang": t.get("lang", ""),
        "text": t["text"],
        "conversation_id": t.get("conversation_id", ""),
        "like_count": pm.get("like_count", 0),
        "reply_count": pm.get("reply_count", 0),
        "repost_count": pm.get("retweet_count", pm.get("repost_count", 0)),
        "collected_at": iso(datetime.now(timezone.utc)),
    }


# =====================================================================
# Pengambilan per sel
# =====================================================================
def fetch_cell(client: XClient, store: Store, cell: Cell, target: int) -> None:
    st = store.ck["cells"].setdefault(cell.id, {"n": 0, "done": False, "next_token": None})
    if st["done"]:
        return
    log(f"Sel {cell.id}  target={cell.quota}  (sudah {st['n']})")
    stats = store.ck["meta"]["stats"]

    while True:
        need = min(cell.quota - st["n"], target - store.count)
        if need <= 0:
            break

        size = min(C.MAX_RESULTS_CAP, max(C.MIN_RESULTS, need))  # min API = 10
        if C.MAX_POSTS_PER_WINDOW:
            size = min(size, C.MAX_POSTS_PER_WINDOW)  # satu halaman tidak boleh melebihi batas post/window
        allowed = store.allowed_posts()
        if allowed < C.MIN_RESULTS:
            store.save_ck()
            raise BudgetStop(f"Budget lokal ${C.BUDGET_USD:.2f} hampir habis "
                             f"({store.billed} post ditagih).")
        size = min(size, allowed)

        params = {
            "query": cell.query,
            "start_time": iso(cell.start),
            "end_time": iso(cell.end),
            "max_results": size,
            "tweet.fields": C.TWEET_FIELDS,
        }
        if st["next_token"]:
            params["next_token"] = st["next_token"]

        data = client.get(params)
        returned = data.get("data", [])
        store.ck["meta"]["billed_posts"] += len(returned)  # ditagih per post yang dikembalikan

        rows = []
        residual = any(g["name"] == cell.group and g["share"] is None for g in C.GROUPS)
        for t in returned:
            if residual:
                if store.count >= target:
                    break
            elif len(rows) >= need:
                break
            why = store.check(t)
            if why:
                stats[why] += 1
                continue
            store.register(t, cell.group)  # daftar langsung: cegah duplikat dalam halaman yang sama
            rows.append(to_row(t, cell.group, store.month))
        store.append(rows)  # tulis CSV dulu, baru checkpoint

        st["n"] += len(rows)
        st["next_token"] = data.get("meta", {}).get("next_token")
        store.save_ck()
        log(f"  +{len(rows)} (API kirim {len(returned)}) | sel {st['n']}/{cell.quota} "
            f"| total {store.count}/{target} | ~${store.billed * C.PRICE_PER_POST:.2f} "
            f"| req ke-{client.n_requests}")
        if not st["next_token"]:
            break  # data di jendela ini habis

    st["done"], st["next_token"] = True, None
    store.save_ck()
    if st["n"] < cell.quota:
        log(f"  (kurang {cell.quota - st['n']} post; sisanya diisi kelompok 'umum')")


# =====================================================================
# Rencana / dry-run
# =====================================================================
def print_plan(month: str, target: int, start: datetime, period_end: datetime) -> None:
    quotas = group_quotas(target)
    est = target * C.PRICE_PER_POST
    print(f"Bulan   : {month}  [{iso(start)} s/d {iso(period_end)}) UTC")
    print(f"Target  : {target} post unik | lang:{C.LANG} | tanpa retweet")
    print(f"Estimasi: {target} x ${C.PRICE_PER_POST} = ${est:.2f}  "
          f"(budget lokal ${C.BUDGET_USD:.2f}, sudah terpakai ${C.SPENT_BEFORE_USD:.2f})\n")
    for i, g in enumerate(C.GROUPS):
        q = quotas[g["name"]]
        cells, spare = plan_group(i, q, start, period_end, month)
        print(f"== {g['name']}  kuota={q}  sel={len(cells)}  "
              f"{'(menyerap sisa kelompok lain)' if g['share'] is None else ''}")
        print(f"   query ({len(build_query(i))} char): {build_query(i)}")
        for c in cells[:4]:
            print(f"   {iso(c.start)} -> {iso(c.end)}  kuota={c.quota}")
        if len(cells) > 4:
            print(f"   ... {len(cells)} sel")
        if spare:
            print(f"   cadangan (sisa irisan, tidak tumpang tindih): {len(spare)} jendela")
        print()


def confirm(args, month: str, target: int) -> bool:
    if args.yes:
        return True
    print(f"\nAkan memanggil API BERBAYAR: bulan {month}, target {target} post "
          f"(maks ~${target * C.PRICE_PER_POST:.2f}, budget lokal ${C.BUDGET_USD:.2f}).")
    return input("Lanjutkan? (YA/tidak): ").strip() == "YA"


def write_ids(store: Store) -> None:
    out_path = C.IDS_DIR / f"post_ids_{store.month}.csv"
    with open(store.csv_path, newline="", encoding="utf-8-sig") as f, \
            open(out_path, "w", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        w.writerow(["id"])
        for row in csv.DictReader(f):
            w.writerow([row["id"]])
    log(f"ID post disimpan di {out_path}")


# =====================================================================
# Main
# =====================================================================
def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Fetch post X (full-archive) untuk riset parenting.")
    ap.add_argument("--month", default=C.DEFAULT_MONTH, help="YYYY-MM (default: %(default)s)")
    ap.add_argument("--target", type=int, default=C.TARGET_PER_MONTH, help="jumlah post unik")
    ap.add_argument("--dry-run", action="store_true", help="tampilkan rencana tanpa memanggil API")
    ap.add_argument("--smoke-test", action="store_true", help="1 request kecil (10 post) untuk uji token/query")
    ap.add_argument("--yes", action="store_true", help="lewati konfirmasi interaktif")
    args = ap.parse_args()

    validate_month(args.month)
    month, target = args.month, args.target

    try:
        from dotenv import load_dotenv
        load_dotenv(C.ROOT / ".env")
    except ImportError:
        pass

    start, month_end = month_bounds(month)
    now_limit = datetime.now(timezone.utc) - timedelta(seconds=C.END_SAFETY_SEC)

    if args.dry_run:
        print_plan(month, target, start, min(month_end, now_limit).replace(microsecond=0))
        return 0

    token = os.environ.get("X_BEARER_TOKEN") or getpass.getpass("Masukkan Bearer Token X: ")
    client = XClient(token)

    if args.smoke_test:
        period_end = min(month_end, now_limit).replace(microsecond=0)
        if not confirm(args, month, 10):
            print("Dibatalkan.")
            return 0
        try:
            data = client.get({"query": build_query(len(C.GROUPS) - 1), "start_time": iso(start),
                               "end_time": iso(period_end), "max_results": 10,
                               "tweet.fields": C.TWEET_FIELDS})
        except FatalAPIError as e:
            log(f"GAGAL: {e}")
            return 2
        posts = data.get("data", [])
        log(f"OK: {len(posts)} post, ~${len(posts) * C.PRICE_PER_POST:.2f}. "
            f"Sisa kuota rate: {client.last_headers.get('x-rate-limit-remaining')}")
        for t in posts:
            print(f"  - {t['created_at']} [{t.get('lang')}] {t['text'][:100]!r}")
        return 0

    store = Store(month)
    meta = store.ck["meta"]
    if "period_end" not in meta:  # bekukan batas akhir supaya resume memakai sel yang sama
        meta["period_end"] = iso(min(month_end, now_limit).replace(microsecond=0))
        store.save_ck()
    period_end = parse_iso(meta["period_end"])

    if store.count >= target:
        log(f"Sudah lengkap: {store.count}/{target}.")
        return 0
    if not confirm(args, month, target):
        print("Dibatalkan. Tidak ada request API.")
        return 0

    quotas = group_quotas(target)
    umum_idx = next(i for i, g in enumerate(C.GROUPS) if g["share"] is None)
    umum_name = C.GROUPS[umum_idx]["name"]

    try:
        # 1) kelompok spesifik dulu (urutan = prioritas)
        for i, g in enumerate(C.GROUPS):
            if g["share"] is None:
                continue
            cells, _ = plan_group(i, quotas[g["name"]], start, period_end, month)
            for c in cells:
                fetch_cell(client, store, c, target)

        # 2) kelompok umum mengisi sisa (termasuk kekurangan kelompok langka)
        if "umum_target" not in meta:
            meta["umum_target"] = target - store.count
            store.save_ck()
        log(f"Kelompok spesifik terkumpul {store.count}; target '{umum_name}' = {meta['umum_target']}")
        cells, spare = plan_group(umum_idx, meta["umum_target"], start, period_end, month)
        cells = [cells[i] for i in spread_order(len(cells))]
        for c in cells:
            fetch_cell(client, store, c, target)

        # 3) cadangan bila masih kurang: pakai sisa irisan yang belum pernah dicari
        if store.count < target:
            if "fill_plan" not in meta:
                fills = plan_fill(umum_idx, target - store.count, spare, month)
                meta["fill_plan"] = [[c.id, c.quota, iso(c.start), iso(c.end)] for c in fills]
                store.save_ck()
            for cid, q, s, e in meta["fill_plan"]:
                if store.count < target:
                    fetch_cell(client, store,
                               Cell(cid, umum_name, build_query(umum_idx), parse_iso(s), parse_iso(e), q),
                               target)

    except BudgetStop as e:
        log(f"BERHENTI (budget): {e} Checkpoint tersimpan.")
        return 3
    except FatalAPIError as e:
        log(f"BERHENTI: {e}")
        log("Checkpoint tersimpan; jalankan ulang untuk melanjutkan.")
        return 2
    except KeyboardInterrupt:
        log("Dihentikan manual; checkpoint tersimpan.")
        return 130

    write_ids(store)
    st = meta["stats"]
    log("=== SELESAI ===")
    for g, n in store.by_group.items():
        log(f"  {g:<12} {n}")
    log(f"Total unik: {store.count}/{target} | request sesi ini: {client.n_requests}")
    log(f"Ditolak: duplikat id={st['dup_id']}, duplikat teks={st['dup_text']}, bahasa lain={st['bad_lang']}")
    log(f"Post ditagih: {store.billed} (~${store.billed * C.PRICE_PER_POST:.2f}); cek biaya aktual di Developer Console.")
    if store.count < target:
        log("Catatan: data lang:id pada periode ini kurang dari target.")
    return 0


if __name__ == "__main__":
    sys.exit(main())