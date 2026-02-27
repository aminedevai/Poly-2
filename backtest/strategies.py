"""
backtest/strategies.py
======================
Honest backtest strategies.

DATA REALITY CHECK:
  The fetched dataset has p30=null for all markets because:
  - Old fetcher used `condition_id` (wrong) instead of `clobTokenIds[up_idx]`
  - New fetcher (v7+) uses the correct field
  - Re-fetch your dataset to get real p30 prices

  DO NOT USE SYNTHETIC p30 — it is always circular:
    synthetic: outcome=UP -> p30=0.62 (UP side high)
    sniper:    p30=0.62 -> bet UP -> outcome=UP -> 100% win
    MR:        p30=0.62 -> bet DOWN -> outcome=UP -> 0% win
  Neither result means anything. It's a tautology, not a backtest.

STRATEGIES WITHOUT p30 (work with current dataset):
  - AlwaysBetUP / AlwaysBetDown: ~50% win rate baseline
  - VolumeContrarian: bet opposite of high-volume direction (tests if volume
    is a contrarian signal — high volume markets may already be "priced in")

STRATEGIES WITH p30 (require re-fetch with correct clobTokenIds):
  - MeanReversionBacktest: real MR using p30 deviation signal
  - SniperBacktest: momentum using p30 direction + volume
"""
from typing import Optional
from backtest.engine import TradeResult

# Realistic entry price assumption when we don't have p30.
# BTC 5-min markets near expiry typically trade 0.38-0.62 for the losing side.
# We use $0.40 as a conservative entry (cheap side, MR style).
DEFAULT_ENTRY = 0.40


# ── Strategies requiring real p30 ─────────────────────────────────────────────

class MeanReversionBacktest:
    """
    Fade extreme prices at T-30s. REQUIRES real p30 from CLOB.
    Skip markets where p30 is None (no data or not re-fetched yet).
    """
    name = "Mean Reversion"

    def __init__(self, trigger_dist: float = 0.05, bet_size: float = 10.0,
                 min_volume: float = 100.0):
        self.trigger_dist = trigger_dist
        self.bet_size     = bet_size
        self.min_volume   = min_volume

    def on_market(self, market: dict, capital: float) -> Optional[TradeResult]:
        if capital < self.bet_size:
            return None
        if not market.get("outcome"):
            return None
        if market.get("volume", 0) < self.min_volume:
            return None

        p30 = market.get("p30")
        # Skip if no real p30 — refuse to use synthetic data
        if p30 is None or not market.get("has_real_p30", False):
            return None

        deviation = abs(p30 - 0.50)
        if deviation < self.trigger_dist:
            return None

        direction   = "DOWN" if p30 > 0.50 else "UP"
        entry_price = max(0.01, min(0.99, (1.0 - p30) if p30 > 0.50 else p30))
        won         = (direction == market["outcome"])
        shares      = self.bet_size / entry_price
        profit      = round((shares - self.bet_size) if won else -self.bet_size, 4)

        return TradeResult(
            slug        = market["slug"],
            open_dt     = market["open_dt"],
            direction   = direction,
            entry_price = entry_price,
            outcome     = market["outcome"],
            won         = won,
            profit      = profit,
            bet_size    = self.bet_size,
            roi_pct     = profit / self.bet_size * 100,
            volume      = market.get("volume", 0),
            url         = market.get("url", ""),
            signal_data = {
                "p30": round(p30, 4),
                "deviation": round(deviation, 4),
                "trigger": self.trigger_dist,
            },
        )


class SniperBacktest:
    """
    Momentum strategy: follow p30 direction on high-volume markets.
    REQUIRES real p30 from CLOB.
    """
    name = "Volume Spike Sniper"

    def __init__(self, min_volume: float = 5000.0, min_move: float = 0.10,
                 bet_size: float = 10.0):
        self.min_volume = min_volume
        self.min_move   = min_move
        self.bet_size   = bet_size

    def on_market(self, market: dict, capital: float) -> Optional[TradeResult]:
        if capital < self.bet_size:
            return None
        if not market.get("outcome"):
            return None
        if market.get("volume", 0) < self.min_volume:
            return None

        p30 = market.get("p30")
        if p30 is None or not market.get("has_real_p30", False):
            return None

        move = abs(p30 - 0.50)
        if move < self.min_move:
            return None

        direction   = "UP" if p30 > 0.50 else "DOWN"
        entry_price = max(0.01, min(0.99, p30 if direction == "UP" else 1.0 - p30))
        won         = (direction == market["outcome"])
        shares      = self.bet_size / entry_price
        profit      = round((shares - self.bet_size) if won else -self.bet_size, 4)

        return TradeResult(
            slug        = market["slug"],
            open_dt     = market["open_dt"],
            direction   = direction,
            entry_price = entry_price,
            outcome     = market["outcome"],
            won         = won,
            profit      = profit,
            bet_size    = self.bet_size,
            roi_pct     = profit / self.bet_size * 100,
            volume      = market.get("volume", 0),
            url         = market.get("url", ""),
            signal_data = {"p30": round(p30, 4), "move": round(move, 4)},
        )


