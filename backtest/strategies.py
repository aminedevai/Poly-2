"""
backtest/strategies.py
======================
Backtest strategies using REAL p30 prices from CLOB API.

fetch_data.py now stores actual mid-prices at:
  p30  = UP token price 30s before close  <- MR entry signal
  p60  = UP token price 60s before close
  p120 = UP token price 120s before close
  p240 = UP token price at open (baseline for sniper)

MR logic:
  - Fire when abs(p30 - 0.50) > trigger_dist
  - Bet the CHEAP side (fade the extreme)
  - Hold to settlement

Sniper logic:
  - Fire when volume spike detected (proxy: high volume + price already moved)
  - Bet the direction of the move
  - Hold to settlement

Markets without real p30 data are skipped cleanly.
"""
from typing import Optional
from backtest.engine import TradeResult


# ── 1. Mean Reversion ─────────────────────────────────────────────────────────

class MeanReversionBacktest:
    """
    Real MR backtest using actual p30 prices from CLOB history.

    Entry: price deviates > trigger_dist from 0.50 at 30s before close
    Direction: fade the extreme (UP high -> bet DOWN, UP low -> bet UP)
    Exit: hold to settlement (outcome = UP or DOWN at 1.0)

    Markets with no p30 data are skipped.
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

        outcome = market.get("outcome")
        if not outcome:
            return None

        volume = market.get("volume", 0)
        if volume < self.min_volume:
            return None

        # Require real p30 price — skip if CLOB data unavailable
        p30 = market.get("p30")
        if p30 is None:
            return None

        # MR trigger: price must be far enough from fair value
        deviation = abs(p30 - 0.50)
        if deviation < self.trigger_dist:
            return None

        # Fade: buy the cheap side
        if p30 > 0.50:
            direction   = "DOWN"
            entry_price = 1.0 - p30          # price of DOWN token at p30
        else:
            direction   = "UP"
            entry_price = p30                # price of UP token at p30

        # Safety clamp — should not be needed with real data but just in case
        entry_price = max(0.01, min(0.99, entry_price))

        won    = (direction == outcome)
        shares = self.bet_size / entry_price
        profit = (shares - self.bet_size) if won else -self.bet_size

        return TradeResult(
            slug        = market["slug"],
            open_dt     = market["open_dt"],
            direction   = direction,
            entry_price = entry_price,
            outcome     = outcome,
            won         = won,
            profit      = round(profit, 4),
            bet_size    = self.bet_size,
            roi_pct     = profit / self.bet_size * 100,
            volume      = volume,
            url         = market.get("url", ""),
            signal_data = {
                "p30":        round(p30, 4),
                "deviation":  round(deviation, 4),
                "trigger":    self.trigger_dist,
                "p60":        market.get("p60"),
                "p240":       market.get("p240"),
                "n_pts":      market.get("n_price_pts", 0),
            },
        )


# ── 2. Volume Spike Sniper ─────────────────────────────────────────────────────

class SniperBacktest:
    """
    Momentum strategy using real p30 price + volume.

    Signal: volume > threshold AND price moved > min_move from 0.50 at p30
    Direction: follow the move (momentum)
    Exit: hold to settlement
    """
    name = "Volume Spike Sniper"

    def __init__(self, min_volume: float = 5000.0, min_move: float = 0.12,
                 bet_size: float = 10.0):
        self.min_volume = min_volume
        self.min_move   = min_move
        self.bet_size   = bet_size

    def on_market(self, market: dict, capital: float) -> Optional[TradeResult]:
        if capital < self.bet_size:
            return None

        outcome = market.get("outcome")
        if not outcome:
            return None

        volume = market.get("volume", 0)
        if volume < self.min_volume:
            return None

        p30 = market.get("p30")
        if p30 is None:
            return None

        move = abs(p30 - 0.50)
        if move < self.min_move:
            return None

        # Follow momentum
        direction   = "UP" if p30 > 0.50 else "DOWN"
        entry_price = p30 if direction == "UP" else (1.0 - p30)
        entry_price = max(0.01, min(0.99, entry_price))

        won    = (direction == outcome)
        shares = self.bet_size / entry_price
        profit = (shares - self.bet_size) if won else -self.bet_size

        return TradeResult(
            slug        = market["slug"],
            open_dt     = market["open_dt"],
            direction   = direction,
            entry_price = entry_price,
            outcome     = outcome,
            won         = won,
            profit      = round(profit, 4),
            bet_size    = self.bet_size,
            roi_pct     = profit / self.bet_size * 100,
            volume      = volume,
            url         = market.get("url", ""),
            signal_data = {
                "p30":        round(p30, 4),
                "move":       round(move, 4),
                "min_volume": self.min_volume,
            },
        )


# ── 3. Control — always bet DOWN ───────────────────────────────────────────────

class AlwaysBetDown:
    """
    Baseline: always bet DOWN, regardless of price.
    Uses real p30 for entry price. Requires p30 data.
    Expected win rate ~50%. Use to benchmark other strategies against.
    """
    name = "Always Bet DOWN (control)"

    def __init__(self, bet_size: float = 10.0, min_volume: float = 100.0):
        self.bet_size   = bet_size
        self.min_volume = min_volume

    def on_market(self, market: dict, capital: float) -> Optional[TradeResult]:
        if capital < self.bet_size:
            return None

        outcome = market.get("outcome")
        if not outcome:
            return None

        if market.get("volume", 0) < self.min_volume:
            return None

        p30 = market.get("p30")
        if p30 is None:
            return None

        direction   = "DOWN"
        entry_price = max(0.01, min(0.99, 1.0 - p30))
        won         = (direction == outcome)
        shares      = self.bet_size / entry_price
        profit      = (shares - self.bet_size) if won else -self.bet_size

        return TradeResult(
            slug        = market["slug"],
            open_dt     = market["open_dt"],
            direction   = direction,
            entry_price = entry_price,
            outcome     = outcome,
            won         = won,
            profit      = round(profit, 4),
            bet_size    = self.bet_size,
            roi_pct     = profit / self.bet_size * 100,
            volume      = market.get("volume", 0),
            url         = market.get("url", ""),
        )


# ── 4. MR with p60 entry (earlier signal) ─────────────────────────────────────

class MeanReversionP60:
    """
    Same as MR but uses p60 (60s before close) as entry signal.
    Gives slightly worse entry price but earlier signal.
    Use to compare timing sensitivity.
    """
    name = "Mean Reversion (p60)"

    def __init__(self, trigger_dist: float = 0.05, bet_size: float = 10.0,
                 min_volume: float = 100.0):
        self.trigger_dist = trigger_dist
        self.bet_size     = bet_size
        self.min_volume   = min_volume

    def on_market(self, market: dict, capital: float) -> Optional[TradeResult]:
        if capital < self.bet_size:
            return None

        outcome = market.get("outcome")
        if not outcome:
            return None

        if market.get("volume", 0) < self.min_volume:
            return None

        p60 = market.get("p60")
        if p60 is None:
            return None

        deviation = abs(p60 - 0.50)
        if deviation < self.trigger_dist:
            return None

        direction   = "DOWN" if p60 > 0.50 else "UP"
        entry_price = max(0.01, min(0.99, (1.0 - p60) if p60 > 0.50 else p60))
        won         = (direction == outcome)
        shares      = self.bet_size / entry_price
        profit      = (shares - self.bet_size) if won else -self.bet_size

        return TradeResult(
            slug        = market["slug"],
            open_dt     = market["open_dt"],
            direction   = direction,
            entry_price = entry_price,
            outcome     = outcome,
            won         = won,
            profit      = round(profit, 4),
            bet_size    = self.bet_size,
            roi_pct     = profit / self.bet_size * 100,
            volume      = market.get("volume", 0),
            url         = market.get("url", ""),
            signal_data = {
                "p60":       round(p60, 4),
                "deviation": round(deviation, 4),
            },
        )


# ── Registry ───────────────────────────────────────────────────────────────────

STRATEGIES = {
    "mean_reversion":    MeanReversionBacktest,
    "mean_reversion_p60": MeanReversionP60,
    "sniper":            SniperBacktest,
    "control_down":      AlwaysBetDown,
}
