"""
core/btc_leaderboard.py — BTC 5-Min Market Top Traders (Feature D)
====================================================================
Fetches recent trades from Polymarket's BTC Up/Down 5-min markets
and ranks wallets by profit over the last 1 hour and 30 minutes.

Endpoints used:
  - gamma-api: list recent BTC 5-min markets
  - data-api:  trades per market (token_id)
  - Aggregates per wallet: PnL, win rate, trade count, avg entry

Refreshes every REFRESH_INTERVAL seconds (default 120s — API-friendly).
"""

import time
import requests
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
from utils import logger

log = logger.get("leaderboard")

REFRESH_INTERVAL = 120      # seconds between full refreshes
WINDOWS = {
    "1h":   3600,
    "30m":  1800,
}
MIN_VOLUME = 10.0           # min $ traded to appear on leaderboard
TOP_N      = 15             # wallets shown per window


def _now() -> int:
    return int(time.time())


def _get_btc_5min_market_ids(limit: int = 20) -> List[dict]:
    """
    Return recent BTC Up/Down 5-min market token IDs from Gamma API.
    Looks at markets with slug pattern btc-updown-5m-*.
    """
    try:
        r = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={
                "tag":    "crypto",
                "limit":  limit,
                "order":  "createdAt",
                "asc":    "false",
            },
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        markets = r.json()
        if not isinstance(markets, list):
            return []

        btc_markets = []
        for m in markets:
            slug = m.get("slug", "")
            if "btc-updown-5m" not in slug:
                continue
            # Extract token IDs
            cond_ids = m.get("conditionId") or m.get("conditionIds") or []
            if isinstance(cond_ids, str):
                cond_ids = [cond_ids]
            # Also try clob_token_ids
            token_ids = m.get("clobTokenIds") or m.get("clob_token_ids") or []
            if isinstance(token_ids, str):
                import json
                try:
                    token_ids = json.loads(token_ids)
                except Exception:
                    token_ids = []
            btc_markets.append({
                "slug":      slug,
                "cond_ids":  cond_ids,
                "token_ids": token_ids,
                "end_ts":    m.get("endDateIso", ""),
            })
        log.debug(f"Found {len(btc_markets)} BTC 5-min markets")
        return btc_markets

    except Exception as e:
        log.debug(f"_get_btc_5min_market_ids: {e}")
        return []


