"""
backtest/fetch_data.py
======================
Fast concurrent fetcher for historical BTC 5-min Polymarket markets.

SPEED: ThreadPoolExecutor with 12 workers = ~10x faster than sequential.
       2 days (~575 markets): ~2 min. 7 days (~2000 markets): ~5 min.

DATA SOURCES:
  1. Gamma API  -> outcome, volume, clobTokenIds (UP token id)
  2. CLOB API   -> real price history using clobTokenIds[up_idx]
                   (NOT conditionId — that was the old bug causing p30=null)

p30 FIELD:
  - p30 is ONLY set if we get real data from CLOB
  - p30=null means no real data available (market too recent, or CLOB gap)
  - NO synthetic p30 — synthetic data produces circular, meaningless results
  - Strategies that need p30 skip markets where p30=null

RE-FETCH TIMING:
  CLOB prices-history typically has data for markets older than ~6-24 hours.
  For very recent data (< 6h), p30 may still be null even with correct token_id.
  Re-fetch the same date range the next day to fill in gaps.
"""
import argparse, json, os, requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

DATA_DIR    = os.path.join(os.path.dirname(__file__), "data")
GAMMA_API   = "https://gamma-api.polymarket.com"
CLOB_API    = "https://clob.polymarket.com"
SLUG_PREFIX = "btc-updown-5m"

_print_lock = Lock()
def log(msg: str):
    with _print_lock:
        print(msg, flush=True)


# ── Gamma fetch ───────────────────────────────────────────────────────────────

