"""
backtest/engine.py
==================
Core backtesting engine.
Replays historical market data through any strategy and returns full results.

A strategy is any object with:
    strategy.on_market(market: dict) -> TradeResult | None

Where `market` is a dict from fetch_data with fields:
    slug, open_ts, close_ts, outcome, volume,
    up_price_final, down_price_final, url, ...
"""
from dataclasses import dataclass, field
from typing import List, Optional, Callable
from datetime import datetime


@dataclass
class TradeResult:
    slug:        str
    open_dt:     str
    direction:   str          # "UP" or "DOWN"
    entry_price: float
    outcome:     str          # "UP" or "DOWN"
    won:         bool
    profit:      float        # net $ profit on bet
    bet_size:    float
    roi_pct:     float
    volume:      float
    url:         str
    signal_data: dict = field(default_factory=dict)  # extra strategy-specific fields


@dataclass
class BacktestResult:
    strategy_name: str
    date_from:     str
    date_to:       str
    n_markets:     int        # total markets in range
    n_signals:     int        # times strategy fired
    n_wins:        int
    n_losses:      int
    starting_capital: float
    final_capital: float
    total_pnl:     float
    win_rate:      float
    roi_pct:       float      # total ROI on starting capital
    avg_profit:    float
    max_drawdown:  float
    trades:        List[TradeResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "strategy_name":    self.strategy_name,
            "date_from":        self.date_from,
            "date_to":          self.date_to,
            "n_markets":        self.n_markets,
            "n_signals":        self.n_signals,
            "n_wins":           self.n_wins,
            "n_losses":         self.n_losses,
            "starting_capital": round(self.starting_capital, 2),
            "final_capital":    round(self.final_capital, 2),
            "total_pnl":        round(self.total_pnl,   2),
            "win_rate":         round(self.win_rate,     4),
            "roi_pct":          round(self.roi_pct,      2),
            "avg_profit":       round(self.avg_profit,   2),
            "max_drawdown":     round(self.max_drawdown, 2),
            "trades": [
                {
                    "slug":        t.slug,
                    "open_dt":     t.open_dt,
                    "direction":   t.direction,
                    "entry_price": round(t.entry_price, 4),
                    "outcome":     t.outcome,
                    "won":         t.won,
                    "profit":      round(t.profit, 2),
                    "bet_size":    t.bet_size,
                    "roi_pct":     round(t.roi_pct, 2),
                    "volume":      round(t.volume, 2),
                    "url":         t.url,
                    **t.signal_data,
                }
                for t in self.trades
            ],
        }


def run(markets: list, strategy, starting_capital: float = 100.0) -> BacktestResult:
    """
    Replay a list of historical markets through a strategy.

    Args:
        markets:           list of market dicts from fetch_data
        strategy:          strategy object with on_market(market) -> TradeResult|None
        starting_capital:  initial paper capital

    Returns:
        BacktestResult with full trade log and statistics
    """
    trades:  List[TradeResult] = []
    capital  = starting_capital
    peak     = starting_capital
    max_dd   = 0.0

    markets_sorted = sorted(markets, key=lambda m: m["open_ts"])

    for mkt in markets_sorted:
        if not mkt.get("outcome"):
            continue

        result = strategy.on_market(mkt, capital)
        if result is None:
            continue

        capital += result.profit
        trades.append(result)

        # Track drawdown
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    n       = len(trades)
    n_wins  = sum(1 for t in trades if t.won)
    total   = sum(t.profit for t in trades)

    # Date range from actual data
    date_from = (datetime.fromisoformat(markets_sorted[0]["open_dt"]).strftime("%Y-%m-%d")
                 if markets_sorted else "")
    date_to   = (datetime.fromisoformat(markets_sorted[-1]["open_dt"]).strftime("%Y-%m-%d")
                 if markets_sorted else "")

    return BacktestResult(
        strategy_name    = strategy.name,
        date_from        = date_from,
        date_to          = date_to,
        n_markets        = len(markets_sorted),
        n_signals        = n,
        n_wins           = n_wins,
        n_losses         = n - n_wins,
        starting_capital = starting_capital,
        final_capital    = capital,
        total_pnl        = total,
        win_rate         = n_wins / n if n else 0,
        roi_pct          = (capital - starting_capital) / starting_capital * 100,
        avg_profit       = total / n if n else 0,
        max_drawdown     = max_dd * 100,
        trades           = trades,
    )
