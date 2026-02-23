"""core/dashboard.py — Writes dashboard_data.json and renders terminal UI.
All 5 strategies: MR, Sniper, Contrarian, Momentum, Always Down.
"""
import json, os, sys, time
from datetime import datetime
from typing import List

from core.collector import live_status, row_count
from utils.config       import PATHS
from utils.colors       import (C, green, red, yel, cyan, gray, bold,
                                blue, orange, mg, pnlc, trunc, pad)
from utils.time_helpers import time_left_from_ts

W = 132


def _div(n=None): return gray("─" * (n or W - 4))


def _strat_json(s, strat_obj, extra_open_fields=None, extra_closed_fields=None):
    """Build standard strategy JSON block from summary dict + trade objects."""
    open_trades   = s.get('open_trades', [])
    closed_trades = s.get('closed_trades', [])
    return {
        'capital':      round(s['capital'],       2),
        'session_start':round(s['session_start'], 2),
        'pnl':          round(s['pnl'],           2),
        'n_closed':     s['n_closed'],
        'n_won':        s['n_won'],
        'win_rate':     round(s['win_rate'],      3),
        'n_open':       s['n_open'],
        'open_trades':  open_trades,
        'closed_trades':closed_trades,
    }


# ── JSON export ───────────────────────────────────────────────────────────────

def write_json(trader, sniper, mr, contrarian, momentum, always_down,
               start_time: float, alerts: List[str]):
    s   = trader.summary()
    ss  = sniper.summary()
    ms  = mr.summary()
    cs  = contrarian.summary()
    mos = momentum.summary()
    ads = always_down.summary()

    ps          = sorted(trader.positions.values(), key=lambda p: p.entry_amount, reverse=True)
    invested_pct= s["invested"] / s["budget"] * 100 if s["budget"] else 0

    data = {
        "wallet":      trader.wallet,
        "start_time":  start_time,
        "updated_at":  time.time(),
        "summary": {
            "budget":        round(s["budget"],        2),
            "available":     round(s["available"],     2),
            "invested":      round(s["invested"],      2),
            "invested_pct":  round(invested_pct,       1),
            "realized":      round(s["realized"],      2),
            "unrealized":    round(s["unrealized"],    2),
            "returned":      round(s["returned"],      2),
            "session_start": round(s["session_start"], 2),
            "n_open":        s["n_open"],
            "n_closed":      s["n_closed"],
        },
        "positions": [
            {"market_title": p.market_title, "outcome": p.outcome,
             "slug": p.slug, "entry_price": round(p.entry_price, 4),
             "cur_price": round(p.cur_price, 4),
             "entry_amount": round(p.entry_amount, 2),
             "cur_value": round(p.cur_value, 2),
             "end_ts": p.end_ts, "url": p.url}
            for p in ps[:20]
        ],
        "closed_trades": [
            {"market_title": t.market_title, "outcome": t.outcome,
             "entry_price": round(t.entry_price, 4), "exit_price": round(t.exit_price, 4),
             "entry_amount": round(t.entry_amount, 2), "exit_amount": round(t.exit_amount, 2),
             "realized_pnl": round(t.realized_pnl, 2),
             "closed_at": t.closed_at, "reason": t.reason,
             "url": t.url, "roi_pct": round(t.roi_pct, 1)}
            for t in trader.closed_trades[-20:]
        ],

        # ── 5 strategies ──────────────────────────────────────────────────────
        "sniper":      _strat_json(ss, sniper),
        "mr":          _strat_json(ms, mr),
        "contrarian":  _strat_json(cs, contrarian),
        "momentum":    _strat_json(mos, momentum),
        "always_down": _strat_json(ads, always_down),

        "live_markets":   list(sorted(live_status.values(), key=lambda x: x["end_ts"])),
        "alerts":         alerts[-30:],
        "collector_rows": row_count(),
    }

    tmp = PATHS["dashboard_json"] + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, PATHS["dashboard_json"])
    except Exception:
        pass


# ── Terminal render ───────────────────────────────────────────────────────────

