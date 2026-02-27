"""strategy/last_second.py — T-10s Last-Second Momentum. |p10-p30| >= 0.10."""
import csv, os, time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List
from utils.time_helpers import slug_close_ts
from utils import logger
from strategy.base import load_fired_from_csv

log = logger.get("last_second")

FIELDS = ['time','slug','direction','p30','p10','move','entry_price','exit_price',
          'bet_size','profit','roi_pct','outcome','status']


@dataclass
class LastSecondTrade:
    slug: str; direction: str; p30: float; p10: float; move: float
    entry_price: float; bet_size: float
    entered_at: float = field(default_factory=time.time)
    exit_price: float = 0.0; outcome: str = ""; profit: float = 0.0; status: str = "open"
    @property
    def end_ts(self):  return slug_close_ts(self.slug)
    @property
    def url(self):     return f"https://polymarket.com/event/{self.slug}"
    @property
    def roi_pct(self): return self.profit / self.bet_size * 100 if self.bet_size else 0.0


class LastSecond:
    name = "Last-Second Momentum"
    def __init__(self, budget=100.0, bet_size=5.0, min_move=0.10):
        self.capital       = budget; self.session_start = budget
        self.bet_size      = bet_size; self.min_move = min_move
        self.open_trades:  Dict[str, LastSecondTrade] = {}
        self.closed_trades: List[dict] = []
        self._p30_cache:   Dict[str, float] = {}
        self._csv = os.path.join("logs", "last_second_trades.csv")
        os.makedirs("logs", exist_ok=True)
        self._ensure_csv()
        self._fired: set = load_fired_from_csv(self._csv)
        self._load_history()
        log.info(f"LAST_SEC init: {len(self.closed_trades)} closed trades loaded")

    def _ensure_csv(self):
        if not os.path.exists(self._csv):
            with open(self._csv, 'w', newline='', encoding='utf-8') as f:
                csv.DictWriter(f, fieldnames=FIELDS).writeheader()

    def _load_history(self):
        try:
            with open(self._csv, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    if not row.get('outcome'): continue
                    self.closed_trades.append({
                        'slug': row['slug'], 'direction': row['direction'],
                        'p30':  float(row.get('p30', 0)), 'p10': float(row.get('p10', 0)),
                        'move': float(row.get('move', 0)),
                        'entry_price': float(row['entry_price']),
                        'exit_price':  float(row['exit_price']),
                        'profit':  float(row['profit']), 'roi_pct': float(row['roi_pct']),
                        'outcome': row['outcome'], 'status': row['status'],
                        'entered_at': time.time(),
                        'url': f"https://polymarket.com/event/{row['slug']}",
                    })
            pnl = sum(t['profit'] for t in self.closed_trades)
            self.capital = self.session_start + pnl
        except FileNotFoundError:
            pass

    def _save(self, t: LastSecondTrade):
        with open(self._csv, 'a', newline='', encoding='utf-8') as f:
            csv.DictWriter(f, fieldnames=FIELDS).writerow({
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'slug': t.slug, 'direction': t.direction,
                'p30': round(t.p30, 4), 'p10': round(t.p10, 4), 'move': round(t.move, 4),
                'entry_price': round(t.entry_price, 4), 'exit_price': round(t.exit_price, 4),
                'bet_size': t.bet_size, 'profit': round(t.profit, 4),
                'roi_pct': round(t.roi_pct, 2), 'outcome': t.outcome, 'status': t.status,
            })

    def on_snapshot(self, slug, up_price, volume, seconds_before):
        if seconds_before == 30:
            if up_price: self._p30_cache[slug] = up_price
            return
        if seconds_before != 10: return
        if slug in self._fired: return
        if not up_price or up_price <= 0.01 or up_price >= 0.99: return
        p30 = self._p30_cache.get(slug)
        if p30 is None: return
        move = up_price - p30
        if abs(move) < self.min_move:
            log.info(f"LAST_SEC SKIP {slug[-10:]} move={move:+.3f} < {self.min_move}"); return
        if self.capital < self.bet_size: return
        direction   = "UP" if move > 0 else "DOWN"
        entry_price = max(0.01, min(0.99, up_price if move > 0 else (1.0 - up_price)))
        self.capital -= self.bet_size
        t = LastSecondTrade(slug=slug, direction=direction, p30=p30, p10=up_price,
                            move=move, entry_price=entry_price, bet_size=self.bet_size)
        self.open_trades[slug] = t; self._fired.add(slug)
        log.info(f"LAST_SEC ENTER ✓ {slug[-10:]} {direction} @ {entry_price:.3f}  p30={p30:.3f}→p10={up_price:.3f}  move={move:+.3f}")

    def on_outcome(self, slug, outcome):
        if slug not in self.open_trades: return
        t = self.open_trades.pop(slug); t.outcome = outcome
        won = (t.direction == outcome)
        shares = self.bet_size / t.entry_price
        t.exit_price = 1.0 if won else 0.0
        t.profit = (shares - self.bet_size) if won else -self.bet_size
        t.status = "won" if won else "lost"
        self.capital += shares if won else 0
        self.closed_trades.append({
            'slug': t.slug, 'direction': t.direction,
            'p30': round(t.p30, 3), 'p10': round(t.p10, 3), 'move': round(t.move, 3),
            'entry_price': round(t.entry_price, 4), 'exit_price': round(t.exit_price, 4),
            'profit': round(t.profit, 4), 'roi_pct': round(t.roi_pct, 2),
            'outcome': t.outcome, 'status': t.status,
            'entered_at': t.entered_at, 'url': t.url,
        })
        self._save(t)
        log.info(f"LAST_SEC SETTLE {slug[-10:]} {t.direction} → {outcome} {'WIN ✓' if won else 'LOSS ✗'} profit=${t.profit:+.2f}")

    def summary(self):
        n    = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.get('status') == 'won')
        pnl  = sum(t.get('profit', 0) for t in self.closed_trades)
        return {
            'capital': round(self.capital, 2), 'session_start': self.session_start,
            'n_open': len(self.open_trades), 'n_closed': n, 'n_won': wins,
            'pnl': round(pnl, 4), 'win_rate': wins / n if n else 0,
            'open_trades': [
                {'slug': t.slug, 'direction': t.direction, 'p30': round(t.p30, 3),
                 'p10': round(t.p10, 3), 'move': round(t.move, 3),
                 'entry_price': round(t.entry_price, 4),
                 'end_ts': t.end_ts, 'entered_at': t.entered_at, 'url': t.url}
                for t in self.open_trades.values()
            ],
            'closed_trades': self.closed_trades[-50:],
        }
