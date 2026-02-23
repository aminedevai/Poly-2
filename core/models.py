"""core/models.py — Shared data classes."""
import time
from dataclasses import dataclass, field
from datetime import datetime
from utils.time_helpers import slug_close_ts, time_left_from_ts

# ── Copy Trader ────────────────────────────────────────────────────────────────

@dataclass
class Position:
    key: str; market_title: str; outcome: str; slug: str; condition_id: str
    entry_price: float; cur_price: float; shares: float
    entry_amount: float; cur_value: float; end_date: str
    opened_at: float = field(default_factory=time.time)

    @property
    def end_ts(self) -> int:
        ts = slug_close_ts(self.slug)
        if ts: return ts
        if self.end_date:
            try: return int(datetime.fromisoformat(self.end_date.replace('Z', '+00:00')).timestamp())
            except: pass
        return 0

    @property
    def url(self): return f"https://polymarket.com/event/{self.slug}"
    @property
    def pnl(self): return self.cur_value - self.entry_amount
    @property
    def roi_pct(self):
        return (self.cur_price - self.entry_price) / self.entry_price * 100 if self.entry_price > 0 else 0.0


@dataclass
class ClosedTrade:
    key: str; market_title: str; outcome: str; slug: str
    entry_price: float; exit_price: float
    entry_amount: float; exit_amount: float
    realized_pnl: float
    closed_at: float = field(default_factory=time.time)
    reason: str = "closed"

    @property
    def url(self): return f"https://polymarket.com/event/{self.slug}"
    @property
    def roi_pct(self):
        return (self.exit_price - self.entry_price) / self.entry_price * 100 if self.entry_price > 0 else 0.0

    def to_dict(self):
        return {k: getattr(self, k) for k in
                ['key', 'market_title', 'outcome', 'slug', 'entry_price',
                 'exit_price', 'entry_amount', 'exit_amount',
                 'realized_pnl', 'closed_at', 'reason']}

    @classmethod
    def from_dict(cls, d: dict):
        keys = ['key', 'market_title', 'outcome', 'slug', 'entry_price',
                'exit_price', 'entry_amount', 'exit_amount',
                'realized_pnl', 'closed_at', 'reason']
        return cls(**{k: d[k] for k in keys if k in d})


# ── Strategies ────────────────────────────────────────────────────────────────

@dataclass
class SniperTrade:
    slug: str; direction: str; entry_price: float; bet_size: float
    vol_ratio: float; move: float
    entered_at: float = field(default_factory=time.time)
    outcome: str = ""; exit_price: float = 0.0
    profit: float = 0.0; status: str = "open"

    @property
    def end_ts(self): return slug_close_ts(self.slug)
    @property
    def url(self):    return f"https://polymarket.com/event/{self.slug}"
    @property
    def roi_pct(self):
        return self.profit / self.bet_size * 100 if self.bet_size > 0 else 0.0


@dataclass
class MRTrade:
    slug: str; direction: str; entry_price: float
    p30: float; bet_size: float
    entered_at: float = field(default_factory=time.time)
    p10: float = 0.0; exit_price: float = 0.0
    outcome: str = ""; profit: float = 0.0; status: str = "open"

    @property
    def end_ts(self): return slug_close_ts(self.slug)
    @property
    def url(self):    return f"https://polymarket.com/event/{self.slug}"
    @property
    def roi_pct(self):
        return self.profit / self.bet_size * 100 if self.bet_size > 0 else 0.0
