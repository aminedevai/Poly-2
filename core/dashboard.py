"""core/dashboard.py — Writes dashboard_data.json and renders the terminal UI.

Snapshot timing (confirmed):
  Slug open_ts = market OPEN timestamp
  Market closes at open_ts + 300 (5 minutes later)

  Snapshots fire AT these seconds-before-close marks:
    240s = right at market OPEN   (baseline for sniper vol comparison)
    180s = 1 min in
    120s = 2 min in
     60s = 3 min in
     30s = 4:30 in  ← MR signal fires here
     10s = 4:50 in  ← MR records unrealised P&L (no exit — hold to settlement)
"""
import json, os, sys, time
from datetime import datetime
from typing import List

from core.collector import live_status, row_count
from utils.config        import PATHS
from utils.colors        import (C, green, red, yel, cyan, gray, bold,
                                 blue, orange, mg, pnlc, trunc, pad)
from utils.time_helpers  import time_left_from_ts

W = 132   # wider to fit URL column in tracker


def _div(n=None):
    return gray("─" * (n or W - 4))


# ── JSON export ───────────────────────────────────────────────────────────────

def write_json(trader, sniper, mr, start_time: float, alerts: List[str]):
    s  = trader.summary()
    ss = sniper.summary()
    ms = mr.summary()
    ps = sorted(trader.positions.values(),
                key=lambda p: p.entry_amount, reverse=True)
    invested_pct = s["invested"] / s["budget"] * 100 if s["budget"] else 0

    data = {
        "wallet":     trader.wallet,
        "start_time": start_time,
        "updated_at": time.time(),
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
             "entry_price": round(t.entry_price, 4),
             "exit_price":  round(t.exit_price,  4),
             "entry_amount": round(t.entry_amount, 2),
             "exit_amount":  round(t.exit_amount,  2),
             "realized_pnl": round(t.realized_pnl, 2),
             "closed_at": t.closed_at, "reason": t.reason,
             "url": t.url, "roi_pct": round(t.roi_pct, 1)}
            for t in trader.closed_trades[-20:]
        ],
        "sniper": {
            "capital":  round(ss["capital"], 2),
            "pnl":      round(ss["pnl"],     2),
            "n_closed": ss["n_closed"],
            "n_won":    ss["n_won"],
            "win_rate": round(ss["win_rate"], 3),
            "open_trades": [
                {"slug": t.slug, "direction": t.direction,
                 "entry_price": round(t.entry_price, 4),
                 "vol_ratio": round(t.vol_ratio, 1),
                 "move": round(t.move, 3),
                 "end_ts": t.end_ts, "entered_at": t.entered_at,
                 "url": t.url}
                for t in sniper.open_trades.values()
            ],
            "closed_trades": [
                {"slug": t.slug, "direction": t.direction,
                 "entry_price": round(t.entry_price, 4),
                 "exit_price":  round(t.exit_price,  4),
                 "profit": round(t.profit, 2), "roi_pct": round(t.roi_pct, 1),
                 "vol_ratio": round(t.vol_ratio, 1),
                 "status": t.status, "entered_at": t.entered_at,
                 "url": t.url}
                for t in sniper.closed_trades[-30:]
            ],
        },
        "mr": {
            "capital":  round(ms["capital"], 2),
            "pnl":      round(ms["pnl"],     2),
            "n_closed": ms["n_closed"],
            "n_won":    ms["n_won"],
            "win_rate": round(ms["win_rate"], 3),
            "open_trades": [
                {"slug": t.slug, "direction": t.direction,
                 "p30": round(t.p30, 3),
                 "entry_price": round(t.entry_price, 4),
                 "end_ts": t.end_ts, "entered_at": t.entered_at,
                 "url": t.url}
                for t in mr.open_trades.values()
            ],
            "closed_trades": [
                {"slug": t.slug, "direction": t.direction,
                 "p30": round(t.p30, 3), "p10": round(t.p10, 3),
                 "entry_price": round(t.entry_price, 4),
                 "exit_price":  round(t.exit_price,  4),
                 "profit": round(t.profit, 2), "roi_pct": round(t.roi_pct, 1),
                 "status": t.status, "entered_at": t.entered_at,
                 "url": t.url}
                for t in mr.closed_trades[-30:]
            ],
        },
        "live_markets": list(
            sorted(live_status.values(), key=lambda x: x["end_ts"])
        ),
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

