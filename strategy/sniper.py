"""strategy/sniper.py — Volume spike sniper: follow sudden price movements."""
import csv, os
from datetime import datetime
from typing import Dict, List
from core.models import SniperTrade
from utils.config import SNIPER, PATHS
from utils import logger

log = logger.get("sniper")

FILE     = PATHS['sniper_csv']
FIELDS   = ['time','slug','direction','entry_price','exit_price',
            'bet_size','profit','roi_pct','vol_ratio','move','outcome','status']


class Sniper:
    def __init__(self):
        self.capital       = SNIPER['budget']
        self.session_start = self.capital
        self.open_trades:   Dict[str, SniperTrade] = {}
        self.closed_trades: List[SniperTrade]      = []
        self._baseline_vols:   Dict[str, float]    = {}
        self._baseline_prices: Dict[str, float]    = {}
        self._fired: set = set()
        self._ensure_csv()

    def _ensure_csv(self):
        os.makedirs(PATHS['logs'], exist_ok=True)
        if not os.path.exists(FILE):
            with open(FILE, 'w', newline='', encoding='utf-8') as f:
                csv.DictWriter(f, fieldnames=FIELDS).writeheader()

    def _save(self, t: SniperTrade):
        with open(FILE, 'a', newline='', encoding='utf-8') as f:
            csv.DictWriter(f, fieldnames=FIELDS).writerow({
                'time':        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'slug':        t.slug, 'direction': t.direction,
                'entry_price': round(t.entry_price, 4),
                'exit_price':  round(t.exit_price,  4),
                'bet_size':    t.bet_size,
                'profit':      round(t.profit,  4),
                'roi_pct':     round(t.roi_pct, 2),
                'vol_ratio':   round(t.vol_ratio, 1),
                'move':        round(t.move, 3),
                'outcome':     t.outcome, 'status': t.status,
            })

    def on_snapshot(self, slug: str, up_price: float, volume: float, seconds_before: int):
        bet_size  = SNIPER['bet_size']
        vol_ratio_threshold = SNIPER['vol_ratio']
        move_threshold      = SNIPER['min_move']

        if seconds_before == 240:
            self._baseline_vols[slug]   = volume
            self._baseline_prices[slug] = up_price
            return

        if seconds_before not in (30, 10): return
        if slug in self._fired: return
        if up_price <= 0.01 or up_price >= 0.99: return

        base_vol   = self._baseline_vols.get(slug, 0)
        base_price = self._baseline_prices.get(slug, up_price)
        if base_vol <= 0: return

        vol_ratio = volume / base_vol
        move      = up_price - base_price

        log.info(f"SNIPER @{seconds_before}s {slug[-10:]} vol={vol_ratio:.1f}x move={move:+.3f}")

        if vol_ratio < vol_ratio_threshold: return
        if abs(move)  < move_threshold:    return
        if self.capital < bet_size:        return

        direction   = "UP" if move > 0 else "DOWN"
        entry_price = up_price if move > 0 else 1.0 - up_price
        self.capital -= bet_size

        trade = SniperTrade(slug=slug, direction=direction, entry_price=entry_price,
                            bet_size=bet_size, vol_ratio=vol_ratio, move=move)
        self.open_trades[slug] = trade
        self._fired.add(slug)
        log.info(f"SNIPER ENTER {slug[-10:]} {direction} @ {entry_price:.3f} vol={vol_ratio:.0f}x")

    def on_outcome(self, slug: str, outcome: str):
        if slug not in self.open_trades: return
        t        = self.open_trades.pop(slug)
        t.outcome    = outcome
        won          = (t.direction == outcome)
        t.exit_price = 1.0 if won else 0.0
        shares       = t.bet_size / t.entry_price
        t.profit     = (shares * 1.0 - t.bet_size) if won else -t.bet_size
        t.status     = "won" if won else "lost"
        self.capital += (shares * 1.0) if won else 0
        self.closed_trades.append(t)
        self._save(t)
        log.info(f"SNIPER SETTLE {slug[-10:]} {outcome} {'WIN' if won else 'LOSS'} profit={t.profit:+.2f}")

    def summary(self) -> dict:
        n    = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.status == "won")
        pnl  = sum(t.profit for t in self.closed_trades)
        return {
            'capital': self.capital, 'session_start': self.session_start,
            'n_open': len(self.open_trades), 'n_closed': n,
            'n_won': wins, 'pnl': pnl,
            'win_rate': wins / n if n else 0,
        }
