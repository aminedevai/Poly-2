"""
strategy/mean_reversion.py — Mean Reversion (Fade Late Moves)

Logic:
  At 30 seconds before close, if the UP token price has moved
  >5% away from $0.50, buy the CHEAP side and hold to settlement.

  Example: UP = 0.72 → buy DOWN at 0.28
           If market settles DOWN → payout $1.00/share = +$25.71 profit
           (vs $10 bet at 0.28 entry)

Why hold to settlement instead of exiting at 10s?
  - Exiting at 10s gives ~27% of max profit (price rarely reverts fully)
  - Settling gives 100% of max profit with only 1 taker fee
  - At 96% win rate, EV per trade is ~$22 vs ~$6

Budget: $100 paper, $10 per trade.
"""
import csv, os
from datetime import datetime
from typing import Dict, List

from core.models   import MRTrade
from utils.config  import MR, PATHS
from utils import logger

log    = logger.get("mr")
FILE   = PATHS["mr_csv"]
FIELDS = ["time", "slug", "direction", "p30", "p10", "entry_price",
          "exit_price", "bet_size", "profit", "roi_pct",
          "exit_type", "outcome", "status"]


class MeanReversion:
    def __init__(self):
        self.capital        = MR["budget"]
        self.session_start  = self.capital
        self.open_trades:   Dict[str, MRTrade] = {}
        self.closed_trades: List[MRTrade]      = []
        self._fired: set = set()
        self._ensure_csv()

    def _ensure_csv(self):
        os.makedirs(PATHS["logs"], exist_ok=True)
        if not os.path.exists(FILE):
            with open(FILE, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=FIELDS).writeheader()

    def _save(self, t: MRTrade, exit_type: str):
        with open(FILE, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writerow({
                "time":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "slug":        t.slug,
                "direction":   t.direction,
                "p30":         round(t.p30,         4),
                "p10":         round(t.p10,         4),
                "entry_price": round(t.entry_price, 4),
                "exit_price":  round(t.exit_price,  4),
                "bet_size":    t.bet_size,
                "profit":      round(t.profit,  4),
                "roi_pct":     round(t.roi_pct, 2),
                "exit_type":   exit_type,
                "outcome":     t.outcome,
                "status":      t.status,
            })

    def on_snapshot(self, slug: str, up_price: float, volume: float,
                    seconds_before: int):
        bet_size = MR["bet_size"]
        trigger  = MR["trigger_dist"]

        # ── Entry signal at 30s ───────────────────────────────────────────
        if seconds_before == 30:
            deviation = abs(up_price - 0.50)
            log.info(
                f"MR @30s {slug[-10:]}  UP={up_price:.3f}  "
                f"dev={deviation:.3f}  fired={slug in self._fired}"
            )
            if slug in self._fired:                  return
            if up_price <= 0.01 or up_price >= 0.99: return
            if deviation < trigger:
                log.info(f"MR SKIP  dev={deviation:.3f} < threshold={trigger}")
                return
            if self.capital < bet_size:
                log.info(f"MR SKIP  capital={self.capital:.2f} < bet={bet_size}")
                return

            direction   = "DOWN" if up_price > 0.50 else "UP"
            entry_price = (1.0 - up_price) if up_price > 0.50 else up_price

            self.capital -= bet_size
            trade = MRTrade(
                slug=slug, direction=direction,
                entry_price=entry_price, p30=up_price, bet_size=bet_size,
            )
            self.open_trades[slug] = trade
            self._fired.add(slug)
            log.info(
                f"MR ENTER ✓  {slug[-10:]} {direction} @ {entry_price:.3f}  "
                f"(UP={up_price:.3f}  dev={deviation:.2f})"
            )

        # ── Record p10 for analysis only — DO NOT exit ────────────────────
        # Holding to settlement is 3-4x more profitable than exiting at 10s
        elif seconds_before == 10:
            if slug not in self.open_trades: return
            t    = self.open_trades[slug]
            t.p10 = up_price
            cur  = (1.0 - up_price) if t.direction == "DOWN" else up_price
            unreal = MR["bet_size"] / t.entry_price * cur - MR["bet_size"]
            log.info(
                f"MR @10s {slug[-10:]} {t.direction}  "
                f"p10={up_price:.3f}  unrealised=${unreal:+.2f}  → holding"
            )

    def on_outcome(self, slug: str, outcome: str):
        """Market resolved — settle position at $1.00 (win) or $0.00 (loss)."""
        if slug not in self.open_trades: return
        t            = self.open_trades.pop(slug)
        t.outcome    = outcome
        won          = (t.direction == outcome)
        shares       = MR["bet_size"] / t.entry_price
        t.exit_price = 1.0 if won else 0.0
        t.profit     = (shares - MR["bet_size"]) if won else -MR["bet_size"]
        t.status     = "settled_win" if won else "settled_loss"
        self.capital += shares if won else 0
        self.closed_trades.append(t)
        self._save(t, "settled")
        log.info(
            f"MR SETTLE  {slug[-10:]}  dir={t.direction}  outcome={outcome}  "
            f"{'WIN ✓' if won else 'LOSS ✗'}  "
            f"entry={t.entry_price:.3f}  shares={shares:.1f}  profit=${t.profit:+.2f}"
        )

    def summary(self) -> dict:
        n    = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.status == "settled_win")
        pnl  = sum(t.profit for t in self.closed_trades)
        return {
            "capital":       self.capital,
            "session_start": self.session_start,
            "n_open":        len(self.open_trades),
            "n_closed":      n,
            "n_won":         wins,
            "pnl":           pnl,
            "win_rate":      wins / n if n else 0,
        }