def render(trader, sniper, mr, start_time: float, alerts: List[str]):
    s   = trader.summary()
    ss  = sniper.summary()
    ms  = mr.summary()
    ps  = sorted(trader.positions.values(),
                 key=lambda p: p.entry_amount, reverse=True)
    cts = trader.closed_trades[-6:]
    up  = time.time() - start_time
    h, rem = divmod(int(up), 3600)
    m, sc  = divmod(rem, 60)

    pct    = s["invested"] / s["budget"] * 100 if s["budget"] else 0
    bar_w  = 24
    filled = int(pct / 100 * bar_w)
    bar    = C.YL + "█" * filled + C.GY + "░" * (bar_w - filled) + C.R
    spnl   = s["available"] - s["session_start"]
    n_rows = row_count()
    L      = []

    # ── Header ────────────────────────────────────────────────────────────────
    L += [
        f"{C.CY}╔{'═' * (W - 2)}╗{C.R}",
        f"{C.CY}║{C.R}  {bold('POLYMARKET BOT  —  PAPER MODE'):<44}"
        f"  uptime {gray(f'{h:02d}:{m:02d}:{sc:02d}')}"
        f"  {yel(trader.wallet[:22] + '...')}"
        f"  {C.CY}║{C.R}",
        f"{C.CY}╚{'═' * (W - 2)}╝{C.R}",
        "",
    ]

    # ── Budget ────────────────────────────────────────────────────────────────
    b_bgt = cyan(f"${s['budget']:.2f}")
    b_avl = green(f"${s['available']:.2f}")
    b_inv = yel(f"${s['invested']:.2f}")
    b_rlz = pnlc(s["realized"],   f"${s['realized']:+.2f}")
    b_unr = pnlc(s["unrealized"], f"${s['unrealized']:+.2f}")
    b_sp  = pnlc(spnl,            f"${spnl:+.2f}")
    L += [
        f"  {green('▼ COPY TRADER BUDGET')}",
        f"  {_div(90)}",
        f"  Portfolio {b_bgt}   Available {b_avl}   Invested {b_inv} ({pct:.0f}%)  {bar}",
        f"  Realized {b_rlz}   Unrealized {b_unr}   Session {b_sp}",
        "",
    ]

    # ── Open Positions ────────────────────────────────────────────────────────
    CM, CS, CE, CN, CI, CP, CR, CT = 30, 6, 8, 8, 9, 10, 7, 10
    L += [
        f"  {C.YL}▼ OPEN POSITIONS  ({s['n_open']}){C.R}",
        f"  {_div()}",
        f"  {bold('[#]'):4} {bold('Market'):{CM}} {bold('Side'):^{CS}} "
        f"{bold('Entry'):>{CE}} {bold('Now'):>{CN}} {bold('In$'):>{CI}} "
        f"{bold('P&L'):>{CP}} {bold('ROI'):>{CR}} {bold('Time Left'):>{CT}}  {bold('URL')}",
        f"  {_div()}",
    ]
    if ps:
        for i, p in enumerate(ps[:10]):
            tl, _  = time_left_from_ts(p.end_ts)
            side_c = green(p.outcome) if p.outcome.lower() in ("yes", "up") else red(p.outcome)
            L.append(
                f"  {pad(cyan(f'[{i}]'), 4, '>')} "
                f"{trunc(p.market_title, CM):{CM}} "
                f"{pad(side_c, CS, '^')} "
                f"{pad(gray(f'${p.entry_price:.4f}'), CE, '>')} "
                f"{pad(cyan(f'${p.cur_price:.4f}'), CN, '>')} "
                f"{pad(yel(f'${p.entry_amount:.2f}'), CI, '>')} "
                f"{pad(pnlc(p.pnl, f'${p.pnl:+.2f}'), CP, '>')} "
                f"{pad(pnlc(p.roi_pct, f'{p.roi_pct:+.1f}%'), CR, '>')} "
                f"{pad(tl, CT, '>')}  {blue(p.url)}"
            )
    else:
        L.append(f"  {gray('No open positions.')}")
    L.append("")

    # ── Closed Trades ─────────────────────────────────────────────────────────
    CC = 8
    L += [
        f"  {C.GR}▼ CLOSED TRADES  ({s['n_closed']} total — last 6){C.R}",
        f"  {_div()}",
        f"  {bold('[#]'):4} {bold('Market'):{CM}} {bold('Side'):^{CS}} "
        f"{bold('Entry'):>{CE}} {bold('Exit'):>{CN}} {bold('In$'):>{CI}} "
        f"{bold('Out$'):>{CP}} {bold('Profit'):>{CR}} {bold('ROI'):>7}  "
        f"{bold('How'):<8}  {bold('Closed'):>{CC}}  {bold('URL')}",
        f"  {_div()}",
    ]
    if cts:
        for i, t in enumerate(reversed(cts)):
            roi_s  = pnlc(t.roi_pct,      f"{t.roi_pct:+.1f}%")
            pnl_s  = pnlc(t.realized_pnl, f"${t.realized_pnl:+.2f}")
            side_c = green(t.outcome) if t.outcome.lower() in ("yes","up") else red(t.outcome)
            rsn_c  = (orange(t.reason) if t.reason == "manual"
                      else red(t.reason) if t.reason == "expired"
                      else gray(t.reason))
            ct_time = gray(datetime.fromtimestamp(t.closed_at).strftime("%H:%M:%S"))
            L.append(
                f"  {pad(cyan(f'[{i}]'), 4, '>')} "
                f"{trunc(t.market_title, CM):{CM}} "
                f"{pad(side_c, CS, '^')} "
                f"{pad(gray(f'${t.entry_price:.4f}'), CE, '>')} "
                f"{pad(cyan(f'${t.exit_price:.4f}'), CN, '>')} "
                f"{pad(yel(f'${t.entry_amount:.2f}'), CI, '>')} "
                f"{pad(cyan(f'${t.exit_amount:.2f}'), CP, '>')} "
                f"{pad(pnl_s, CR, '>')} "
                f"{pad(roi_s, 7, '>')}  "
                f"{pad(rsn_c, 8)}  "
                f"{pad(ct_time, CC, '>')}  {blue(t.url)}"
            )
    else:
        L.append(f"  {gray('No closed trades yet.')}")
    L.append("")

    # ── BTC 5-MIN LIVE TRACKER ────────────────────────────────────────────────
    L += [
        f"  {C.CY}▼ BTC 5-MIN LIVE TRACKER{C.R}"
        f"  {gray('· 240s=open  · 30s=MR signal  · 10s=hold check')}",
        f"  {_div(W - 4)}",
        f"  {'Market':<32} {'Closes In':>10}  {'UP':>6}  {'Dev':>5}  "
        f"{'Vol':>9}  {'Snaps':>22}  {'Phase':>10}  {'Out':>4}  URL",
        f"  {_div(W - 4)}",
    ]
    if live_status:
        for st in sorted(live_status.values(), key=lambda x: x["end_ts"]):
            tl, _ = time_left_from_ts(st["end_ts"])
            price = st["up_price"]
            dev   = abs(price - 0.5) if price else 0
            p_s   = (red   if price and price < 0.45 else
                     green if price and price > 0.55 else gray)(
                         f"{price:.3f}" if price else "  --  ")
            dev_s = orange(f"{dev * 100:.1f}%") if dev > 0.05 else gray(f"{dev * 100:.1f}%")
            done  = st.get("snaps_done", [])
            snaps_s = " ".join(
                green(str(s)) if s in done else gray(str(s))
                for s in [240, 180, 120, 60, 30, 10]
            )
            phase_c = (green(st["phase"])  if st["phase"] == "closing"
                       else cyan(st["phase"])  if st["phase"] == "live"
                       else orange(st["phase"]))
            oc = (green(st["outcome"])  if st["outcome"] == "UP"
                  else red(st["outcome"]) if st["outcome"] == "DOWN"
                  else gray("--"))
            url_s = blue(st.get("url", ""))  # ← URL now shown per row
            L.append(
                f"  {st['slug']:<32} {pad(tl, 10, '>')}  "
                f"{pad(p_s, 6, '>')}  {pad(dev_s, 5, '>')}  "
                f"${st['volume']:>8,.0f}  {snaps_s}  "
                f"{pad(phase_c, 10, '>')}  {pad(oc, 4, '^')}  {url_s}"
            )
    else:
        L.append(f"  {gray('Initializing collector...')}")
    L.append("")

    # ── Sniper ────────────────────────────────────────────────────────────────
    sp_pnl  = ss["pnl"]; sp_cap = ss["capital"]
    sp_sess = sp_cap - ss["session_start"]
    wr_c    = (green(f"{ss['win_rate']:.0%}") if ss["win_rate"] >= 0.6
               else red(f"{ss['win_rate']:.0%}") if ss["n_closed"] > 0
               else gray("N/A"))
    L += [
        f"  {orange('▼ SNIPER  (volume-spike — $100 budget)')}",
        f"  {_div(90)}",
        f"  Capital {cyan(f'${sp_cap:.2f}')}   Session {pnlc(sp_sess, f'${sp_sess:+.2f}')}   "
        f"Closed {yel(str(ss['n_closed']))}   Won {green(str(ss['n_won']))}   "
        f"Win rate {wr_c}   P&L {pnlc(sp_pnl, f'${sp_pnl:+.2f}')}",
    ]
    if sniper.open_trades:
        for slug, t in sniper.open_trades.items():
            tl, _ = time_left_from_ts(t.end_ts)
            dc    = green if t.direction == "UP" else red
            L.append(f"    {slug[-12:]}  {pad(dc(t.direction), 6, '^')}  "
                     f"entry={t.entry_price:.4f}  vol={t.vol_ratio:.0f}x  {tl}  "
                     f"{blue(t.url)}")
    L.append("")

    # ── Mean Reversion ────────────────────────────────────────────────────────
    mr_pnl  = ms["pnl"]; mr_cap = ms["capital"]
    mr_sess = mr_cap - ms["session_start"]
    mr_wr   = (green(f"{ms['win_rate']:.0%}") if ms["win_rate"] >= 0.7
               else red(f"{ms['win_rate']:.0%}") if ms["n_closed"] > 0
               else gray("N/A"))
    L += [
        f"  {mg('▼ MEAN REVERSION  (fade late moves — $100 budget, hold to settle)')}",
        f"  {_div(90)}",
        f"  Capital {cyan(f'${mr_cap:.2f}')}   Session {pnlc(mr_sess, f'${mr_sess:+.2f}')}   "
        f"Closed {yel(str(ms['n_closed']))}   Won {green(str(ms['n_won']))}   "
        f"Win rate {mr_wr}   P&L {pnlc(mr_pnl, f'${mr_pnl:+.2f}')}",
        f"  {gray('Signal: price >5% from $0.50 at 30s → buy cheap side → hold to settlement')}",
    ]
    if mr.open_trades:
        for slug, t in mr.open_trades.items():
            tl, _ = time_left_from_ts(t.end_ts)
            dc    = green if t.direction == "UP" else red
            L.append(f"    {slug[-12:]}  {pad(dc(t.direction), 6, '^')}  "
                     f"p30={t.p30:.3f}  entry={t.entry_price:.4f}  {tl}  "
                     f"{blue(t.url)}")
    if mr.closed_trades:
        L.append(f"  {gray('Last trades:')}")
        for t in reversed(mr.closed_trades[-4:]):
            dt = datetime.fromtimestamp(t.entered_at).strftime("%H:%M:%S")
            dc = green if t.direction == "UP" else red
            rc = green("WIN") if t.status == "settled_win" else red("LOSS")
            pc = pnlc(t.profit, f"${t.profit:+.2f}")
            L.append(f"    {dt}  {t.slug[-12:]}  {pad(dc(t.direction), 6, '^')}  "
                     f"p30={t.p30:.3f}  entry={t.entry_price:.4f}  "
                     f"{pad(rc, 7, '^')}  {pad(pc, 8, '>')}  {blue(t.url)}")
    L.append("")

    # ── Alerts ────────────────────────────────────────────────────────────────
    L += [f"  {C.MG}▼ ALERTS{C.R}", f"  {_div(80)}"]
    if alerts:
        for a in alerts[-6:]:
            c = (C.GR if "NEW BET" in a
                 else C.OR if "SNIPER" in a
                 else C.MG if "MR "    in a
                 else C.RE if any(x in a for x in ("CLOSED", "EXPIRED"))
                 else C.YL)
            L.append(f"  {c}{a}{C.R}")
    else:
        L.append(f"  {gray('Monitoring...')}")

    L.append(
        f"\n  {gray(f'Collector: {n_rows} snapshots')}  "
        f"{gray('|')}"
        f"  {gray('Type position number + Enter to manually close  |  Ctrl+C = shutdown')}"
    )

    os.system("cls" if os.name == "nt" else "clear")
    print("\n".join(L))
    sys.stdout.flush()
