"""
core/basket_trader.py — Multi-Wallet Basket Copy Trader (Feature B)
=====================================================================
Instead of copying one wallet blindly, maintains a scored basket of wallets.
Only enters a position when >= consensus_threshold fraction agree on the same
outcome in the same market.

Config (config.yaml):
  basket_trader:
    wallets:
      - "0xabc..."
      - "0xdef..."
    consensus_threshold: 0.80   # 80% of basket must agree
    starting_budget:    200.0
    bet_size:            10.0
    score_interval:    3600      # re-score wallets every 1h
    min_grade: "B"               # only follow A or B graded wallets
"""

import time
import json
import os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from core.api          import fetch_wallet_positions
from core.wallet_scorer import score_wallet, WalletScore
from utils.config      import CFG
from utils             import logger

log = logger.get("basket")

BASKET_CFG = CFG.get("basket_trader", {})
WALLETS    = BASKET_CFG.get("wallets", [])
THRESHOLD  = BASKET_CFG.get("consensus_threshold", 0.80)
BUDGET     = BASKET_CFG.get("starting_budget", 200.0)
BET_SIZE   = BASKET_CFG.get("bet_size", 10.0)
SCORE_IVTL = BASKET_CFG.get("score_interval", 3600)
MIN_GRADE  = BASKET_CFG.get("min_grade", "B")
GRADE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1, "SKIP": 0, "?": 0}

SCORES_PATH = os.path.join("logs", "basket_scores.json")


