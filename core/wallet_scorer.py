"""
core/wallet_scorer.py — Wallet Scoring Engine
================================================
Implements the 4 metrics from the copy-trading meta article:
  1. Sharpe Ratio       — risk-adjusted returns (target > 1.5)
  2. Kelly Criterion    — optimal position sizing fraction
  3. Win Rate Decay     — rolling 30-trade WR vs all-time (alert if <55% or <80% of ATH)
  4. Expected Value     — EV per trade after slippage (target > $0 per trade)

Also classifies wallet type:
  - INFORMED  → few trades, high conviction, 60%+ WR
  - MARKET_MAKER → holds YES+NO simultaneously (ignore)
  - BOT        → >100 trades/month, high frequency
  - UNKNOWN    → insufficient history
"""

import time
import math
import requests
from typing import Dict, List, Optional, Tuple
from utils import logger

log = logger.get("wallet_scorer")

DECAY_WINDOW      = 30      # rolling window for win rate decay check
DECAY_MIN_WR      = 0.55    # alert if rolling WR drops below 55%
DECAY_ATH_RATIO   = 0.80    # alert if rolling WR < 80% of all-time WR
MIN_TRADES        = 10      # min trades to score a wallet
SLIPPAGE          = 0.02    # assumed 2% slippage per copy trade
RISK_FREE_RATE    = 0.0     # no risk-free rate on Polymarket


