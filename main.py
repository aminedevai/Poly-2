"""
main.py — Polymarket Bot Entry Point
=====================================
Run:  python main.py

Five independent paper-trading strategies + copy trader:
  1. Copy Trader        — mirrors target wallet at 50% scale
  2. Mean Reversion     — fades late BTC 5-min price extremes  ($100 budget)
  3. Volume Spike Sniper— follows volume spikes                 ($100 budget)
  4. Volume Contrarian  — bets UP on high-volume markets        ($100 budget)
  5. High Vol Momentum  — bets DOWN on high-volume markets      ($100 budget)
  6. Always Bet DOWN    — baseline control strategy             ($100 budget)
"""
import asyncio, threading, time
from datetime import datetime
from typing import List

import utils.logger as logger_mod
from utils.config  import COPY
from utils.colors  import C, green, cyan, orange, mg, bold, gray

from core.copy_trader import CopyTrader, load_memory, save_memory
from core.collector   import run as run_collector
from core.dashboard   import write_json, render

from strategy.mean_reversion import MeanReversion
from strategy.sniper         import Sniper
from strategy.contrarian     import Contrarian
from strategy.momentum       import Momentum
from strategy.always_down    import AlwaysDown


def input_thread(trader: CopyTrader):
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
    # Windows console color fix
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except: pass

    logger_mod.setup()
    log        = logger_mod.get("main")
    start_time = time.time()
    alerts:    List[str] = []

    # ── Initialise all strategies ─────────────────────────────────────────────
    memory      = load_memory()
    trader      = CopyTrader(memory)
    mr          = MeanReversion()
    sniper      = Sniper()
    contrarian  = Contrarian()
    momentum    = Momentum()
    always_down = AlwaysDown()

    print(f"\n  {bold('POLYMARKET BOT  —  PAPER MODE  (5 strategies)')}")
    print(f"  Copy:        {cyan(f'${trader.available:.2f}')}")
    print(f"  MR:          {mg(f'${mr.capital:.2f}')}")
    print(f"  Sniper:      {orange(f'${sniper.capital:.2f}')}")
    print(f"  Contrarian:  {cyan(f'${contrarian.capital:.2f}')}")
    print(f"  Momentum:    {green(f'${momentum.capital:.2f}')}")
    print(f"  Always Down: {gray(f'${always_down.capital:.2f}')}\n")

    n_total, n_loaded = trader.load_existing()
    print(f"  Target wallet: {n_total} positions — {green(str(n_loaded))} loaded")
    print(f"  {green('Watching for signals...')}\n")

    threading.Thread(target=input_thread, args=(trader,), daemon=True).start()
    asyncio.create_task(run_collector(sniper, mr, contrarian, momentum, always_down))
    await asyncio.sleep(2)

    # ── Main loop ─────────────────────────────────────────────────────────────
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
            write_json(trader, sniper, mr, contrarian, momentum, always_down,
                       start_time, alerts)
            render(trader, sniper, mr, contrarian, momentum, always_down,
                   start_time, alerts)
            await asyncio.sleep(poll)

    except KeyboardInterrupt:
        write_json(trader, sniper, mr, contrarian, momentum, always_down,
                   start_time, alerts)
        print(f"\n{C.YL}Shutting down...{C.R}")
        for ct in trader.close_all(reason="shutdown"):
            from utils.colors import pnlc
            print(f"  Closed {ct.market_title[:45]}  ${ct.realized_pnl:+.2f}")
        save_memory(trader.to_memory())

        # Print session summary for all strategies
        print(f"\n{'─'*60}")
        for name, strat in [("Copy Trader", trader), ("MR", mr), ("Sniper", sniper),
                             ("Contrarian", contrarian), ("Momentum", momentum),
                             ("Always Down", always_down)]:
            s = strat.summary()
            pnl = s.get('realized', s.get('pnl', 0))
            wr  = s.get('win_rate', 0)
            n   = s.get('n_closed', 0)
            print(f"  {name:<14} P&L: ${pnl:+.2f}  WR: {wr:.0%}  Trades: {n}")
        print(f"{'─'*60}")
        print(f"{green('Session saved.')}")


if __name__ == "__main__":
    asyncio.run(main())