def _strat_block(label: str, color, s: dict, win_threshold: float = 0.55) -> List[str]:
    """Render a compact strategy block for the terminal."""
    cap  = s['capital']; sess_pnl = cap - s['session_start']
    pnl  = s['pnl'];     wr = s['win_rate']
    wr_c = (green(f"{wr:.0%}") if wr >= win_threshold
            else red(f"{wr:.0%}") if s['n_closed'] > 0 else gray("N/A"))
    L = [
        f"  {color}▼ {label}{C.R}  "
        f"Capital {cyan(f'${cap:.2f}')}  "
        f"Session {pnlc(sess_pnl, f'${sess_pnl:+.2f}')}  "
        f"Closed {yel(str(s['n_closed']))}  "
        f"Won {green(str(s['n_won']))}  "
        f"WR {wr_c}  "
        f"P&L {pnlc(pnl, f'${pnl:+.2f}')}",
    ]
    # Open trades
    for t in s.get('open_trades', []):
        tl, _ = time_left_from_ts(t.get('end_ts', 0))
        dc = green if t['direction'] == "UP" else red
        extras = ""
        if 'p30' in t:      extras += f"  p30={t['p30']:.3f}"
        if 'vol_ratio' in t: extras += f"  vol={t['vol_ratio']:.0f}x"
        if 'volume' in t:   extras += f"  vol=${t['volume']:,.0f}"
        L.append(f"    {t['slug'][-12:]}  {pad(dc(t['direction']), 6, '^')}  "
                 f"entry={t['entry_price']:.4f}{extras}  {tl}  {blue(t.get('url',''))}")
    # Last 4 closed trades
    closed = s.get('closed_trades', [])
    if closed:
        for t in closed[-4:]:
            dt  = datetime.fromtimestamp(t.get('entered_at', 0)).strftime("%H:%M")
            dc  = green if t['direction'] == "UP" else red
            rc  = green("WIN") if t['status'] == "won" else red("LOSS")
            pc  = pnlc(t['profit'], f"${t['profit']:+.2f}")
            L.append(f"    {dt}  {t['slug'][-12:]}  {pad(dc(t['direction']), 6, '^')}  "
                     f"entry={t['entry_price']:.4f}  exit={t['exit_price']:.4f}  "
                     f"{pad(rc, 7, '^')}  {pad(pc, 8, '>')}  {blue(t.get('url',''))}")
    return L


