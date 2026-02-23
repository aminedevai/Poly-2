"""
backtest/fetch_data.py
======================
Fetches historical BTC 5-min markets from Polymarket.

HOW IT WORKS:
  1. Gamma API  -> get market metadata + condition_id + outcome
  2. CLOB API   -> get actual price history during the candle's 5 minutes
  3. We extract p30 (price 30s before close) = the real MR entry signal

The CLOB price history endpoint:
  GET https://clob.polymarket.com/prices-history
  ?market={condition_id}&startTs={open_ts}&endTs={close_ts}&fidelity=1

  Returns minute-by-minute (or finer) CLOB mid-prices for the UP token.
  We take the price at close_ts - 30s as p30.

This gives us REAL backtesting data, not synthetic proxies.
"""
import argparse, json, os, time, requests
from datetime import datetime, timezone, timedelta

# Force UTF-8 on Windows
import sys
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

DATA_DIR    = os.path.join(os.path.dirname(__file__), "data")
GAMMA_API   = "https://gamma-api.polymarket.com"
CLOB_API    = "https://clob.polymarket.com"
SLUG_PREFIX = "btc-updown-5m"


def ts_range(start_dt: datetime, end_dt: datetime):
    """Yield all 5-min open timestamps in range, aligned to 5-min grid."""
    t   = (int(start_dt.timestamp()) // 300) * 300
    end = int(end_dt.timestamp())
    while t <= end:
        yield t
        t += 300


# ── Step 1: Gamma API — metadata + outcome ────────────────────────────────────

def fetch_gamma(slug: str) -> dict | None:
    """Fetch market metadata from Gamma API."""
    try:
        r = requests.get(
            f"{GAMMA_API}/markets",
            params={"slug": slug},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = r.json()
        return data[0] if data and isinstance(data, list) else None
    except Exception:
        return None


# ── Step 2: CLOB API — actual price history ────────────────────────────────────

def fetch_price_history(condition_id: str, open_ts: int, close_ts: int) -> list:
    """
    Fetch UP token price history for a specific market window.
    Returns list of {t: timestamp, p: price} dicts, sorted by time.
    fidelity=1 = 1-minute candles (finest available for historical).
    """
    try:
        r = requests.get(
            f"{CLOB_API}/prices-history",
            params={
                "market":    condition_id,
                "startTs":   open_ts,
                "endTs":     close_ts,
                "fidelity":  1,
            },
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = r.json()
        history = data.get("history", [])
        # Normalise: each point is {t: unix_ts, p: float_price}
        return sorted(
            [{"t": int(pt.get("t", 0)), "p": float(pt.get("p", 0))}
             for pt in history if pt.get("t") and pt.get("p") is not None],
            key=lambda x: x["t"]
        )
    except Exception:
        return []


def price_at(history: list, target_ts: int, tolerance: int = 90) -> float | None:
    """
    Find the price closest to target_ts within tolerance seconds.
    Returns None if no point is close enough.
    """
    best = None
    best_diff = tolerance + 1
    for pt in history:
        diff = abs(pt["t"] - target_ts)
        if diff < best_diff:
            best_diff = diff
            best = pt["p"]
    return best if best_diff <= tolerance else None


# ── Step 3: Parse everything into one market record ────────────────────────────

def parse_market(raw: dict, slug: str) -> dict | None:
    """
    Build a full market record from Gamma metadata + CLOB price history.
    Returns dict with real p30, p60, p120 prices (not synthetic proxies).
    """
    import json as _json
    try:
        prices   = raw.get("outcomePrices", [])
        outcomes = raw.get("outcomes",      [])
        if isinstance(prices,   str): prices   = _json.loads(prices)
        if isinstance(outcomes, str): outcomes = _json.loads(outcomes)
        if len(prices) < 2: return None

        # Identify UP token index
        up_idx = next((i for i, o in enumerate(outcomes)
                       if "up" in str(o).lower()), 0)

        # Determine settlement outcome
        p0, p1  = float(prices[0]), float(prices[1])
        outcome = None
        if abs(p0 - 1.0) < 0.01:
            outcome = "UP"   if "up" in str(outcomes[0]).lower() else "DOWN"
        elif abs(p1 - 1.0) < 0.01:
            outcome = "DOWN" if "up" in str(outcomes[0]).lower() else "UP"
        if not outcome:
            return None

        open_ts  = int(slug.split("-")[-1])
        close_ts = open_ts + 300

        # Get condition_id for CLOB lookup
        condition_id = raw.get("conditionId", "")
        if not condition_id:
            # Try clobTokenIds
            token_ids = raw.get("clobTokenIds", [])
            if isinstance(token_ids, str):
                try: token_ids = _json.loads(token_ids)
                except: token_ids = []
            condition_id = token_ids[up_idx] if token_ids and len(token_ids) > up_idx else ""

        # Fetch real CLOB price history
        history = []
        if condition_id:
            history = fetch_price_history(condition_id, open_ts, close_ts)
            time.sleep(0.05)  # be polite to CLOB API

        # Extract prices at key snapshot moments
        p30  = price_at(history, close_ts - 30,  tolerance=60)
        p60  = price_at(history, close_ts - 60,  tolerance=60)
        p120 = price_at(history, close_ts - 120, tolerance=90)
        p240 = price_at(history, open_ts,         tolerance=90)

        # Fallback: if CLOB returned nothing, we cannot do real MR backtest
        # Store None so strategies can skip this market
        volume = float(raw.get("volumeNum", 0) or 0)

        return {
            "slug":        slug,
            "open_ts":     open_ts,
            "close_ts":    close_ts,
            "open_dt":     datetime.fromtimestamp(open_ts,  tz=timezone.utc).isoformat(),
            "close_dt":    datetime.fromtimestamp(close_ts, tz=timezone.utc).isoformat(),
            "outcome":     outcome,
            "volume":      volume,
            "condition_id": condition_id,
            # Real CLOB prices (None if unavailable)
            "p30":         round(p30,  4) if p30  is not None else None,
            "p60":         round(p60,  4) if p60  is not None else None,
            "p120":        round(p120, 4) if p120 is not None else None,
            "p240":        round(p240, 4) if p240 is not None else None,
            # How many price points we got from CLOB
            "n_price_pts": len(history),
            "url":         f"https://polymarket.com/event/{slug}",
        }
    except Exception:
        return None


# ── Fetch range ────────────────────────────────────────────────────────────────

def fetch_range(start_dt: datetime, end_dt: datetime,
                delay: float = 0.2) -> list:
    """
    Fetch all 5-min BTC markets + real CLOB prices in a date range.
    delay is per-market (2 API calls each: Gamma + CLOB).
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    results    = []
    timestamps = list(ts_range(start_dt, end_dt))
    total      = len(timestamps)
    n_clob     = 0

    print(f"\n  Fetching {total} markets "
          f"({start_dt.strftime('%Y-%m-%d')} -> {end_dt.strftime('%Y-%m-%d')})")
    print(f"  Each market: Gamma API + CLOB price history")
    print(f"  Est. time: ~{total * (delay + 0.2) / 60:.1f} min\n")

    for i, ts in enumerate(timestamps):
        slug = f"{SLUG_PREFIX}-{ts}"
        raw  = fetch_gamma(slug)
        if raw is None:
            time.sleep(delay)
            continue

        parsed = parse_market(raw, slug)
        if parsed and parsed["outcome"]:
            results.append(parsed)
            has_clob = parsed["n_price_pts"] > 0
            if has_clob: n_clob += 1
            if i % 10 == 0 or has_clob:
                p30_s = f"p30={parsed['p30']:.3f}" if parsed["p30"] is not None else "p30=--"
                print(f"  [{i:4d}/{total}] {slug[-14:]}  "
                      f"vol=${parsed['volume']:>8,.0f}  "
                      f"{p30_s}  "
                      f"outcome={parsed['outcome']}  "
                      f"pts={parsed['n_price_pts']}")
        time.sleep(delay)

    print(f"\n  Done: {len(results)} markets, {n_clob} with real CLOB prices")
    return results


# ── Save / load ────────────────────────────────────────────────────────────────

def save(markets: list, label: str) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"markets_{label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(markets, f, indent=2)
    print(f"  Saved {len(markets)} markets -> {path}")
    return path


def load(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_saved() -> list:
    os.makedirs(DATA_DIR, exist_ok=True)
    return sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".json"))


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch historical Polymarket BTC 5-min data")
    parser.add_argument("--days",      type=int, default=7)
    parser.add_argument("--from",      dest="date_from")
    parser.add_argument("--to",        dest="date_to")
    parser.add_argument("--delay",     type=float, default=0.2,
                        help="Seconds between requests (default 0.2)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    if args.date_from:
        start = datetime.fromisoformat(args.date_from).replace(tzinfo=timezone.utc)
        end   = datetime.fromisoformat(args.date_to).replace(tzinfo=timezone.utc) if args.date_to else now
    else:
        end   = now
        start = now - timedelta(days=args.days)

    label   = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    markets = fetch_range(start, end, delay=args.delay)
    save(markets, label)
