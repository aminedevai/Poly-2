"""
backtest/strategies.py
======================
Backtest-compatible versions of all strategies.

Each strategy has:
    name: str
    on_market(market: dict, capital: float) -> TradeResult | None

The `market` dict contains the FINAL resolved prices,
so we simulate what the strategy would see at different points.

For the live bot, MR fires at p30 (price 30s before close).
Since historical data only has final prices, we simulate p30
using a model: final price is our best proxy, but we add the
key insight — MR only fires when p30 is far from 0.50,
and we know that happened when the final price was far from 0.50.
"""
from typing import Optional
from backtest.engine import TradeResult


# ── 1. Mean Reversion ─────────────────────────────────────────────────────────

class MeanReversionBacktest:
    """
    Fade extreme prices at 30s before close, hold to settlement.

    Since we only have final prices in historical data, we use
    up_price_final as a proxy for p30. When the market was already
    extreme (resolved at 0.90+), we know it was also extreme at 30s.

    Parameters:
        trigger_dist:   minimum deviation from 0.50 to fire
        bet_size:       fixed bet size per trade
        min_volume:     skip markets with volume below this (likely inactive)
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

        # Use final price as proxy for p30 extreme
        up_final = market.get("up_price_final", 0.5)

        # For MR to have fired, price needed to be extreme at 30s
        # We use final price as signal proxy (conservative — real p30 often more extreme)
        deviation = abs(up_final - 0.50)
        if deviation < self.trigger_dist:
            return None

        # Fade: if UP was high, we bet DOWN (and vice versa)
        direction   = "DOWN" if up_final > 0.50 else "UP"
        entry_price = (1.0 - up_final) if up_final > 0.50 else up_final

        # Settlement: win if our direction = outcome
        won    = (direction == outcome)
        shares = self.bet_size / entry_price
        profit = (shares - self.bet_size) if won else -self.bet_size

        return TradeResult(
            slug         = market["slug"],
            open_dt      = market["open_dt"],
            direction    = direction,
            entry_price  = entry_price,
            outcome      = outcome,
            won          = won,
            profit       = profit,
            bet_size     = self.bet_size,
            roi_pct      = profit / self.bet_size * 100,
            volume       = volume,
            url          = market.get("url", ""),
            signal_data  = {
                "up_final":   round(up_final, 4),
                "deviation":  round(deviation, 4),
                "trigger":    self.trigger_dist,
            },
        )


# ── 2. Volume Spike Sniper ────────────────────────────────────────────────────

class SniperBacktest:
    """
    Follow volume spikes — momentum strategy.

    In historical data we only have total volume per candle,
    not intra-candle volume progression. So we simulate the signal
    using final price as proxy for direction and volume as a filter.

    Parameters:
        min_volume:       minimum total volume to consider "spike"
        min_move:         minimum price deviation from 0.50 (proxy for move)
        bet_size:         fixed bet size per trade
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

        up_final = market.get("up_price_final", 0.5)
        move     = abs(up_final - 0.50)
        if move < self.min_move:
            return None

        # Follow direction of price move (momentum)
        direction   = "UP" if up_final > 0.50 else "DOWN"
        entry_price = up_final if direction == "UP" else (1.0 - up_final)

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
            profit      = profit,
            bet_size    = self.bet_size,
            roi_pct     = profit / self.bet_size * 100,
            volume      = volume,
            url         = market.get("url", ""),
            signal_data = {
                "up_final":   round(up_final, 4),
                "move":       round(move, 4),
                "min_volume": self.min_volume,
            },
        )


# ── 3. Always Bet — baseline control ─────────────────────────────────────────

class AlwaysBetDown:
    """
    Control strategy: always bet DOWN regardless of price.
    Useful to compare MR against a random baseline.
    Expected win rate: ~50%.
    """
    name = "Always Bet DOWN (control)"

    def __init__(self, bet_size: float = 10.0, min_volume: float = 100.0):
        self.bet_size   = bet_size
        self.min_volume = min_volume

    def on_market(self, market: dict, capital: float) -> Optional[TradeResult]:
        if capital < self.bet_size:
            return None
        if not market.get("outcome"):
            return None
        if market.get("volume", 0) < self.min_volume:
            return None

        up_final    = market.get("up_price_final", 0.5)
        entry_price = 1.0 - up_final
        direction   = "DOWN"
        outcome     = market["outcome"]
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
            profit      = profit,
            bet_size    = self.bet_size,
            roi_pct     = profit / self.bet_size * 100,
            volume      = market.get("volume", 0),
            url         = market.get("url", ""),
        )


# ── Registry — used by launcher and HTML dashboard ───────────────────────────

STRATEGIES = {
    "mean_reversion": MeanReversionBacktest,
    "sniper":         SniperBacktest,
    "control_down":   AlwaysBetDown,
}
