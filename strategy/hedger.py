"""
strategy/hedger.py
==================
Gabagool Hedging Strategy — Direction-Agnostic Pair Locking

Based on the "gabagool22" Polymarket bot discovered in analysis.

CORE CONCEPT:
  Buy YES when YES is cheap  (up_price <= entry_threshold)
  Buy NO  when NO  is cheap  (1-up_price <= entry_threshold)
  at DIFFERENT snapshots within the same 5-min market window.

  Goal: avg_YES_cost + avg_NO_cost < $1.00
  Once achieved → GUARANTEED profit regardless of BTC direction.

MATH:
  pair_cost  = avg_YES + avg_NO
  If pair_cost < 1.00:
    profit = min(shares_YES, shares_NO) * 1.00 - (cost_YES + cost_NO)

ENTRY LOGIC (per snapshot: 240s, 180s, 120s, 60s, 30s, 10s):
  - YES leg: if YES price <= entry_threshold AND no YES leg yet → buy YES
  - NO  leg: if NO  price <= entry_threshold AND YES leg exists
             AND sim pair_cost < max_pair_cost → buy NO, lock hedge

PARAMETERS:
  entry_threshold: max price to pay for either leg (default 0.50)
  max_pair_cost:   only complete hedge if sim cost < this (default 0.98)
  leg_bet:         $ per leg — total risk = leg_bet × 2 if only one leg fills
  min_volume:      skip low-liquidity markets

SETTLEMENT:
  - Both legs filled + locked: collect winner, profit guaranteed
  - Only YES filled: act as regular UP bet (settles normally)
  - Only NO  filled: act as regular DOWN bet (settles normally)
  - Neither filled: no trade, no exposure
"""
import csv, os, time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from utils.time_helpers import slug_close_ts
from utils import logger
from strategy.base import load_fired_from_csv

log = logger.get("hedger")

FIELDS = [
    'time', 'slug',
    'yes_entry', 'no_entry', 'yes_shares', 'no_shares',
    'yes_cost',  'no_cost',  'pair_cost',
    'locked',    'outcome',  'profit',     'roi_pct',
    'leg_bet',   'volume',   'status',
]


@dataclass
class HedgeTrade:
    slug:       str
    leg_bet:    float
    volume:     float
    entered_at: float = field(default_factory=time.time)

    # YES leg
    yes_entry:  Optional[float] = None
    yes_shares: float = 0.0
    yes_cost:   float = 0.0

    # NO leg
    no_entry:   Optional[float] = None
    no_shares:  float = 0.0
    no_cost:    float = 0.0

    # Result
    locked:     bool  = False   # True once pair_cost < 1.00
    pair_cost:  float = 0.0
    outcome:    str   = ""
    profit:     float = 0.0
    status:     str   = "open"

    @property
    def end_ts(self): return slug_close_ts(self.slug)
    @property
    def url(self):    return f"https://polymarket.com/event/{self.slug}"

    @property
    def total_cost(self) -> float:
        return self.yes_cost + self.no_cost

    @property
    def guaranteed_profit(self) -> float:
        """Profit if both legs filled and locked."""
        if not self.locked:
            return 0.0
        payout = min(self.yes_shares, self.no_shares)
        return payout - self.total_cost

    @property
    def roi_pct(self) -> float:
        return self.profit / self.total_cost * 100 if self.total_cost > 0 else 0.0

    def legs_str(self) -> str:
        yes = f"YES@{self.yes_entry:.3f}" if self.yes_entry else "YES=--"
        no  = f"NO@{self.no_entry:.3f}"   if self.no_entry  else "NO=--"
        return f"{yes}  {no}"


