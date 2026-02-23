"""
main.py — Polymarket Bot Entry Point
=====================================
Run:  python main.py

Three independent systems, one process:
  • Copy Trader   — mirrors target wallet at 50% scale
  • Mean Reversion— fades late BTC 5-min price extremes ($100 paper)
  • Sniper        — follows volume spikes ($100 paper)
"""
import asyncio, threading, time
from datetime import datetime
from typing import List

import utils.logger as logger_mod
from utils.config import COPY
from utils.colors import C, green, cyan, orange, mg, bold, gray

from core.copy_trader import CopyTrader, load_memory, save_memory
from core.collector   import run as run_collector
from core.dashboard   import write_json, render
from strategy.sniper         import Sniper
from strategy.mean_reversion import MeanReversion


def input_thread(trader: CopyTrader):
    """Allow manual closing of copy-trader positions by typing index."""
    while True:
        try:
            line = input().strip()
            if line.isdigit():
                ps  = sorted(trader.positions.values(), key=lambda p: p.entry_amount, reverse=True)
                idx = int(line)
                if 0 <= idx < len(ps):
                    trader.manual_queue.add(ps[idx].key)
        except: pass


async def main():
    # ── Windows console fix ──────────────────────────────────────────────────
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except: pass

    logger_mod.setup()
    log        = logger_mod.get("main")
    start_time = time.time()
    alerts:    List[str] = []

    # ── Initialise components ────────────────────────────────────────────────
    memory  = load_memory()
    trader  = CopyTrader(memory)
    sniper  = Sniper()
    mr      = MeanReversion()

    print(f"\n  {bold('POLYMARKET BOT  ─  PAPER MODE')}")
    print(f"  Copy: {cyan(f'${trader.available:.2f}')}  "
          f"Sniper: {orange(f'${sniper.capital:.2f}')}  "
          f"MR: {mg(f'${mr.capital:.2f}')}\n")

    # Load existing target positions on startup
    n_total, n_loaded = trader.load_existing()
    print(f"  Target wallet: {C.WH}{n_total}{C.R} positions — "
          f"{green(str(n_loaded))} loaded")
    print(f"  {green('Watching for new bets + BTC 5-min signals...')}\n")

    threading.Thread(target=input_thread, args=(trader,), daemon=True).start()
    asyncio.create_task(run_collector(sniper, mr))
    await asyncio.sleep(2)

    # ── Main loop ────────────────────────────────────────────────────────────
    poll = COPY['poll_interval']
    try:
        while True:
            events = trader.sync()
            for kind, msg in events:
                ts = datetime.now().strftime('%H:%M:%S')
                alerts.append(f"[{ts}] {msg.split(chr(10))[0]}")
                alerts = alerts[-20:]
                log.info(msg.replace('\n', ' | '))
                if kind in ("new", "close"):
                    c = C.GR if kind == "new" else C.RE
                    print(f"\n{c}{'─'*65}{C.R}")
                    for line in msg.split('\n'): print(f"{c}{line}{C.R}")
                    print(f"{c}{'─'*65}{C.R}\n")
                    if kind == "new": await asyncio.sleep(3)

            save_memory(trader.to_memory())
            write_json(trader, sniper, mr, start_time, alerts)
            render(trader, sniper, mr, start_time, alerts)
            await asyncio.sleep(poll)

    except KeyboardInterrupt:
        write_json(trader, sniper, mr, start_time, alerts)
        print(f"\n{C.YL}Shutting down...{C.R}")
        for ct in trader.close_all(reason="shutdown"):
            from utils.colors import pnlc
            print(f"  Closed {ct.market_title[:45]}  "
                  f"{pnlc(ct.realized_pnl, f'${ct.realized_pnl:+.2f}')}")
        save_memory(trader.to_memory())
        s  = trader.summary()
        ss = sniper.summary()
        ms = mr.summary()
        print(f"\n{'─'*50}")
        print(f"Copy Trader  — P&L: ${s['realized']:+.2f}  "
              f"Trades: {s['n_closed']}")
        print(f"Sniper       — P&L: ${ss['pnl']:+.2f}  "
              f"Win: {ss['win_rate']:.0%}  Trades: {ss['n_closed']}")
        print(f"Mean Reversion—P&L: ${ms['pnl']:+.2f}  "
              f"Win: {ms['win_rate']:.0%}  Trades: {ms['n_closed']}")
        print(f"{green('Session saved. Next run continues from here.')}")


if __name__ == "__main__":
    asyncio.run(main())
