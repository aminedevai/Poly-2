"""strategy/always_down.py — Always Bet DOWN baseline. Entry: DOWN token $0.40-$0.4999."""
import csv, os, time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List
from utils.time_helpers import slug_close_ts
from utils import logger
from strategy.base import load_fired_from_csv

log = logger.get("always_down")

@dataclass
class AlwaysDownTrade:
    slug: str; entry_price: float; bet_size: float; volume: float
    direction: str = "DOWN"
    entered_at: float = field(default_factory=time.time)
    exit_price: float = 0.0; outcome: str = ""; profit: float = 0.0; status: str = "open"
    @property
    def end_ts(self):  return slug_close_ts(self.slug)
    @property
    def url(self):     return f"https://polymarket.com/event/{self.slug}"
    @property
    def roi_pct(self): return self.profit / self.bet_size * 100 if self.bet_size else 0.0

FIELDS = ['time','slug','direction','entry_price','exit_price',
          'bet_size','profit','roi_pct','volume','outcome','status']

class AlwaysDown:
    name = "Always Bet DOWN"
    def __init__(self, budget=100.0, bet_size=5.0, min_volume=100.0,
                 down_min=0.40, down_max=0.49999):
        self.capital = budget; self.session_start = budget
        self.bet_size = bet_size; self.min_volume = min_volume
        self.down_min = down_min; self.down_max = down_max
        self.open_trades: Dict[str, AlwaysDownTrade] = {}
        self.closed_trades: List[dict] = []
        self._csv = os.path.join("logs", "always_down_trades.csv")
        os.makedirs("logs", exist_ok=True)
        self._ensure_csv()
        # Load history from CSV — survive restarts
        self._fired: set = load_fired_from_csv(self._csv)
        self._load_history()
        log.info(f"ALWAYS_DOWN init: {len(self.closed_trades)} closed trades loaded, {len(self._fired)} slugs fired")

    def _ensure_csv(self):
        if not os.path.exists(self._csv):
            with open(self._csv, 'w', newline='', encoding='utf-8') as f:
                csv.DictWriter(f, fieldnames=FIELDS).writeheader()

    def _load_history(self):
        """Reload closed trades from CSV into memory."""
        try:
            with open(self._csv, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    if not row.get('outcome'): continue
                    self.closed_trades.append({
                        'slug':        row['slug'],
                        'direction':   'DOWN',
                        'entry_price': float(row['entry_price']),
                        'exit_price':  float(row['exit_price']),
                        'profit':      float(row['profit']),
                        'roi_pct':     float(row['roi_pct']),
                        'volume':      float(row.get('volume', 0)),
                        'outcome':     row['outcome'],
                        'status':      row['status'],
                        'entered_at':  time.time(),
                        'url':         f"https://polymarket.com/event/{row['slug']}",
                    })
            # Rebuild capital from history
            won = sum(1 for t in self.closed_trades if t['status'] == 'won')
            lost = sum(1 for t in self.closed_trades if t['status'] == 'lost')
            pnl  = sum(t['profit'] for t in self.closed_trades)
            self.capital = self.session_start + pnl
        except FileNotFoundError:
            pass

    def _save(self, t: AlwaysDownTrade):
        with open(self._csv, 'a', newline='', encoding='utf-8') as f:
            csv.DictWriter(f, fieldnames=FIELDS).writerow({
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'slug': t.slug, 'direction': 'DOWN',
                'entry_price': round(t.entry_price, 4),
                'exit_price':  round(t.exit_price, 4),
                'bet_size':    t.bet_size,
                'profit':      round(t.profit, 4),
                'roi_pct':     round(t.roi_pct, 2),
                'volume':      round(t.volume, 2),
                'outcome':     t.outcome, 'status': t.status,
            })

    def on_snapshot(self, slug, up_price, volume, seconds_before):
        if seconds_before not in (30, 60): return  # 60s fallback if 30s missed
        if slug in self._fired:  return
        if up_price <= 0.01 or up_price >= 0.99: return
        if volume < self.min_volume: return
        if self.capital < self.bet_size: return
        down_price = 1.0 - up_price
        if not (self.down_min <= down_price <= self.down_max):
            log.info(f"ALWAYS_DOWN SKIP {slug[-10:]} down={down_price:.4f} not in [{self.down_min},{self.down_max}]")
            return
        self.capital -= self.bet_size
        t = AlwaysDownTrade(slug=slug, entry_price=down_price, bet_size=self.bet_size, volume=volume)
        self.open_trades[slug] = t; self._fired.add(slug)
        log.info(f"ALWAYS_DOWN ENTER ✓ {slug[-10:]} DOWN @ {down_price:.4f}  vol=${volume:,.0f}")

    def on_outcome(self, slug, outcome):
        if slug not in self.open_trades: return
        t = self.open_trades.pop(slug); t.outcome = outcome
        won = (outcome == "DOWN")
        shares = self.bet_size / t.entry_price
        t.exit_price = 1.0 if won else 0.0
        t.profit = (shares - self.bet_size) if won else -self.bet_size
        t.status = "won" if won else "lost"
        self.capital += shares if won else 0
        closed_dict = {
            'slug': t.slug, 'direction': 'DOWN',
            'entry_price': round(t.entry_price, 4), 'exit_price': round(t.exit_price, 4),
            'profit': round(t.profit, 4), 'roi_pct': round(t.roi_pct, 2),
            'volume': round(t.volume, 0), 'outcome': t.outcome, 'status': t.status,
            'entered_at': t.entered_at, 'url': t.url,
        }
        self.closed_trades.append(closed_dict)
        self._save(t)
        log.info(f"ALWAYS_DOWN SETTLE {slug[-10:]} → {outcome} {'WIN ✓' if won else 'LOSS ✗'} {t.profit:+.2f}")

    def summary(self):
        n = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.get('status') == 'won')
        pnl  = sum(t.get('profit', 0) for t in self.closed_trades)
        return {
            'capital': round(self.capital, 2), 'session_start': self.session_start,
            'n_open': len(self.open_trades), 'n_closed': n, 'n_won': wins,
            'pnl': round(pnl, 4), 'win_rate': wins / n if n else 0,
            'open_trades': [
                {'slug': t.slug, 'direction': 'DOWN', 'entry_price': round(t.entry_price, 4),
                 'volume': round(t.volume, 0), 'end_ts': t.end_ts, 'entered_at': t.entered_at, 'url': t.url}
                for t in self.open_trades.values()
            ],
            'closed_trades': self.closed_trades[-50:],
        }
