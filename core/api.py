"""core/api.py — All Polymarket API calls in one place."""
import json, requests
from utils.config import APIS
from utils import logger

log = logger.get("api")


def fetch_market(slug: str) -> tuple:
    """
    Fetch live data for a single market slug.
    Returns (up_price, outcome, volume).
    All values are None/None/0.0 on failure or when market has no data yet.
    """
    try:
        r = requests.get(
            f"{APIS['gamma']}/markets",
            params={"slug": slug},
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = r.json()
        if not data or not isinstance(data, list):
            return None, None, 0.0

        m        = data[0]
        prices   = m.get("outcomePrices", [])
        outcomes = m.get("outcomes", [])
        if isinstance(prices,   str): prices   = json.loads(prices)
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        if len(prices) < 2 or len(outcomes) < 2:
            return None, None, 0.0

        # Detect resolved market (one price at 1.00)
        p0, p1  = float(prices[0]), float(prices[1])
        outcome = None
        if abs(p0 - 1.0) < 0.01:
            outcome = "UP" if "up" in str(outcomes[0]).lower() else "DOWN"
        elif abs(p1 - 1.0) < 0.01:
            outcome = "UP" if "up" in str(outcomes[1]).lower() else "DOWN"

        up_idx   = next((i for i, o in enumerate(outcomes)
                         if "up" in str(o).lower()), 0)
        up_p     = float(prices[up_idx])
        up_price = up_p if 0.01 < up_p < 0.99 else None
        volume   = float(m.get("volumeNum", 0) or 0)
        return up_price, outcome, volume

    except Exception as e:
        log.debug(f"fetch_market({slug}): {e}")
        return None, None, 0.0


def fetch_wallet_positions(wallet: str) -> dict:
    """
    Fetch all open positions for a wallet address.
    Returns dict keyed by "{slug}_{outcome}".
    """
    try:
        r = requests.get(
            "https://data-api.polymarket.com/positions",
            params={"user": wallet, "sizeThreshold": "0.01"},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        raw = r.json()
        if not isinstance(raw, list):
            return {}

        out = {}
        for p in raw:
            if float(p.get("size", 0)) < 0.01:
                continue
            slug    = p.get("market", "")
            outcome = p.get("outcome", "").upper()
            key     = f"{slug}_{outcome}"
            out[key] = {
                "slug":         slug,
                "outcome":      outcome,
                "condition_id": p.get("conditionId", ""),
                "market_title": p.get("title", slug),
                "avg_price":    float(p.get("avgPrice",  0) or 0),
                "cur_price":    float(p.get("curPrice",  0) or 0),
                "shares":       float(p.get("size",      0) or 0),
                "end_date":     p.get("endDate", ""),
            }
        return out

    except Exception as e:
        log.error(f"fetch_wallet_positions({wallet}): {e}")
        return {}
