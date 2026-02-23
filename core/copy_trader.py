"""core/copy_trader.py — Follows a target wallet and mirrors trades at scale."""
import json, os, time
from typing import Dict, List, Optional
from core.models import Position, ClosedTrade
from core.api import fetch_wallet_positions
from utils.config import COPY, PATHS
from utils.time_helpers import time_left, time_left_from_ts
from utils import logger

log = logger.get("copy_trader")


def load_memory() -> dict:
    os.makedirs(PATHS['logs'], exist_ok=True)
    if os.path.exists(PATHS['memory']):
        try:
            with open(PATHS['memory']) as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Memory load: {e}")
    return {}


def save_memory(m: dict):
    try:
        with open(PATHS['memory'], 'w') as f:
            json.dump(m, f, indent=2)
    except Exception as e:
        log.error(f"Memory save: {e}")


class CopyTrader:
    def __init__(self, memory: dict):
        self.wallet        = COPY['target_wallet']
        self.scale         = COPY['scale']
        self.budget        = memory.get('balance', COPY['starting_budget'])
        self.available     = self.budget
        self.invested      = memory.get('invested', 0.0)
        self.returned      = memory.get('returned', 0.0)
        self.realized      = memory.get('realized', 0.0)
        self.session_start = self.available
        self.positions:     Dict[str, Position]    = {}
        self.closed_trades: List[ClosedTrade]      = []
        self.manual_queue:  set                    = set()
        self._prev_target:  dict                   = {}

        for d in memory.get('closed_trades', []):
            try: self.closed_trades.append(ClosedTrade.from_dict(d))
            except: pass

    def load_existing(self):
        """On startup: load current target positions into tracker immediately."""
        raw = fetch_wallet_positions(self.wallet)
        copied = 0
        for key, r in raw.items():
            needed = r['avg_price'] * r['shares'] * self.scale
            if needed < 0.01 or needed > self.available:
                continue
            our_shares = r['shares'] * self.scale
            self.available -= needed
            self.invested  += needed
            pos = Position(
                key=key, market_title=r['market_title'], outcome=r['outcome'],
                slug=r['slug'], condition_id=r['condition_id'],
                entry_price=r['avg_price'], cur_price=r['cur_price'],
                shares=our_shares, entry_amount=needed,
                cur_value=r['cur_price'] * our_shares, end_date=r['end_date'],
            )
            self.positions[key] = pos
            copied += 1
        self._prev_target = raw
        log.info(f"Loaded {copied}/{len(raw)} existing target positions")
        return len(raw), copied

    def _do_close(self, key: str, reason: str = "closed") -> Optional[ClosedTrade]:
        pos = self.positions.pop(key, None)
        if not pos: return None
        exit_p   = pos.cur_price
        exit_amt = pos.shares * exit_p
        pnl      = exit_amt - pos.entry_amount
        self.available  += exit_amt
        self.invested   -= pos.entry_amount
        self.returned   += exit_amt
        self.realized   += pnl
        ct = ClosedTrade(
            key=key, market_title=pos.market_title, outcome=pos.outcome,
            slug=pos.slug, entry_price=pos.entry_price, exit_price=exit_p,
            entry_amount=pos.entry_amount, exit_amount=exit_amt,
            realized_pnl=pnl, reason=reason,
        )
        self.closed_trades.append(ct)
        log.info(f"CLOSED {pos.market_title[:40]} pnl={pnl:+.2f} reason={reason}")
        return ct

    def sync(self) -> List[tuple]:
        """Compare current target positions to previous. Return list of events."""
        events  = []
        current = fetch_wallet_positions(self.wallet)

        # New positions opened by target
        for key, raw in current.items():
            if key in self._prev_target:
                # Check if target added shares to existing position
                prev_shares = float(self._prev_target[key].get('shares', 0))
                new_shares  = float(raw.get('shares', 0))
                added       = new_shares - prev_shares
                if added > 0.01 and key in self.positions:
                    extra = raw['avg_price'] * added * self.scale
                    if extra <= self.available:
                        pos = self.positions[key]
                        old_cost  = pos.entry_price * pos.shares
                        new_cost  = raw['avg_price'] * added * self.scale
                        pos.shares       += added * self.scale
                        pos.entry_amount += new_cost
                        pos.entry_price   = (old_cost + new_cost) / pos.shares
                        self.available   -= new_cost
                        self.invested    += new_cost
                        events.append(("add", f"ADD {raw['market_title'][:40]} +{added*self.scale:.1f} shares"))
                continue

            # Brand new position
            needed = raw['avg_price'] * raw['shares'] * self.scale
            if needed < 0.01: continue
            if needed > self.available:
                events.append(("skip", f"SKIP {raw['market_title'][:35]} — need ${needed:.2f}"))
                continue
            our_shares = raw['shares'] * self.scale
            self.available -= needed
            self.invested  += needed
            pos = Position(
                key=key, market_title=raw['market_title'], outcome=raw['outcome'],
                slug=raw['slug'], condition_id=raw['condition_id'],
                entry_price=raw['avg_price'], cur_price=raw['cur_price'],
                shares=our_shares, entry_amount=needed,
                cur_value=raw['cur_price'] * our_shares, end_date=raw['end_date'],
            )
            self.positions[key] = pos
            tl, _ = time_left(pos.end_date)
            events.append(("new",
                f"NEW BET  {raw['market_title']}\n"
                f"         Side={raw['outcome']}  Entry=${raw['avg_price']:.4f}  "
                f"Invested=${needed:.2f}\n"
                f"         URL=https://polymarket.com/event/{raw['slug']}"
            ))
            log.info(f"COPIED {raw['market_title'][:40]} {raw['outcome']} ${needed:.2f}")

        # Update prices + close expired/closed
        for key, pos in list(self.positions.items()):
            if key in current:
                pos.cur_price = current[key]['cur_price']
                pos.cur_value = pos.cur_price * pos.shares
                _, secs = time_left_from_ts(pos.end_ts)
                if secs == -1:
                    ct = self._do_close(key, reason="expired")
                    if ct: events.append(("close",
                        f"EXPIRED  {pos.market_title}\n"
                        f"         Profit=${ct.realized_pnl:+.2f}  ROI={ct.roi_pct:+.1f}%"))
            elif key in self._prev_target:
                ct = self._do_close(key, reason="closed")
                if ct: events.append(("close",
                    f"TARGET CLOSED  {pos.market_title}\n"
                    f"               Profit=${ct.realized_pnl:+.2f}"))

        # Manual closes
        for key in list(self.manual_queue):
            if key in self.positions:
                ct = self._do_close(key, reason="manual")
                if ct: events.append(("close", f"MANUAL  {ct.market_title[:40]}  P&L=${ct.realized_pnl:+.2f}"))
        self.manual_queue.clear()

        self._prev_target = current
        return events

    def close_all(self, reason: str = "shutdown") -> List[ClosedTrade]:
        return [ct for key in list(self.positions) if (ct := self._do_close(key, reason))]

    def summary(self) -> dict:
        unreal = sum(p.pnl for p in self.positions.values())
        return {
            'budget': self.budget, 'available': self.available,
            'invested': self.invested, 'returned': self.returned,
            'realized': self.realized, 'unrealized': unreal,
            'n_open': len(self.positions), 'n_closed': len(self.closed_trades),
            'session_start': self.session_start,
        }

    def to_memory(self) -> dict:
        s = self.summary()
        return {
            'balance': s['available'], 'invested': s['invested'],
            'returned': s['returned'], 'realized': s['realized'],
            'closed_trades': [t.to_dict() for t in self.closed_trades[-200:]],
        }