class BasketTrader:
    """
    Multi-wallet basket with consensus gating.
    Positions are paper-traded (no real execution).
    """

    def __init__(self):
        self.wallets         = WALLETS[:]
        self.threshold       = THRESHOLD
        self.capital         = BUDGET
        self.session_start   = BUDGET
        self.bet_size        = BET_SIZE

        # wallet_addr -> WalletScore
        self.scores: Dict[str, WalletScore] = {}
        self._last_score_time = 0.0

        # slug_outcome -> BasketPosition
        self.open_trades: Dict[str, dict] = {}
        self.closed_trades: List[dict]    = []
        self._fired: set                  = set()

        # Most recent snapshot of each wallet's positions
        self._wallet_positions: Dict[str, dict] = {}  # wallet -> {key: pos}

        os.makedirs("logs", exist_ok=True)
        self._load_scores()

        n = len(self.wallets)
        log.info(f"BASKET init: {n} wallets  threshold={self.threshold:.0%}  budget=${self.capital:.2f}")

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _load_scores(self):
        """Load previously computed scores from disk to avoid re-fetching."""
        try:
            with open(SCORES_PATH) as f:
                raw = json.load(f)
            for w, d in raw.items():
                ws = WalletScore(w)
                ws.__dict__.update(d)
                self.scores[w] = ws
            log.info(f"BASKET loaded {len(self.scores)} cached scores")
        except FileNotFoundError:
            pass
        except Exception as e:
            log.error(f"BASKET score load: {e}")

    def _save_scores(self):
        try:
            with open(SCORES_PATH, "w") as f:
                json.dump({w: s.to_dict() for w, s in self.scores.items()}, f, indent=2)
        except Exception as e:
            log.error(f"BASKET score save: {e}")

    def rescore_wallets(self):
        """Re-score all wallets. Called on startup and every SCORE_IVTL seconds."""
        log.info(f"BASKET rescoring {len(self.wallets)} wallets...")
        for wallet in self.wallets:
            positions = self._wallet_positions.get(wallet, {})
            ws = score_wallet(wallet, positions)
            self.scores[wallet] = ws
        self._last_score_time = time.time()
        self._save_scores()
        self._log_score_table()

    def _log_score_table(self):
        log.info("BASKET SCORECARD:")
        for w in self.wallets:
            ws = self.scores.get(w)
            if ws:
                decay_flag = " ⚠️ DECAY" if ws.is_decaying else ""
                log.info(
                    f"  {w[:14]}  {ws.wallet_type:<12}  "
                    f"WR={ws.win_rate:.1%}  Sharpe={ws.sharpe:.2f}  "
                    f"Kelly={ws.kelly:.2f}  EV={ws.ev_per_trade:.3f}  "
                    f"score={ws.score:.0f}  grade={ws.grade}{decay_flag}"
                )

    def _eligible_wallets(self) -> List[str]:
        """Return wallets that are copyable based on grade and decay status."""
        eligible = []
        for w in self.wallets:
            ws = self.scores.get(w)
            if ws is None:
                continue
            if ws.wallet_type in ("MARKET_MAKER", "BOT"):
                continue
            if ws.is_decaying:
                continue
            if GRADE_RANK.get(ws.grade, 0) >= GRADE_RANK.get(MIN_GRADE, 3):
                eligible.append(w)
        return eligible

    # ── Consensus detection ───────────────────────────────────────────────────

    def _build_consensus(self, eligible: List[str]) -> List[Tuple[str, str, float, float]]:
        """
        Returns list of (market_slug, outcome, consensus_pct, avg_entry_price)
        where consensus_pct >= threshold and position not already fired.
        Only fires on BTC 5-min markets (slug contains 'btc-updown-5m').
        """
        if not eligible:
            return []

        # Count how many eligible wallets hold each market+outcome
        vote_count: Dict[str, int] = defaultdict(int)
        vote_price: Dict[str, List[float]] = defaultdict(list)

        for wallet in eligible:
            positions = self._wallet_positions.get(wallet, {})
            for key, pos in positions.items():
                slug = pos.get("slug", "")
                # Filter to BTC 5-min markets only
                if "btc-updown-5m" not in slug:
                    continue
                if key in self._fired:
                    continue
                vote_count[key] += 1
                avg_p = float(pos.get("avg_price", 0))
                if avg_p > 0:
                    vote_price[key].append(avg_p)

        signals = []
        total = len(eligible)
        for key, count in vote_count.items():
            pct = count / total if total > 0 else 0.0
            if pct >= self.threshold:
                # Parse slug and outcome from key (format: slug_OUTCOME)
                parts = key.rsplit("_", 1)
                if len(parts) != 2:
                    continue
                slug, outcome = parts[0], parts[1]
                prices = vote_price.get(key, [])
                avg_p = sum(prices) / len(prices) if prices else 0.5
                signals.append((slug, outcome, pct, avg_p))
                log.info(
                    f"BASKET CONSENSUS ✓  {slug[-14:]}  {outcome}  "
                    f"{count}/{total} wallets ({pct:.0%})  avg_entry=${avg_p:.3f}"
                )

        return signals

    # ── Sync loop ─────────────────────────────────────────────────────────────

    def sync(self) -> List[Tuple[str, str]]:
        """
        Main sync: fetch positions for all wallets, detect consensus, enter trades.
        Returns list of (kind, message) events.
        """
        events = []
        now    = time.time()

        # Re-score wallets periodically
        if now - self._last_score_time > SCORE_IVTL:
            self.rescore_wallets()

        # Fetch positions for all eligible wallets
        eligible = self._eligible_wallets()
        if not eligible:
            return events

        for wallet in eligible:
            try:
                positions = fetch_wallet_positions(wallet)
                self._wallet_positions[wallet] = positions
            except Exception as e:
                log.debug(f"BASKET fetch {wallet[:12]}: {e}")

        # Detect consensus signals
        signals = self._build_consensus(eligible)

        for slug, outcome, pct, avg_price in signals:
            key = f"{slug}_{outcome}"
            if key in self._fired:
                continue
            if self.capital < self.bet_size:
                events.append(("skip", f"BASKET SKIP {slug[-14:]} — insufficient capital"))
                continue

            # Compute Kelly-scaled bet size
            ws_list = [self.scores.get(w) for w in eligible if self.scores.get(w)]
            if ws_list:
                avg_kelly = sum(ws.kelly for ws in ws_list) / len(ws_list)
                kelly_bet = min(self.bet_size * 2, self.bet_size * (1 + avg_kelly))
            else:
                kelly_bet = self.bet_size

            kelly_bet = min(kelly_bet, self.capital)
            self.capital -= kelly_bet
            self._fired.add(key)

            trade = {
                "slug":         slug,
                "outcome":      outcome,
                "key":          key,
                "entry_price":  avg_price,
                "bet_size":     kelly_bet,
                "consensus_pct": pct,
                "n_voters":     int(pct * len(eligible)),
                "n_eligible":   len(eligible),
                "entered_at":   now,
                "url":          f"https://polymarket.com/event/{slug}",
                "profit":       0.0,
                "status":       "open",
            }
            self.open_trades[key] = trade

            msg = (
                f"BASKET ENTER ✓  {slug[-14:]}  {outcome}  "
                f"consensus={pct:.0%}  bet=${kelly_bet:.2f}  "
                f"entry=${avg_price:.3f}  "
                f"wallets={trade['n_voters']}/{trade['n_eligible']}"
            )
            log.info(msg)
            events.append(("new", msg))

        # Settle resolved trades
        for key, trade in list(self.open_trades.items()):
            slug    = trade["slug"]
            outcome = trade["outcome"]

            # Check all wallet positions for resolution
            resolved_outcome = None
            for wallet_pos in self._wallet_positions.values():
                for pos_key, pos in wallet_pos.items():
                    if pos.get("slug") == slug:
                        cur_p = float(pos.get("cur_price", 0))
                        if abs(cur_p - 1.0) < 0.01:
                            # Resolved
                            pos_outcome = pos.get("outcome", "").upper()
                            resolved_outcome = pos_outcome
                            break
                if resolved_outcome:
                    break

            if resolved_outcome is not None:
                won = (resolved_outcome == outcome)
                entry  = trade["entry_price"]
                bet    = trade["bet_size"]
                shares = bet / entry if entry > 0 else 0
                profit = (shares - bet) if won else -bet
                self.capital += shares if won else 0

                trade["profit"] = round(profit, 4)
                trade["status"] = "won" if won else "lost"
                trade["exit_price"] = 1.0 if won else 0.0
                trade["closed_at"]  = now

                self.closed_trades.append(dict(trade))
                del self.open_trades[key]

                msg = (
                    f"BASKET SETTLE  {slug[-14:]}  {outcome} → {resolved_outcome}  "
                    f"{'WIN ✓' if won else 'LOSS ✗'}  profit=${profit:+.2f}"
                )
                log.info(msg)
                events.append(("close", msg))

        return events

    def summary(self) -> dict:
        n    = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.get("status") == "won")
        pnl  = sum(t.get("profit", 0) for t in self.closed_trades)
        eligible = self._eligible_wallets()

        return {
            "capital":       round(self.capital, 2),
            "session_start": round(self.session_start, 2),
            "pnl":           round(pnl, 4),
            "n_open":        len(self.open_trades),
            "n_closed":      n,
            "n_won":         wins,
            "win_rate":      wins / n if n else 0.0,
            "n_wallets":     len(self.wallets),
            "n_eligible":    len(eligible),
            "threshold":     self.threshold,
            "open_trades":   list(self.open_trades.values()),
            "closed_trades": self.closed_trades[-50:],
            "scores": [
                self.scores[w].to_dict()
                for w in self.wallets
                if w in self.scores
            ],
        }
