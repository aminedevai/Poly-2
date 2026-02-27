"""strategy/mean_reversion.py — Fade 2-15% deviations at T-30s. Hold to settlement."""
import csv, os, time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List
from core.models import MRTrade
from utils.config import MR, PATHS
from utils import logger
from strategy.base import load_fired_from_csv

log = logger.get("mr")

FILE   = PATHS['mr_csv']
FIELDS = ['time','slug','direction','p30','p10','entry_price','exit_price',
          'bet_size','profit','roi_pct','exit_type','outcome','status']


class MeanReversion:
    def __init__(self):
        self.capital       = MR['budget']
        self.session_start = self.capital
        self.open_trades:  Dict[str, MRTrade] = {}
        self.closed_trades: List[dict] = []
        self._min_dev = MR.get('trigger_dist_min', 0.02)
        self._max_dev = MR.get('trigger_dist_max', 0.15)
        os.makedirs(PATHS['logs'], exist_ok=True)
        self._ensure_csv()
        # Load history — survive restarts
        self._fired: set = load_fired_from_csv(FILE)
        self._load_history()
        log.info(f"MR init: {len(self.closed_trades)} closed trades loaded, band=[{self._min_dev},{self._max_dev})")

    def _ensure_csv(self):
        if not os.path.exists(FILE):
            with open(FILE, 'w', newline='', encoding='utf-8') as f:
                csv.DictWriter(f, fieldnames=FIELDS).writeheader()

    def _load_history(self):
        try:
            with open(FILE, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    if not row.get('outcome'): continue
                    self.closed_trades.append({
                        'slug':        row['slug'],
                        'direction':   row['direction'],
                        'p30':         float(row.get('p30', 0)),
                        'p10':         float(row.get('p10', 0)),
                        'entry_price': float(row['entry_price']),
                        'exit_price':  float(row['exit_price']),
                        'profit':      float(row['profit']),
                        'roi_pct':     float(row['roi_pct']),
                        'outcome':     row['outcome'],
                        'status':      row['status'],
                        'entered_at':  time.time(),
                        'url':         f"https://polymarket.com/event/{row['slug']}",
                    })
            pnl = sum(t['profit'] for t in self.closed_trades)
            self.capital = self.session_start + pnl
        except FileNotFoundError:
            pass

    def _save(self, t: MRTrade, exit_type: str):
        with open(FILE, 'a', newline='', encoding='utf-8') as f:
            csv.DictWriter(f, fieldnames=FIELDS).writerow({
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'slug': t.slug, 'direction': t.direction,
                'p30': round(t.p30, 4), 'p10': round(t.p10, 4),
                'entry_price': round(t.entry_price, 4), 'exit_price': round(t.exit_price, 4),
                'bet_size': t.bet_size, 'profit': round(t.profit, 4),
                'roi_pct': round(t.roi_pct, 2), 'exit_type': exit_type,
                'outcome': t.outcome, 'status': t.status,
            })

    def on_snapshot(self, slug, up_price, volume, seconds_before):
        bet_size = MR['bet_size']
        if seconds_before in (30, 60):  # 60s is fallback if 30s was missed
            deviation = abs(up_price - 0.50)
            if slug in self._fired: return
            if up_price <= 0.01 or up_price >= 0.99: return
            if deviation < self._min_dev:
                log.info(f"MR SKIP {slug[-10:]} dev={deviation:.3f} < min {self._min_dev}"); return
            if deviation >= self._max_dev:
                log.info(f"MR SKIP {slug[-10:]} dev={deviation:.3f} >= max {self._max_dev}"); return
            if self.capital < bet_size: return
            direction   = "DOWN" if up_price > 0.50 else "UP"
            entry_price = (1.0 - up_price) if up_price > 0.50 else up_price
            self.capital -= bet_size
            trade = MRTrade(slug=slug, direction=direction, entry_price=entry_price,
                            p30=up_price, bet_size=bet_size)
            self.open_trades[slug] = trade; self._fired.add(slug)
            log.info(f"MR ENTER ✓ {slug[-10:]} {direction} @ {entry_price:.3f}  dev={deviation:.3f}")
        elif seconds_before == 10:  # update p10 for tracking
            if slug not in self.open_trades: return
            t = self.open_trades[slug]; t.p10 = up_price

    def on_outcome(self, slug, outcome):
        if slug not in self.open_trades: return
        t = self.open_trades.pop(slug); t.outcome = outcome
        won = (t.direction == outcome)
        shares = MR['bet_size'] / t.entry_price
        t.exit_price = 1.0 if won else 0.0
        t.profit = (shares - MR['bet_size']) if won else -MR['bet_size']
        t.status = "settled_win" if won else "settled_loss"
        self.capital += shares if won else 0
        closed_dict = {
            'slug': t.slug, 'direction': t.direction,
            'p30': round(t.p30, 3), 'p10': round(t.p10, 3),
            'entry_price': round(t.entry_price, 4), 'exit_price': round(t.exit_price, 4),
            'profit': round(t.profit, 4), 'roi_pct': round(t.roi_pct, 2),
            'outcome': t.outcome, 'status': t.status,
            'entered_at': t.entered_at, 'url': t.url,
        }
        self.closed_trades.append(closed_dict)
        self._save(t, "settled")
        log.info(f"MR SETTLE {slug[-10:]} {t.direction} → {outcome} {'WIN ✓' if won else 'LOSS ✗'} profit=${t.profit:+.2f}")

    def summary(self):
        n    = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.get('status') == 'settled_win')
        pnl  = sum(t.get('profit', 0) for t in self.closed_trades)
        return {
            'capital': round(self.capital, 2), 'session_start': self.session_start,
            'n_open': len(self.open_trades), 'n_closed': n, 'n_won': wins,
            'pnl': round(pnl, 4), 'win_rate': wins / n if n else 0,
            'open_trades': [
                {'slug': t.slug, 'direction': t.direction, 'p30': round(t.p30, 3),
                 'entry_price': round(t.entry_price, 4), 'end_ts': t.end_ts,
                 'entered_at': t.entered_at, 'url': t.url}
                for t in self.open_trades.values()
            ],
            'closed_trades': self.closed_trades[-50:],
        }