def fetch_gamma(slug: str) -> dict | None:
    try:
        r = requests.get(
            f"{GAMMA_API}/markets",
            params={"slug": slug},
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = r.json()
        return data[0] if data and isinstance(data, list) else None
    except Exception:
        return None


# ── CLOB price history ────────────────────────────────────────────────────────

def fetch_clob_history(token_id: str, open_ts: int, close_ts: int) -> list:
    """
    Fetch UP token price history using clobTokenIds[up_idx].
    This is the correct field — NOT conditionId.
    fidelity=1 = finest available resolution.
    """
    if not token_id:
        return []
    try:
        r = requests.get(
            f"{CLOB_API}/prices-history",
            params={
                "market":   token_id,       # <- clobTokenIds[up_idx], not conditionId
                "startTs":  open_ts  - 120,
                "endTs":    close_ts + 120,
                "fidelity": 1,
            },
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        pts = r.json().get("history", [])
        return sorted(
            [{"t": int(p["t"]), "p": float(p["p"])}
             for p in pts if p.get("t") and p.get("p") is not None],
            key=lambda x: x["t"]
        )
    except Exception:
        return []


def price_at(history: list, target_ts: int, tolerance: int = 150) -> float | None:
    """Nearest price within tolerance seconds. Returns None if nothing close."""
    best, best_diff = None, tolerance + 1
    for pt in history:
        d = abs(pt["t"] - target_ts)
        if d < best_diff:
            best_diff, best = d, pt["p"]
    return best if best_diff <= tolerance else None


# ── Parse one market ──────────────────────────────────────────────────────────

def parse_one(slug: str) -> dict | None:
    import json as _j
    raw = fetch_gamma(slug)
    if not raw:
        return None
    try:
        prices   = raw.get("outcomePrices", [])
        outcomes = raw.get("outcomes", [])
        if isinstance(prices,   str): prices   = _j.loads(prices)
        if isinstance(outcomes, str): outcomes = _j.loads(outcomes)
        if len(prices) < 2:
            return None

        up_idx = next((i for i, o in enumerate(outcomes)
                       if "up" in str(o).lower()), 0)

        p0, p1 = float(prices[0]), float(prices[1])
        outcome = None
        if   abs(p0 - 1.0) < 0.02: outcome = "UP"   if "up" in str(outcomes[0]).lower() else "DOWN"
        elif abs(p1 - 1.0) < 0.02: outcome = "DOWN" if "up" in str(outcomes[0]).lower() else "UP"
        if not outcome:
            return None

        open_ts  = int(slug.split("-")[-1])
        close_ts = open_ts + 300
        volume   = float(raw.get("volumeNum", 0) or 0)

        # CORRECT field for CLOB prices-history: clobTokenIds, not conditionId
        token_ids = raw.get("clobTokenIds", [])
        if isinstance(token_ids, str):
            try:    token_ids = _j.loads(token_ids)
            except: token_ids = []
        up_token = token_ids[up_idx] if token_ids and len(token_ids) > up_idx else ""

        # Fetch real CLOB price history
        history  = fetch_clob_history(up_token, open_ts, close_ts)
        p30      = price_at(history, close_ts - 30,  tolerance=150)
        p60      = price_at(history, close_ts - 60,  tolerance=150)
        p120     = price_at(history, close_ts - 120, tolerance=150)
        p240     = price_at(history, open_ts,         tolerance=150)

        has_real = p30 is not None

        return {
            "slug":         slug,
            "open_ts":      open_ts,
            "close_ts":     close_ts,
            "open_dt":      datetime.fromtimestamp(open_ts,  tz=timezone.utc).isoformat(),
            "close_dt":     datetime.fromtimestamp(close_ts, tz=timezone.utc).isoformat(),
            "outcome":      outcome,
            "volume":       volume,
            "up_token":     up_token,
            # p30=None means no real CLOB data — do NOT synthesize
            "p30":          round(p30,  4) if p30  is not None else None,
            "p60":          round(p60,  4) if p60  is not None else None,
            "p120":         round(p120, 4) if p120 is not None else None,
            "p240":         round(p240, 4) if p240 is not None else None,
            "n_price_pts":  len(history),
            "has_real_p30": has_real,
            "url":          f"https://polymarket.com/event/{slug}",
        }
    except Exception:
        return None


# ── Timestamp range ───────────────────────────────────────────────────────────

def ts_range(start_dt: datetime, end_dt: datetime):
    t   = (int(start_dt.timestamp()) // 300) * 300
    end = int(end_dt.timestamp())
    while t <= end:
        yield t
        t += 300


# ── Fast concurrent fetch ─────────────────────────────────────────────────────

def fetch_range(start_dt: datetime, end_dt: datetime, workers: int = 12) -> list:
    slugs = [f"{SLUG_PREFIX}-{ts}" for ts in ts_range(start_dt, end_dt)]
    total = len(slugs)

    log(f"\n  Fetching {total} markets  "
        f"({start_dt.strftime('%Y-%m-%d')} -> {end_dt.strftime('%Y-%m-%d')})")
    log(f"  {workers} parallel workers  |  "
        f"Est. ~{max(1, total // workers // 6)}-{max(2, total // workers // 3)} min\n")

    results = []
    done    = 0
    n_real  = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(parse_one, s): s for s in slugs}
        for fut in as_completed(futures):
            done += 1
            r = fut.result()
            if r and r.get("outcome"):
                results.append(r)
                if r.get("has_real_p30"):
                    n_real += 1
            if done % 100 == 0 or done == total:
                log(f"  [{done:>4}/{total}]  markets={len(results)}  "
                    f"with_real_p30={n_real}  no_p30={len(results)-n_real}")

    results.sort(key=lambda m: m["open_ts"])
    n_no = len(results) - n_real
    log(f"\n  Done: {len(results)} markets")
    log(f"  Real CLOB p30: {n_real}  |  No p30 (too recent / gap): {n_no}")
    if n_no > 0 and n_real == 0:
        log(f"  TIP: Re-fetch this dataset in 6-24h to get real p30 prices.")
    return results


# ── Save / load ───────────────────────────────────────────────────────────────

def save(markets: list, label: str) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"markets_{label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(markets, f, indent=2)
    log(f"  Saved {len(markets)} markets -> {path}")
    return path


def load(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_saved() -> list:
    os.makedirs(DATA_DIR, exist_ok=True)
    return sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".json"))


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fast Polymarket BTC 5-min data fetcher")
    parser.add_argument("--days",    type=int, default=7)
    parser.add_argument("--from",    dest="date_from")
    parser.add_argument("--to",      dest="date_to")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    if args.date_from:
        start = datetime.fromisoformat(args.date_from).replace(tzinfo=timezone.utc)
        end   = datetime.fromisoformat(args.date_to).replace(tzinfo=timezone.utc) if args.date_to else now
    else:
        end, start = now, now - timedelta(days=args.days)

    label   = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    markets = fetch_range(start, end, workers=min(args.workers, 20))
    save(markets, label)