def render(trader, sniper, mr, contrarian, momentum, always_down,
           start_time: float, alerts: List[str]):
    s   = trader.summary()
    ss  = sniper.summary()
    ms  = mr.summary()
    cs  = contrarian.summary()
    mos = momentum.summary()
    ads = always_down.summary()

    ps  = sorted(trader.positions.values(), key=lambda p: p.entry_amount, reverse=True)
    cts = trader.closed_trades[-4:]
    up  = time.time() - start_time
    h, rem = divmod(int(up), 3600); m, sc = divmod(rem, 60)

    pct    = s["invested"] / s["budget"] * 100 if s["budget"] else 0
    bar_w  = 20
    filled = int(pct / 100 * bar_w)
    bar    = C.YL + "█" * filled + C.GY + "░" * (bar_w - filled) + C.R
    n_rows = row_count()
    L = []

    # Header
    L += [
        f"{C.CY}╔{'═'*(W-2)}╗{C.R}",
        f"{C.CY}║{C.R}  {bold('POLYMARKET BOT  —  PAPER MODE  (5 STRATEGIES)'):<52}"
        f"  uptime {gray(f'{h:02d}:{m:02d}:{sc:02d}')}"
        f"  {yel(trader.wallet[:22]+'...')}  {C.CY}║{C.R}",
        f"{C.CY}╚{'═'*(W-2)}╝{C.R}", "",
    ]

    # Copy Trader budget
    spnl = s["available"] - s["session_start"]
    budget_s    = f"${s['budget']:.2f}"
    avail_s     = f"${s['available']:.2f}"
    invest_s    = f"${s['invested']:.2f}"
    real_s      = f"${s['realized']:+.2f}"
    unreal_s    = f"${s['unrealized']:+.2f}"
    L += [
        (f"  {bold('COPY TRADER')}  Budget {cyan(budget_s)}  Available {green(avail_s)}  "
         f"Invested {yel(invest_s)} [{bar}{yel(f' {pct:.0f}%')}]  "
         f"Realized {pnlc(s['realized'], real_s)}  Unrealized {pnlc(s['unrealized'], unreal_s)}"),
        "",
    ]

    # Open positions (compact)
    CM = 28
    L += [f"  {C.YL}▼ OPEN POSITIONS ({s['n_open']}){C.R}", f"  {_div(100)}"]
    if ps:
        for i, p in enumerate(ps[:8]):
            tl, _ = time_left_from_ts(p.end_ts)
            sc2   = green(p.outcome) if p.outcome.lower() in ("yes","up") else red(p.outcome)
            L.append(f"  [{i}] {trunc(p.market_title,CM):{CM}} {pad(sc2,5,'^')} "
                     f"entry={gray(f'${p.entry_price:.4f}')} now={cyan(f'${p.cur_price:.4f}')} "
                     f"in={yel(f'${p.entry_amount:.2f}')} "
                     f"pnl={pnlc(p.pnl,f'${p.pnl:+.2f}')} {tl}  {blue(p.url)}")
    else:
        L.append(f"  {gray('No open positions.')}")
    L.append("")

    # BTC 5-Min Live Tracker (compact)
    L += [f"  {C.CY}▼ BTC 5-MIN LIVE TRACKER{C.R}", f"  {_div(120)}"]
    if live_status:
        for st in sorted(live_status.values(), key=lambda x: x["end_ts"]):
            tl, _ = time_left_from_ts(st["end_ts"])
            price = st["up_price"] or 0
            dev   = abs(price - 0.5)
            p_s   = (red if price < 0.45 else green if price > 0.55 else gray)(f"{price:.3f}")
            done  = st.get("snaps_done", [])
            snaps = " ".join(green(str(s2)) if s2 in done else gray(str(s2))
                             for s2 in [240, 180, 120, 60, 30, 10])
            oc    = (green(st["outcome"]) if st["outcome"] == "UP"
                     else red(st["outcome"]) if st["outcome"] == "DOWN" else gray("--"))
            L.append(f"  {st['slug']:<32} {pad(tl,8,'>')}  {pad(p_s,6,'>')}  "
                     f"dev={orange(f'{dev*100:.1f}%') if dev>0.05 else gray(f'{dev*100:.1f}%')}  "
                     f"${st['volume']:>8,.0f}  {snaps}  {pad(oc,4,'^')}  {blue(st.get('url',''))}")
    else:
        L.append(f"  {gray('Initializing...')}")
    L.append("")

    # ── All 5 strategies ──────────────────────────────────────────────────────
    L += _strat_block("MEAN REVERSION  (fade late moves · $100 paper)", C.MG, ms, 0.65)
    L.append("")
    L += _strat_block("VOLUME SPIKE SNIPER  (follow vol spikes · $100 paper)", C.OR, ss, 0.55)
    L.append("")
    L += _strat_block("VOLUME CONTRARIAN  (bet UP on high-vol · $100 paper)", C.CY, cs, 0.52)
    L.append("")
    L += _strat_block("HIGH VOLUME MOMENTUM  (bet DOWN on high-vol · $100 paper)", "\033[94m", mos, 0.50)
    L.append("")
    L += _strat_block("ALWAYS BET DOWN  (baseline control · $100 paper)", C.GY, ads, 0.49)
    L.append("")

    # Alerts
    L += [f"  {C.MG}▼ ALERTS{C.R}", f"  {_div(80)}"]
    if alerts:
        for a in alerts[-6:]:
            c = (C.GR if "NEW BET" in a else C.OR if "SNIPER" in a
                 else C.MG if "MR" in a else C.RE if any(x in a for x in ("CLOSED","EXPIRED"))
                 else C.YL)
            L.append(f"  {c}{a}{C.R}")
    else:
        L.append(f"  {gray('Monitoring...')}")

    L.append(f"\n  {gray(f'Snapshots: {n_rows}  |  Ctrl+C = shutdown  |  Type position # + Enter to close')}")

    os.system("cls" if os.name == "nt" else "clear")
    print("\n".join(L))
    sys.stdout.flush()
