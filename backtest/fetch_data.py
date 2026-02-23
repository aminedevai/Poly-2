"""
backtest/fetch_data.py
======================
Pulls historical BTC 5-min markets from Polymarket Gamma API.
Stores raw data as JSON in backtest/data/ directory.

Usage:
    python -m backtest.fetch_data --days 7
    python -m backtest.fetch_data --from 2025-02-16 --to 2025-02-23
"""
import argparse, json, os, time, requests
from datetime import datetime, timezone, timedelta

DATA_DIR   = os.path.join(os.path.dirname(__file__), "data")
GAMMA_API  = "https://gamma-api.polymarket.com"
SLUG_PREFIX = "btc-updown-5m"


def ts_range(start_dt: datetime, end_dt: datetime):
    """Yield all 5-min open timestamps between start and end."""
    # 5-min candles: every 300 seconds
    t = int(start_dt.timestamp())
    t = (t // 300) * 300   # align to 5-min grid
    end = int(end_dt.timestamp())
    while t <= end:
        yield t
        t += 300


def fetch_slug(slug: str) -> dict | None:
    """Fetch a single market's full data from Gamma API."""
    try:
        r = requests.get(
            f"{GAMMA_API}/markets",
            params={"slug": slug},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = r.json()
        if data and isinstance(data, list):
            return data[0]
        return None
    except Exception as e:
        return None


def parse_market(raw: dict, slug: str) -> dict | None:
    """Extract the fields we care about for backtesting."""
    import json as _json
    try:
        prices   = raw.get("outcomePrices", [])
        outcomes = raw.get("outcomes", [])
        if isinstance(prices,   str): prices   = _json.loads(prices)
        if isinstance(outcomes, str): outcomes = _json.loads(outcomes)
        if len(prices) < 2: return None

        # Identify UP index
        up_idx = next((i for i, o in enumerate(outcomes)
                       if "up" in str(o).lower()), 0)

        # Determine outcome from resolved prices
        p0, p1 = float(prices[0]), float(prices[1])
        outcome = None
        if abs(p0 - 1.0) < 0.01:
            outcome = "UP" if "up" in str(outcomes[0]).lower() else "DOWN"
        elif abs(p1 - 1.0) < 0.01:
            outcome = "UP" if "up" in str(outcomes[1]).lower() else "DOWN"

        open_ts = int(slug.split("-")[-1])
        return {
            "slug":       slug,
            "open_ts":    open_ts,
            "close_ts":   open_ts + 300,
            "open_dt":    datetime.fromtimestamp(open_ts, tz=timezone.utc).isoformat(),
            "close_dt":   datetime.fromtimestamp(open_ts + 300, tz=timezone.utc).isoformat(),
            "outcome":    outcome,
            "volume":     float(raw.get("volumeNum", 0) or 0),
            "up_price_final": float(prices[up_idx]),
            "down_price_final": float(prices[1 - up_idx]),
            "title":      raw.get("question", raw.get("title", slug)),
            "url":        f"https://polymarket.com/event/{slug}",
        }
    except Exception as e:
        return None


def fetch_range(start_dt: datetime, end_dt: datetime,
                delay: float = 0.15) -> list:
    """
    Fetch all 5-min BTC markets in a date range.
    Returns list of parsed market dicts.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    results = []
    timestamps = list(ts_range(start_dt, end_dt))
    total = len(timestamps)

    print(f"\n  Fetching {total} markets "
          f"({start_dt.strftime('%Y-%m-%d')} -> {end_dt.strftime('%Y-%m-%d')})")
    print(f"  Estimated time: ~{total * delay / 60:.1f} min at {delay}s/req\n")

    for i, ts in enumerate(timestamps):
        slug = f"{SLUG_PREFIX}-{ts}"
        raw  = fetch_slug(slug)
        if raw is None:
            if i % 50 == 0:
                print(f"  [{i:4d}/{total}] {slug}  — skip (no data)")
            time.sleep(delay)
            continue

        parsed = parse_market(raw, slug)
        if parsed and parsed["outcome"]:
            results.append(parsed)
            if i % 20 == 0:
                print(f"  [{i:4d}/{total}] {slug}  "
                      f"vol=${parsed['volume']:,.0f}  "
                      f"outcome={parsed['outcome']}")
        time.sleep(delay)

    return results


def save(markets: list, label: str) -> str:
    """Save list of markets to JSON file. Returns file path."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"markets_{label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(markets, f, indent=2)
    print(f"\n  Saved {len(markets)} markets -> {path}")
    return path


def load(path: str) -> list:
    """Load markets from a JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_saved() -> list:
    """Return list of available saved dataset files."""
    os.makedirs(DATA_DIR, exist_ok=True)
    files = [f for f in os.listdir(DATA_DIR) if f.endswith(".json")]
    return sorted(files)


# -- CLI -----------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch historical Polymarket BTC 5-min data")
    parser.add_argument("--days",  type=int, default=7,
                        help="Number of past days to fetch (default: 7)")
    parser.add_argument("--from",  dest="date_from",
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--to",    dest="date_to",
                        help="End date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)

    if args.date_from:
        start = datetime.fromisoformat(args.date_from).replace(tzinfo=timezone.utc)
        end   = (datetime.fromisoformat(args.date_to).replace(tzinfo=timezone.utc)
                 if args.date_to else now)
    else:
        end   = now
        start = now - timedelta(days=args.days)

    label   = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    markets = fetch_range(start, end)
    save(markets, label)
    print(f"\n  Done — {len(markets)} resolved markets fetched.")
