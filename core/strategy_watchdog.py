"""
core/strategy_watchdog.py — Strategy Decay Watchdog (Feature C)
================================================================
Applies the same rolling win-rate decay detection from the copy-trading
article to all 7 bot strategies. Runs every cycle and emits alerts when
any strategy shows signs of degradation.

Detection logic (same as WalletScorer):
  - Rolling 20-trade WR < strategy's expected threshold → DECAY ALERT
  - Rolling 20-trade WR < 80% of all-time WR → DECAY ALERT
  - If strategy fires 0 trades in last 60min → STALE ALERT

Does NOT stop strategies automatically — it logs alerts and surfaces them
in the dashboard. You decide what to do.
"""

import time
from typing import Dict, List, Optional, Tuple
from utils import logger

log = logger.get("watchdog")

WINDOW          = 20          # rolling window (trades)
DECAY_ATH_RATIO = 0.80        # rolling WR must be >= 80% of all-time
STALE_SECS      = 3600        # if no new trades for 1h, flag as stale

# Per-strategy expected win-rate thresholds (from backtest calibration)
STRATEGY_THRESHOLDS = {
    "sniper":      0.55,
    "mr":          0.65,
    "contrarian":  0.52,
    "momentum":    0.50,
    "always_down": 0.49,
    "last_second": 0.65,
    "basket":      0.55,
}


class StrategyDecayAlert:
    def __init__(self, strategy: str, alert_type: str, message: str):
        self.strategy   = strategy
        self.alert_type = alert_type   # "DECAY" | "STALE" | "OK"
        self.message    = message
        self.ts         = time.time()

    def __str__(self):
        ts = time.strftime("%H:%M:%S", time.localtime(self.ts))
        icon = "⚠️ " if self.alert_type in ("DECAY", "STALE") else "✅"
        return f"[{ts}] {icon} {self.strategy.upper()} — {self.message}"


class StrategyWatchdog:
    """
    Checks all strategies each cycle for win-rate decay and staleness.
    Call .check(strategies_dict) from main loop.
    """

    def __init__(self):
        self._last_trade_count: Dict[str, int]   = {}
        self._last_trade_time:  Dict[str, float] = {}
        self.alerts:            List[StrategyDecayAlert] = []
        self.status:            Dict[str, dict]  = {}  # strategy -> status dict
        log.info("WATCHDOG init")

    def _rolling_wr(self, closed_trades: List[dict], window: int = WINDOW) -> float:
        recent = closed_trades[-window:]
        if not recent:
            return 0.0
        wins = sum(
            1 for t in recent
            if t.get("status") in ("won", "settled_win")
            or (t.get("profit", 0)) > 0
        )
        return wins / len(recent)

    def _alltime_wr(self, closed_trades: List[dict]) -> float:
        if not closed_trades:
            return 0.0
        wins = sum(
            1 for t in closed_trades
            if t.get("status") in ("won", "settled_win")
            or (t.get("profit", 0)) > 0
        )
        return wins / len(closed_trades)

    def _check_one(
        self, name: str, summary: dict
    ) -> StrategyDecayAlert:
        """Check a single strategy summary dict. Returns an alert."""
        closed   = summary.get("closed_trades", [])
        n        = len(closed)
        n_open   = summary.get("n_open", 0)
        threshold = STRATEGY_THRESHOLDS.get(name, 0.50)

        prev_n = self._last_trade_count.get(name, 0)
        if n > prev_n:
            self._last_trade_time[name] = time.time()
        self._last_trade_count[name] = n

        # Stale check
        last_trade = self._last_trade_time.get(name, time.time())
        secs_since = time.time() - last_trade
        if n > 5 and secs_since > STALE_SECS and n_open == 0:
            msg = f"No new trades in {secs_since/3600:.1f}h — may not be firing"
            alert = StrategyDecayAlert(name, "STALE", msg)
            log.warning(f"WATCHDOG STALE  {name}  {msg}")
            return alert

        # Need minimum trades to detect decay
        if n < WINDOW:
            msg = f"Accumulating history ({n}/{WINDOW} trades)"
            return StrategyDecayAlert(name, "OK", msg)

        rolling = self._rolling_wr(closed, WINDOW)
        alltime = self._alltime_wr(closed)

        reasons = []
        if rolling < threshold:
            reasons.append(
                f"rolling-{WINDOW} WR {rolling:.1%} < threshold {threshold:.0%}"
            )
        if alltime > 0 and rolling < alltime * DECAY_ATH_RATIO:
            reasons.append(
                f"rolling {rolling:.1%} < 80% of all-time {alltime:.1%}"
            )

        if reasons:
            msg = " | ".join(reasons)
            log.warning(f"WATCHDOG DECAY  {name}  {msg}")
            return StrategyDecayAlert(name, "DECAY", msg)

        msg = (
            f"rolling={rolling:.1%}  alltime={alltime:.1%}  "
            f"threshold={threshold:.0%}  trades={n}"
        )
        return StrategyDecayAlert(name, "OK", msg)

    def check(self, strategies: Dict[str, object]) -> List[StrategyDecayAlert]:
        """
        Pass a dict of {name: strategy_instance}.
        Calls .summary() on each and checks for decay.
        Returns list of new alerts (DECAY or STALE only).
        """
        new_alerts = []
        for name, strat in strategies.items():
            try:
                summary = strat.summary()
            except Exception as e:
                log.debug(f"WATCHDOG {name} summary(): {e}")
                continue

            alert = self._check_one(name, summary)
            pnl   = summary.get("pnl", 0)
            wr    = summary.get("win_rate", 0)
            n     = summary.get("n_closed", 0)
            rolling = self._rolling_wr(summary.get("closed_trades", []), WINDOW)

            self.status[name] = {
                "name":        name,
                "alert_type":  alert.alert_type,
                "message":     alert.message,
                "n_closed":    n,
                "win_rate":    round(wr, 3),
                "rolling_wr":  round(rolling, 3),
                "pnl":         round(pnl, 2),
                "threshold":   STRATEGY_THRESHOLDS.get(name, 0.50),
                "ts":          alert.ts,
            }

            if alert.alert_type in ("DECAY", "STALE"):
                self.alerts.append(alert)
                self.alerts = self.alerts[-100:]  # keep last 100
                new_alerts.append(alert)

        return new_alerts

    def get_status_list(self) -> List[dict]:
        """Return all strategy statuses sorted by alert severity."""
        order = {"DECAY": 0, "STALE": 1, "OK": 2}
        return sorted(
            self.status.values(),
            key=lambda x: order.get(x["alert_type"], 3)
        )

    def recent_alerts(self, n: int = 10) -> List[str]:
        """Return last N alert strings (DECAY/STALE only)."""
        return [str(a) for a in self.alerts[-n:]]