def _fetch_trades_for_token(token_id: str, since_ts: int, limit: int = 200) -> List[dict]:
    """Fetch trades for a specific token since a timestamp."""
    try:
        r = requests.get(
            "https://data-api.polymarket.com/trades",
            params={
                "asset_id": token_id,
                "limit":    limit,
            },
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        trades = r.json()
        if not isinstance(trades, list):
            return []
        # Filter by timestamp
        return [
            t for t in trades
            if int(t.get("timestamp", 0)) >= since_ts
        ]
    except Exception as e:
        log.debug(f"_fetch_trades_for_token({token_id[:10]}): {e}")
        return []


def _fetch_market_trades(slug: str, since_ts: int) -> List[dict]:
    """
    Fetch trades for a BTC market using the activity endpoint filtered by slug.
    Falls back to trades endpoint if slug search works.
    """
    try:
        r = requests.get(
            "https://data-api.polymarket.com/trades",
            params={
                "market": slug,
                "limit":  500,
            },
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        data = r.json()
        if not isinstance(data, list):
            return []
        return [
            t for t in data
            if int(t.get("timestamp", 0)) >= since_ts
        ]
    except Exception as e:
        log.debug(f"_fetch_market_trades({slug}): {e}")
        return []


class WalletStats:
    """Aggregate stats for one wallet across all BTC 5-min trades."""
    def __init__(self, wallet: str):
        self.wallet    = wallet
        self.trades    = 0
        self.wins      = 0
        self.total_pnl = 0.0
        self.bought    = 0.0   # total $ spent
        self.sold      = 0.0   # total $ received
        self.entries:  List[float] = []
        self.sides:    Dict[str, int] = defaultdict(int)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades > 0 else 0.0

    @property
    def avg_entry(self) -> float:
        return sum(self.entries) / len(self.entries) if self.entries else 0.0

    @property
    def net_pnl(self) -> float:
        return self.sold - self.bought

    def to_dict(self) -> dict:
        return {
            "wallet":    self.wallet,
            "wallet_short": self.wallet[:6] + "..." + self.wallet[-4:],
            "trades":    self.trades,
            "wins":      self.wins,
            "win_rate":  round(self.win_rate, 3),
            "total_pnl": round(self.total_pnl, 2),
            "net_pnl":   round(self.net_pnl, 2),
            "avg_entry": round(self.avg_entry, 3),
            "bought":    round(self.bought, 2),
            "dominant_side": max(self.sides, key=self.sides.get) if self.sides else "?",
        }


class BtcLeaderboard:
    """
    Polls BTC 5-min market trades and maintains a live leaderboard
    of top profitable wallets for the last 1h and 30min windows.
    """

    def __init__(self):
        self._last_refresh   = 0.0
        self.leaderboard_1h  : List[dict] = []
        self.leaderboard_30m : List[dict] = []
        self.last_markets_seen: int = 0
        self.last_trades_seen:  int = 0
        self.last_updated:      float = 0.0
        self.is_refreshing:     bool = False
        log.info("BTC LEADERBOARD init")

    def _aggregate_trades(
        self, all_trades: List[dict], since_ts: int
    ) -> List[dict]:
        """Aggregate trades by wallet and return sorted leaderboard."""
        wallet_stats: Dict[str, WalletStats] = {}

        for trade in all_trades:
            ts = int(trade.get("timestamp", 0))
            if ts < since_ts:
                continue

            # Try multiple field names for wallet
            wallet = (
                trade.get("maker")
                or trade.get("taker")
                or trade.get("transactorAddress")
                or trade.get("trader")
                or ""
            ).lower()
            if not wallet or len(wallet) < 10:
                continue

            if wallet not in wallet_stats:
                wallet_stats[wallet] = WalletStats(wallet)

            ws = wallet_stats[wallet]
            ws.trades += 1

            size  = float(trade.get("size",  0) or 0)
            price = float(trade.get("price", 0) or 0)
            side  = trade.get("side", "BUY").upper()
            notional = size * price

            if side in ("BUY", "LONG"):
                ws.bought += notional
                if price > 0:
                    ws.entries.append(price)
                ws.sides["BUY"] += 1
            else:
                ws.sold += notional
                ws.sides["SELL"] += 1

            # profit from trade data if available
            profit = float(trade.get("profit", 0) or 0)
            ws.total_pnl += profit
            if profit > 0:
                ws.wins += 1

        # Use net_pnl (sold - bought) as primary sort metric
        # Filter by minimum activity
        ranked = [
            ws.to_dict()
            for ws in wallet_stats.values()
            if ws.bought >= MIN_VOLUME or ws.sold >= MIN_VOLUME
        ]

        # Sort by net_pnl descending — top profit wallets first
        ranked.sort(key=lambda x: x["net_pnl"], reverse=True)
        return ranked[:TOP_N]

    def refresh(self) -> bool:
        """
        Fetch fresh data and rebuild leaderboards.
        Returns True if refresh succeeded.
        """
        if self.is_refreshing:
            return False
        self.is_refreshing = True

        try:
            now = _now()
            since_1h  = now - WINDOWS["1h"]
            since_30m = now - WINDOWS["30m"]

            # Get BTC 5-min markets from last 2 hours
            markets = _get_btc_5min_market_ids(limit=30)
            self.last_markets_seen = len(markets)

            all_trades: List[dict] = []

            # Fetch trades per market (token-level)
            for market in markets:
                slug = market["slug"]
                # Try slug-based fetch
                trades = _fetch_market_trades(slug, since_1h)
                all_trades.extend(trades)

                # Also try token_id based fetch if available
                for token_id in market.get("token_ids", [])[:2]:
                    if token_id:
                        t2 = _fetch_trades_for_token(str(token_id), since_1h)
                        # Deduplicate by id
                        existing_ids = {t.get("id") for t in all_trades if t.get("id")}
                        for t in t2:
                            if t.get("id") not in existing_ids:
                                all_trades.append(t)

            self.last_trades_seen = len(all_trades)
            log.info(
                f"LEADERBOARD refresh: {self.last_markets_seen} markets  "
                f"{self.last_trades_seen} trades"
            )

            self.leaderboard_1h  = self._aggregate_trades(all_trades, since_1h)
            self.leaderboard_30m = self._aggregate_trades(all_trades, since_30m)
            self._last_refresh   = time.time()
            self.last_updated    = time.time()

            if self.leaderboard_1h:
                top = self.leaderboard_1h[0]
                log.info(
                    f"LEADERBOARD #1 (1h): {top['wallet_short']}  "
                    f"trades={top['trades']}  net_pnl=${top['net_pnl']:+.2f}  "
                    f"WR={top['win_rate']:.0%}"
                )
            return True

        except Exception as e:
            log.error(f"LEADERBOARD refresh error: {e}")
            return False
        finally:
            self.is_refreshing = False

    def maybe_refresh(self) -> bool:
        """Refresh if interval elapsed. Returns True if refresh was triggered."""
        if time.time() - self._last_refresh >= REFRESH_INTERVAL:
            return self.refresh()
        return False

    def summary(self) -> dict:
        return {
            "leaderboard_1h":    self.leaderboard_1h,
            "leaderboard_30m":   self.leaderboard_30m,
            "last_updated":      self.last_updated,
            "markets_seen":      self.last_markets_seen,
            "trades_seen":       self.last_trades_seen,
            "refresh_interval":  REFRESH_INTERVAL,
            "is_refreshing":     self.is_refreshing,
        }
