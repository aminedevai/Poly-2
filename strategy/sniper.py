"""strategy/sniper.py — Volume Spike Sniper. Entry: token price $0.40-$0.4999."""
import csv, os, time
from datetime import datetime
from typing import Dict, List
from core.models import SniperTrade
from utils.config import SNIPER, PATHS
from utils import logger
from strategy.base import load_fired_from_csv

log = logger.get("sniper")

FILE   = PATHS['sniper_csv']
FIELDS = ['time','slug','direction','entry_price','exit_price',
          'bet_size','profit','roi_pct','vol_ratio','move','outcome','status']


class Sniper:
    def __init__(self):
        self.capital       = SNIPER['budget']
        self.session_start = self.capital
        self.open_trades:   Dict[str, SniperTrade] = {}
        self.closed_trades: List[dict] = []
        self._baseline_vols:   Dict[str, float] = {}
        self._baseline_prices: Dict[str, float] = {}
        self._entry_min = SNIPER.get('down_min', 0.40)
        self._entry_max = SNIPER.get('down_max', 0.49999)
        os.makedirs(PATHS['logs'], exist_ok=True)
        self._ensure_csv()
        self._fired: set = load_fired_from_csv(FILE)
        self._load_history()
        log.info(f"SNIPER init: {len(self.closed_trades)} closed trades loaded")

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
                        'slug': row['slug'], 'direction': row['direction'],
                        'entry_price': float(row['entry_price']),
                        'exit_price':  float(row['exit_price']),
                        'profit':    float(row['profit']),
                        'roi_pct':   float(row['roi_pct']),
                        'vol_ratio': float(row.get('vol_ratio', 0)),
                        'move':      float(row.get('move', 0)),
                        'outcome':   row['outcome'], 'status': row['status'],
                        'entered_at': time.time(),
                        'url': f"https://polymarket.com/event/{row['slug']}",
                    })
            pnl = sum(t['profit'] for t in self.closed_trades)
            self.capital = self.session_start + pnl
        except FileNotFoundError:
            pass

    def _save(self, t: SniperTrade):
        with open(FILE, 'a', newline='', encoding='utf-8') as f:
            csv.DictWriter(f, fieldnames=FIELDS).writerow({
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'slug': t.slug, 'direction': t.direction,
                'entry_price': round(t.entry_price, 4), 'exit_price': round(t.exit_price, 4),
                'bet_size': t.bet_size, 'profit': round(t.profit, 4),
                'roi_pct': round(t.roi_pct, 2), 'vol_ratio': round(t.vol_ratio, 1),
                'move': round(t.move, 3), 'outcome': t.outcome, 'status': t.status,
            })

    def on_snapshot(self, slug, up_price, volume, seconds_before):
        bet_size            = SNIPER['bet_size']
        vol_ratio_threshold = SNIPER['vol_ratio']
        move_threshold      = SNIPER['min_move']
        if seconds_before == 240:
            self._baseline_vols[slug] = volume
            self._baseline_prices[slug] = up_price; return
        if seconds_before not in (60, 30, 10): return
        if slug in self._fired: return
        if up_price <= 0.01 or up_price >= 0.99: return
        base_vol   = self._baseline_vols.get(slug, 0)
        base_price = self._baseline_prices.get(slug, up_price)
        if base_vol <= 0: return
        vol_ratio = volume / base_vol
        move      = up_price - base_price
        if vol_ratio < vol_ratio_threshold: return
        if abs(move) < move_threshold: return
        if self.capital < bet_size: return
        direction   = "UP" if move > 0 else "DOWN"
        # For DOWN: token price = 1 - up_price. For UP: token price = up_price.
        # Both must be in [0.40, 0.4999] — we want underdog entries in either direction.
        entry_price = up_price if move > 0 else (1.0 - up_price)
        token_price = entry_price  # same regardless of direction
        if not (self._entry_min <= token_price <= self._entry_max):
            log.info(f"SNIPER SKIP {slug[-10:]} {direction} entry={token_price:.4f} not in [{self._entry_min},{self._entry_max}]"); return
        self.capital -= bet_size
        trade = SniperTrade(slug=slug, direction=direction, entry_price=entry_price,
                            bet_size=bet_size, vol_ratio=vol_ratio, move=move)
        self.open_trades[slug] = trade; self._fired.add(slug)
        log.info(f"SNIPER ENTER ✓ {slug[-10:]} {direction} @ {entry_price:.4f}  vol={vol_ratio:.0f}x")

    def on_outcome(self, slug, outcome):
        if slug not in self.open_trades: return
        t = self.open_trades.pop(slug); t.outcome = outcome
        won = (t.direction == outcome)
        t.exit_price = 1.0 if won else 0.0
        shares = t.bet_size / t.entry_price
        t.profit = (shares - t.bet_size) if won else -t.bet_size
        t.status = "won" if won else "lost"
        self.capital += shares if won else 0
        self.closed_trades.append({
            'slug': t.slug, 'direction': t.direction,
            'entry_price': round(t.entry_price, 4), 'exit_price': round(t.exit_price, 4),
            'profit': round(t.profit, 4), 'roi_pct': round(t.roi_pct, 2),
            'vol_ratio': round(t.vol_ratio, 1), 'move': round(t.move, 3),
            'outcome': t.outcome, 'status': t.status,
            'entered_at': t.entered_at, 'url': t.url,
        })
        self._save(t)
        log.info(f"SNIPER SETTLE {slug[-10:]} {t.direction} → {outcome} {'WIN ✓' if won else 'LOSS ✗'} profit=${t.profit:+.2f}")

    def summary(self):
        n    = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.get('status') == 'won')
        pnl  = sum(t.get('profit', 0) for t in self.closed_trades)
        return {
            'capital': round(self.capital, 2), 'session_start': self.session_start,
            'n_open': len(self.open_trades), 'n_closed': n, 'n_won': wins,
            'pnl': round(pnl, 4), 'win_rate': wins / n if n else 0,
            'open_trades': [
                {'slug': t.slug, 'direction': t.direction, 'entry_price': round(t.entry_price, 4),
                 'vol_ratio': round(t.vol_ratio, 1), 'move': round(t.move, 3),
                 'end_ts': t.end_ts, 'entered_at': t.entered_at, 'url': t.url}
                for t in self.open_trades.values()
            ],
            'closed_trades': self.closed_trades[-50:],
        }