# ── Honest baseline strategies (work without p30) ─────────────────────────────

class AlwaysBetDown:
    """
    Baseline: always bet DOWN at a fixed entry price.
    Expected win rate: ~50% (DOWN wins ~50% of BTC 5-min markets).
    Use this to confirm the backtest engine is working correctly.
    If this shows != 50%, something else is wrong.
    """
    name = "Always Bet DOWN (baseline)"

    def __init__(self, bet_size: float = 10.0, min_volume: float = 100.0,
                 entry_price: float = DEFAULT_ENTRY):
        self.bet_size    = bet_size
        self.min_volume  = min_volume
        self.entry_price = entry_price

    def on_market(self, market: dict, capital: float) -> Optional[TradeResult]:
        if capital < self.bet_size:
            return None
        outcome = market.get("outcome")
        if not outcome:
            return None
        if market.get("volume", 0) < self.min_volume:
            return None

        won    = (outcome == "DOWN")
        shares = self.bet_size / self.entry_price
        profit = round((shares - self.bet_size) if won else -self.bet_size, 4)

        return TradeResult(
            slug        = market["slug"],
            open_dt     = market["open_dt"],
            direction   = "DOWN",
            entry_price = self.entry_price,
            outcome     = outcome,
            won         = won,
            profit      = profit,
            bet_size    = self.bet_size,
            roi_pct     = profit / self.bet_size * 100,
            volume      = market.get("volume", 0),
            url         = market.get("url", ""),
        )


class VolumeContrarian:
    """
    Contrarian hypothesis: extremely high-volume markets are already 'priced in'
    and more likely to reverse. Bet UP on very high-volume markets (theory:
    high volume = panic buying DOWN, so UP is actually cheap).

    This is a TESTABLE hypothesis using only outcome + volume (no p30 needed).
    If win rate > 55% on high-volume markets, there's a real contrarian edge.

    Entry price: fixed at $0.40 (conservative — buying the out-of-favor side).
    """
    name = "Volume Contrarian (UP on high-vol)"

    def __init__(self, bet_size: float = 10.0, min_volume: float = 50_000.0,
                 entry_price: float = DEFAULT_ENTRY):
        self.bet_size    = bet_size
        self.min_volume  = min_volume
        self.entry_price = entry_price

    def on_market(self, market: dict, capital: float) -> Optional[TradeResult]:
        if capital < self.bet_size:
            return None
        outcome = market.get("outcome")
        if not outcome:
            return None
        volume = market.get("volume", 0)
        if volume < self.min_volume:
            return None

        # Contrarian: always bet UP (theory: DOWN panic on high-vol reverses)
        direction = "UP"
        won       = (outcome == "UP")
        shares    = self.bet_size / self.entry_price
        profit    = round((shares - self.bet_size) if won else -self.bet_size, 4)

        return TradeResult(
            slug        = market["slug"],
            open_dt     = market["open_dt"],
            direction   = direction,
            entry_price = self.entry_price,
            outcome     = outcome,
            won         = won,
            profit      = profit,
            bet_size    = self.bet_size,
            roi_pct     = profit / self.bet_size * 100,
            volume      = volume,
            url         = market.get("url", ""),
            signal_data = {"volume": volume, "min_volume": self.min_volume},
        )


class HighVolumeMomentum:
    """
    Momentum hypothesis: very high-volume markets continue their move.
    On markets with volume > threshold, bet DOWN (the most common 'panic' side).
    Opposite of VolumeContrarian — tests which direction high volume predicts.
    """
    name = "High Volume Momentum (DOWN on high-vol)"

    def __init__(self, bet_size: float = 10.0, min_volume: float = 150_000.0,
                 entry_price: float = DEFAULT_ENTRY):
        self.bet_size    = bet_size
        self.min_volume  = min_volume
        self.entry_price = entry_price

    def on_market(self, market: dict, capital: float) -> Optional[TradeResult]:
        if capital < self.bet_size:
            return None
        outcome = market.get("outcome")
        if not outcome:
            return None
        volume = market.get("volume", 0)
        if volume < self.min_volume:
            return None

        direction = "DOWN"
        won       = (outcome == "DOWN")
        shares    = self.bet_size / self.entry_price
        profit    = round((shares - self.bet_size) if won else -self.bet_size, 4)

        return TradeResult(
            slug        = market["slug"],
            open_dt     = market["open_dt"],
            direction   = direction,
            entry_price = self.entry_price,
            outcome     = outcome,
            won         = won,
            profit      = profit,
            bet_size    = self.bet_size,
            roi_pct     = profit / self.bet_size * 100,
            volume      = volume,
            url         = market.get("url", ""),
            signal_data = {"volume": volume, "min_volume": self.min_volume},
        )


STRATEGIES = {
    "mean_reversion":       MeanReversionBacktest,
    "sniper":               SniperBacktest,
    "control_down":         AlwaysBetDown,
    "volume_contrarian":    VolumeContrarian,
    "high_vol_momentum":    HighVolumeMomentum,
}