def fetch_wallet_trades(wallet: str, limit: int = 200) -> List[dict]:
    """Fetch recent trade history for a wallet from Polymarket data API."""
    try:
        r = requests.get(
            "https://data-api.polymarket.com/activity",
            params={"user": wallet, "limit": limit, "offset": 0},
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        data = r.json()
        if not isinstance(data, list):
            return []
        return data
    except Exception as e:
        log.debug(f"fetch_wallet_trades({wallet[:10]}): {e}")
        return []


def fetch_wallet_profit(wallet: str) -> Optional[float]:
    """Fetch total profit/PnL for a wallet."""
    try:
        r = requests.get(
            "https://data-api.polymarket.com/profile",
            params={"address": wallet},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        data = r.json()
        if isinstance(data, dict):
            return float(data.get("pnl", 0) or 0)
        return None
    except Exception as e:
        log.debug(f"fetch_wallet_profit({wallet[:10]}): {e}")
        return None


def _classify_wallet(trades: List[dict], positions: dict) -> str:
    """Classify wallet type based on trade patterns."""
    if not trades:
        return "UNKNOWN"

    n = len(trades)

    # Check for MM: holds YES + NO simultaneously in same market
    market_sides: Dict[str, set] = {}
    for key, pos in positions.items():
        slug = pos.get("slug", "")
        side = pos.get("outcome", "")
        if slug not in market_sides:
            market_sides[slug] = set()
        market_sides[slug].add(side)
    mm_count = sum(1 for sides in market_sides.values() if len(sides) >= 2)
    if mm_count >= 2 or (mm_count >= 1 and len(market_sides) <= 3):
        return "MARKET_MAKER"

    # Check for bot: high trade frequency
    if n >= 2:
        timestamps = sorted([t.get("timestamp", 0) for t in trades if t.get("timestamp")])
        if len(timestamps) >= 50:
            time_span_days = (timestamps[-1] - timestamps[0]) / 86400
            if time_span_days > 0:
                trades_per_day = len(timestamps) / time_span_days
                if trades_per_day > 20:  # >20/day = >600/month, clearly a bot
                    return "BOT"

    # Informed trader: moderate trade count, track on win rate
    return "INFORMED"


def compute_sharpe(returns: List[float]) -> float:
    """Compute Sharpe ratio from list of trade returns (as fractions)."""
    if len(returns) < 5:
        return 0.0
    avg = sum(returns) / len(returns)
    variance = sum((r - avg) ** 2 for r in returns) / len(returns)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std == 0:
        return 0.0
    return (avg - RISK_FREE_RATE) / std


def compute_kelly(win_rate: float, avg_odds: float) -> float:
    """
    Kelly Criterion: f = (p*b - q) / b
    p = win_rate, q = 1-p, b = avg_odds (e.g. buying at $0.40 → b = 1.5)
    Returns fraction of bankroll (0.0 to 0.5 capped for safety).
    """
    if avg_odds <= 0 or win_rate <= 0:
        return 0.0
    q = 1.0 - win_rate
    f = (win_rate * avg_odds - q) / avg_odds
    return max(0.0, min(0.5, f))  # cap at 50% (half-Kelly safety)


def compute_ev(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """EV per trade adjusted for slippage."""
    raw_ev = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
    return raw_ev - SLIPPAGE


def rolling_win_rate(trades: List[dict], window: int = DECAY_WINDOW) -> float:
    """Win rate over the last N settled trades."""
    recent = [t for t in trades if t.get("profit") is not None][-window:]
    if not recent:
        return 0.0
    wins = sum(1 for t in recent if float(t.get("profit", 0)) > 0)
    return wins / len(recent)


def detect_decay(trades: List[dict]) -> Tuple[bool, str]:
    """
    Returns (is_decaying, reason_string).
    Triggers if rolling-30 WR < 55% OR rolling-30 WR < 80% of all-time WR.
    """
    settled = [t for t in trades if t.get("profit") is not None]
    if len(settled) < DECAY_WINDOW:
        return False, f"Insufficient history ({len(settled)} trades)"

    all_wins = sum(1 for t in settled if float(t.get("profit", 0)) > 0)
    alltime_wr = all_wins / len(settled)
    recent_wr  = rolling_win_rate(settled, DECAY_WINDOW)

    reasons = []
    if recent_wr < DECAY_MIN_WR:
        reasons.append(f"rolling WR {recent_wr:.1%} < {DECAY_MIN_WR:.0%} threshold")
    if alltime_wr > 0 and recent_wr < alltime_wr * DECAY_ATH_RATIO:
        reasons.append(f"rolling WR {recent_wr:.1%} < 80% of ATH {alltime_wr:.1%}")

    if reasons:
        return True, " | ".join(reasons)
    return False, f"OK  rolling={recent_wr:.1%}  alltime={alltime_wr:.1%}"


class WalletScore:
    """Score summary for a single wallet."""
    def __init__(self, wallet: str):
        self.wallet       = wallet
        self.wallet_type  = "UNKNOWN"
        self.n_trades     = 0
        self.win_rate     = 0.0
        self.rolling_wr   = 0.0
        self.sharpe       = 0.0
        self.kelly        = 0.0
        self.ev_per_trade = 0.0
        self.avg_entry    = 0.0
        self.avg_win      = 0.0
        self.avg_loss     = 0.0
        self.total_pnl    = 0.0
        self.is_decaying  = False
        self.decay_reason = ""
        self.score        = 0.0       # composite 0-100
        self.grade        = "?"
        self.copyable     = False
        self.scored_at    = time.time()
        self.error        = ""

    def to_dict(self) -> dict:
        return {
            "wallet":       self.wallet,
            "wallet_type":  self.wallet_type,
            "n_trades":     self.n_trades,
            "win_rate":     round(self.win_rate, 3),
            "rolling_wr":   round(self.rolling_wr, 3),
            "sharpe":       round(self.sharpe, 2),
            "kelly":        round(self.kelly, 3),
            "ev_per_trade": round(self.ev_per_trade, 4),
            "avg_entry":    round(self.avg_entry, 3),
            "total_pnl":    round(self.total_pnl, 2),
            "is_decaying":  self.is_decaying,
            "decay_reason": self.decay_reason,
            "score":        round(self.score, 1),
            "grade":        self.grade,
            "copyable":     self.copyable,
            "scored_at":    self.scored_at,
            "error":        self.error,
        }


def score_wallet(wallet: str, positions: dict = None) -> WalletScore:
    """Full scoring pipeline for a single wallet."""
    ws = WalletScore(wallet)
    if positions is None:
        positions = {}

    try:
        trades = fetch_wallet_trades(wallet, limit=200)
        ws.n_trades = len(trades)

        if ws.n_trades < MIN_TRADES:
            ws.error = f"Only {ws.n_trades} trades — need {MIN_TRADES} minimum"
            ws.grade = "?"
            return ws

        ws.wallet_type = _classify_wallet(trades, positions)

        # Settled trades with profit data
        settled = [t for t in trades if t.get("profit") is not None]

        if settled:
            profits = [float(t.get("profit", 0)) for t in settled]
            entries = [float(t.get("price", 0.5)) for t in settled if t.get("price")]
            wins  = [p for p in profits if p > 0]
            loses = [abs(p) for p in profits if p < 0]

            ws.win_rate   = len(wins) / len(settled) if settled else 0.0
            ws.rolling_wr = rolling_win_rate(settled, DECAY_WINDOW)
            ws.avg_entry  = sum(entries) / len(entries) if entries else 0.5
            ws.avg_win    = sum(wins)  / len(wins)   if wins  else 0.0
            ws.avg_loss   = sum(loses) / len(loses)  if loses else 0.0
            ws.total_pnl  = sum(profits)

            # Returns as fraction of bet
            bet_amounts = [float(t.get("size", 1.0)) * float(t.get("price", 0.5))
                           for t in settled]
            returns = [p / b if b > 0 else 0.0
                       for p, b in zip(profits, bet_amounts)]
            ws.sharpe = compute_sharpe(returns)

            # Kelly: use avg_entry as proxy for odds
            if ws.avg_entry > 0:
                avg_odds = (1.0 - ws.avg_entry) / ws.avg_entry  # payout ratio
                ws.kelly = compute_kelly(ws.win_rate, avg_odds)

            ws.ev_per_trade = compute_ev(ws.win_rate, ws.avg_win, ws.avg_loss)

            ws.is_decaying, ws.decay_reason = detect_decay(settled)

        # Composite score (0-100)
        score = 0.0
        if ws.wallet_type == "INFORMED":
            score += min(40, ws.win_rate * 60)         # up to 40pts for WR
            score += min(20, max(0, ws.sharpe * 10))   # up to 20pts for Sharpe
            score += min(20, max(0, ws.ev_per_trade * 200))  # up to 20pts for EV
            score += min(10, ws.kelly * 20)            # up to 10pts for Kelly
            score += 10 if not ws.is_decaying else 0   # 10pts for no decay
        ws.score = score

        # Grade
        if ws.wallet_type in ("MARKET_MAKER", "BOT"):
            ws.grade = "SKIP"
            ws.copyable = False
        elif ws.score >= 70 and not ws.is_decaying:
            ws.grade = "A"
            ws.copyable = True
        elif ws.score >= 55 and not ws.is_decaying:
            ws.grade = "B"
            ws.copyable = True
        elif ws.score >= 40:
            ws.grade = "C"
            ws.copyable = False
        else:
            ws.grade = "D"
            ws.copyable = False

        log.info(
            f"SCORED {wallet[:12]}  type={ws.wallet_type}  "
            f"WR={ws.win_rate:.1%}  rolling={ws.rolling_wr:.1%}  "
            f"Sharpe={ws.sharpe:.2f}  Kelly={ws.kelly:.2f}  "
            f"EV={ws.ev_per_trade:.3f}  score={ws.score:.0f}  grade={ws.grade}"
            + (f"  ⚠️ DECAY: {ws.decay_reason}" if ws.is_decaying else "")
        )

    except Exception as e:
        ws.error = str(e)
        log.error(f"score_wallet({wallet[:12]}): {e}")

    return ws