class Hedger:
    name = "Pair Hedger (Gabagool)"

    def __init__(self, budget: float = 200.0, leg_bet: float = 5.0,
                 entry_threshold: float = 0.50, max_pair_cost: float = 0.98,
                 min_volume: float = 100.0):
        self.capital          = budget
        self.session_start    = budget
        self.leg_bet          = leg_bet            # $ per leg ($5 YES + $5 NO = $10 max)
        self.entry_threshold  = entry_threshold    # buy leg when price <= this
        self.max_pair_cost    = max_pair_cost      # only lock if combined avg < this
        self.min_volume       = min_volume

        self.open_trades:   Dict[str, HedgeTrade] = {}
        self.closed_trades: List[dict]            = []
        self._no_entry_fired: set = set()         # slugs where NO leg already entered
        self._skip: set = set()                   # slugs to fully skip

        self._csv = os.path.join("logs", "hedger_trades.csv")
        os.makedirs("logs", exist_ok=True)
        self._ensure_csv()
        # Dedup: load all previously traded slugs
        self._traded: set = load_fired_from_csv(self._csv)
        self._load_history()
        log.info(f"HEDGER init: {len(self.closed_trades)} closed trades loaded  "
                 f"threshold={entry_threshold}  max_pair={max_pair_cost}")

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
                        'slug':       row['slug'],
                        'yes_entry':  float(row.get('yes_entry') or 0),
                        'no_entry':   float(row.get('no_entry')  or 0),
                        'pair_cost':  float(row.get('pair_cost') or 0),
                        'locked':     row.get('locked') == 'True',
                        'profit':     float(row['profit']),
                        'roi_pct':    float(row['roi_pct']),
                        'volume':     float(row.get('volume', 0)),
                        'outcome':    row['outcome'],
                        'status':     row['status'],
                        'entered_at': time.time(),
                        'url':        f"https://polymarket.com/event/{row['slug']}",
                    })
            pnl = sum(t['profit'] for t in self.closed_trades)
            self.capital = self.session_start + pnl
        except FileNotFoundError:
            pass

    def _save(self, t: HedgeTrade):
        with open(self._csv, 'a', newline='', encoding='utf-8') as f:
            csv.DictWriter(f, fieldnames=FIELDS).writerow({
                'time':       datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'slug':       t.slug,
                'yes_entry':  round(t.yes_entry, 4)  if t.yes_entry  else '',
                'no_entry':   round(t.no_entry,  4)  if t.no_entry   else '',
                'yes_shares': round(t.yes_shares, 4),
                'no_shares':  round(t.no_shares,  4),
                'yes_cost':   round(t.yes_cost,   4),
                'no_cost':    round(t.no_cost,    4),
                'pair_cost':  round(t.pair_cost,  4),
                'locked':     t.locked,
                'outcome':    t.outcome,
                'profit':     round(t.profit,  4),
                'roi_pct':    round(t.roi_pct, 2),
                'leg_bet':    t.leg_bet,
                'volume':     round(t.volume,  2),
                'status':     t.status,
            })

    # ── Snapshot handler ──────────────────────────────────────────────────────

    def on_snapshot(self, slug: str, up_price: float, volume: float,
                    seconds_before: int):
        if slug in self._skip:
            return
        if not up_price or up_price <= 0.01 or up_price >= 0.99:
            return
        if volume < self.min_volume:
            return

        down_price = 1.0 - up_price
        t = self.open_trades.get(slug)

        # ── YES leg: buy when YES is cheap ────────────────────────────────────
        if t is None and slug not in self._traded:
            if up_price <= self.entry_threshold:
                if self.capital < self.leg_bet:
                    return
                self.capital -= self.leg_bet
                shares = self.leg_bet / up_price
                t = HedgeTrade(slug=slug, leg_bet=self.leg_bet, volume=volume,
                               yes_entry=up_price, yes_shares=shares,
                               yes_cost=self.leg_bet)
                self.open_trades[slug] = t
                log.info(f"HEDGER YES ✓ {slug[-10:]} YES@{up_price:.3f}  "
                         f"{shares:.2f}sh  cost=${self.leg_bet:.2f}")

        # ── NO leg: buy when NO is cheap AND YES already held ─────────────────
        if t is not None and t.no_entry is None and slug not in self._no_entry_fired:
            if down_price <= self.entry_threshold:
                # Simulate pair cost with this NO entry
                sim_pair = t.yes_entry + down_price if t.yes_entry else down_price
                if sim_pair < self.max_pair_cost:
                    if self.capital < self.leg_bet:
                        return
                    self.capital -= self.leg_bet
                    shares = self.leg_bet / down_price
                    t.no_entry  = down_price
                    t.no_shares = shares
                    t.no_cost   = self.leg_bet
                    t.pair_cost = sim_pair
                    t.locked    = sim_pair < 1.0  # True = risk-free profit locked
                    self._no_entry_fired.add(slug)
                    locked_str = f"LOCKED pair={sim_pair:.3f} guaranteed=${t.guaranteed_profit:.2f}" \
                                 if t.locked else f"pair={sim_pair:.3f} NOT locked"
                    log.info(f"HEDGER NO  ✓ {slug[-10:]} NO@{down_price:.3f}  "
                             f"{shares:.2f}sh  {locked_str}")
                else:
                    log.info(f"HEDGER NO SKIP {slug[-10:]} sim_pair={sim_pair:.3f} "
                             f">= max {self.max_pair_cost}")

    # ── Settlement ────────────────────────────────────────────────────────────

    def on_outcome(self, slug: str, outcome: str):
        if slug not in self.open_trades:
            return
        t          = self.open_trades.pop(slug)
        t.outcome  = outcome
        self._traded.add(slug)

        # Calculate profit based on which legs were filled
        if t.yes_entry is not None and t.no_entry is not None:
            # Both legs — collect winner
            if outcome == "UP":
                payout = t.yes_shares * 1.0
            else:
                payout = t.no_shares * 1.0
            t.profit = payout - t.total_cost
            t.status = "won" if t.profit > 0 else "lost"
            self.capital += payout

        elif t.yes_entry is not None:
            # Only YES leg — treat as UP bet
            won = (outcome == "UP")
            payout = t.yes_shares * 1.0 if won else 0.0
            t.profit = payout - t.yes_cost
            t.status = "won" if won else "lost"
            self.capital += payout

        else:
            # Shouldn't happen (no legs), skip
            return

        closed_dict = {
            'slug':      t.slug,
            'yes_entry': round(t.yes_entry, 4) if t.yes_entry else None,
            'no_entry':  round(t.no_entry,  4) if t.no_entry  else None,
            'pair_cost': round(t.pair_cost, 4),
            'locked':    t.locked,
            'profit':    round(t.profit,  4),
            'roi_pct':   round(t.roi_pct, 2),
            'volume':    round(t.volume,  0),
            'outcome':   t.outcome,
            'status':    t.status,
            'entered_at': t.entered_at,
            'url':       t.url,
        }
        self.closed_trades.append(closed_dict)
        self._save(t)

        legs = "BOTH" if t.no_entry else "YES-only"
        lock = "LOCKED ✓" if t.locked else "unlocked"
        log.info(f"HEDGER SETTLE {slug[-10:]} [{legs}] {lock} → {outcome}  "
                 f"{'WIN ✓' if t.status=='won' else 'LOSS ✗'}  profit=${t.profit:+.2f}")

    def summary(self) -> dict:
        n    = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.get('status') == 'won')
        pnl  = sum(t.get('profit', 0) for t in self.closed_trades)

        # Open trades — show pair_cost and lock status
        open_list = []
        for t in self.open_trades.values():
            open_list.append({
                'slug':      t.slug,
                'yes_entry': round(t.yes_entry, 4) if t.yes_entry else None,
                'no_entry':  round(t.no_entry,  4) if t.no_entry  else None,
                'pair_cost': round(t.pair_cost, 4),
                'locked':    t.locked,
                'guaranteed': round(t.guaranteed_profit, 4),
                'end_ts':    t.end_ts,
                'entered_at':t.entered_at,
                'url':       t.url,
                # For dashboard compat
                'direction': 'HEDGE',
                'entry_price': round(t.yes_entry or 0, 4),
                'volume':    round(t.volume, 0),
            })

        # Count locked hedges
        n_locked = sum(1 for t in self.open_trades.values() if t.locked)
        n_locked += sum(1 for t in self.closed_trades if t.get('locked'))

        return {
            'capital':       round(self.capital, 2),
            'session_start': self.session_start,
            'n_open':        len(self.open_trades),
            'n_closed':      n,
            'n_won':         wins,
            'pnl':           round(pnl, 4),
            'win_rate':      wins / n if n else 0,
            'n_locked':      n_locked,
            'open_trades':   open_list,
            'closed_trades': self.closed_trades[-50:],
        }
