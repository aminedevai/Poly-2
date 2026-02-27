"""
main.py — Polymarket Bot Entry Point
=====================================
7 independent paper-trading strategies + copy trader:
  1. Copy Trader           — mirrors target wallet at 50% scale
  2. Mean Reversion        — fades 2-15% deviations at T-30s       ($100)
  3. Volume Spike Sniper   — follows vol spikes, entry $0.40-$0.50  ($100)
  4. Volume Contrarian     — bets UP on $1k-$20k vol markets        ($100)
  5. HV Momentum           — bets DOWN on $1k-$20k vol markets      ($100)
  6. Always Bet DOWN       — baseline, DOWN token $0.40-$0.50       ($100)
  7. Last-Second Momentum  — bets on |p10-p30| > 0.10 move         ($100)
"""
import asyncio, threading, time
from datetime import datetime
from typing import List

import utils.logger as logger_mod
from utils.config  import COPY, CFG
from utils.colors  import C, green, cyan, orange, mg, bold, gray

from core.copy_trader import CopyTrader, load_memory, save_memory
from core.collector   import run as run_collector
from core.dashboard   import write_json, render

from strategy.mean_reversion import MeanReversion
from strategy.sniper         import Sniper
from strategy.contrarian     import Contrarian
from strategy.momentum       import Momentum
from strategy.always_down    import AlwaysDown
from strategy.last_second    import LastSecond

from core.basket_trader      import BasketTrader
from core.strategy_watchdog  import StrategyWatchdog
from core.btc_leaderboard    import BtcLeaderboard


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
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except: pass

    logger_mod.setup()
    log        = logger_mod.get("main")
    start_time = time.time()
    alerts:    List[str] = []

    # ── Init all strategies ────────────────────────────────────────────────────
    memory     = load_memory()
    trader     = CopyTrader(memory)
    mr         = MeanReversion()
    sniper     = Sniper()

    def _cfg(key, **defaults):
        d = CFG.get(key, {})
        return {**defaults, **d}

    contrarian  = Contrarian(**_cfg('contrarian',
        budget=100.0, bet_size=5.0, min_volume=1000.0, max_volume=20000.0,
        up_min=0.40, up_max=0.49999))
    momentum    = Momentum(**_cfg('momentum',
        budget=100.0, bet_size=5.0, min_volume=1000.0, max_volume=20000.0,
        down_min=0.40, down_max=0.49999))
    always_down = AlwaysDown(**_cfg('always_down',
        budget=100.0, bet_size=5.0, min_volume=100.0,
        down_min=0.40, down_max=0.49999))
    last_second = LastSecond(**_cfg('last_second',
        budget=100.0, bet_size=5.0, min_move=0.10))

    print(f"\n  {bold('POLYMARKET BOT  v13  —  PAPER MODE')}")
    print(f"  {'─'*50}")
    print(f"  Copy Trader:      {cyan(f'${trader.available:.2f}')}")
    print(f"  MR (2-15%):       {mg(f'${mr.capital:.2f}')}  band=[{mr._min_dev},{mr._max_dev})")
    print(f"  Sniper:           {orange(f'${sniper.capital:.2f}')}  entry $0.40-$0.4999")
    print(f"  Contrarian:       {cyan(f'${contrarian.capital:.2f}')}  vol $1k-$20k  UP $0.40-$0.4999")
    print(f"  Momentum:         {green(f'${momentum.capital:.2f}')}  vol $1k-$20k  DOWN $0.40-$0.4999")
    print(f"  Always DOWN:      {gray(f'${always_down.capital:.2f}')}  entry $0.40-$0.4999")
    print(f"  Last-Second:      {green(f'${last_second.capital:.2f}')}  |p10-p30| >= {last_second.min_move}")
    print(f"  {'─'*50}")

    n_total, n_loaded = trader.load_existing()
    print(f"  Target wallet: {n_total} positions — {green(str(n_loaded))} loaded")
    print(f"  {green('All 8 strategies live. Watching...')}\n")

    # ── Init new features ─────────────────────────────────────────────────────
    basket    = BasketTrader()
    watchdog  = StrategyWatchdog()
    leaderboard = BtcLeaderboard()

    # Kick off first leaderboard refresh in background
    threading.Thread(target=leaderboard.refresh, daemon=True).start()

    # Score basket wallets on startup (background to not block)
    threading.Thread(target=basket.rescore_wallets, daemon=True).start()

    print(f"  {cyan('Basket Trader:')}    {len(basket.wallets)} wallets  threshold={basket.threshold:.0%}")
    print(f"  {cyan('Strategy Watchdog:')} monitoring 7 strategies")
    print(f"  {cyan('BTC Leaderboard:')}  top traders last 1h/30m")
    print(f"  {bold('─'*50)}")

    threading.Thread(target=input_thread, args=(trader,), daemon=True).start()
    asyncio.create_task(
        run_collector(sniper, mr, contrarian, momentum, always_down, last_second)
    )
    await asyncio.sleep(2)

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
                    col = C.GR if kind == "new" else C.RE
                    print(f"\n{col}{'─'*65}{C.R}")
                    for line in msg.split('\n'): print(f"{col}{line}{C.R}")
                    print(f"{col}{'─'*65}{C.R}\n")
                    if kind == "new": await asyncio.sleep(3)

            # Basket consensus copy
            basket_events = basket.sync()
            for kind, msg in basket_events:
                ts = datetime.now().strftime("%H:%M:%S")
                alerts.append(f"[{ts}] {msg.split(chr(10))[0]}")
                alerts = alerts[-20:]
                log.info(msg)

            # Strategy watchdog check
            strat_map = {
                "sniper":      sniper,
                "mr":          mr,
                "contrarian":  contrarian,
                "momentum":    momentum,
                "always_down": always_down,
                "last_second": last_second,
                "basket":      basket,
            }
            decay_alerts = watchdog.check(strat_map)
            for da in decay_alerts:
                ts = datetime.now().strftime("%H:%M:%S")
                alerts.append(f"[{ts}] {str(da)}")
                alerts = alerts[-20:]

            # Leaderboard refresh (non-blocking — skips if interval not elapsed)
            threading.Thread(target=leaderboard.maybe_refresh, daemon=True).start()

            save_memory(trader.to_memory())
            write_json(trader, sniper, mr, contrarian, momentum, always_down,
                       last_second, start_time, alerts,
                       basket=basket, watchdog=watchdog, leaderboard=leaderboard)
            render(trader, sniper, mr, contrarian, momentum, always_down,
                   last_second, start_time, alerts,
                   watchdog=watchdog, leaderboard=leaderboard)
            await asyncio.sleep(poll)

    except KeyboardInterrupt:
        write_json(trader, sniper, mr, contrarian, momentum, always_down,
                   last_second, start_time, alerts,
                   basket=basket, watchdog=watchdog, leaderboard=leaderboard)
        print(f"\n{C.YL}Shutting down...{C.R}")
        for ct in trader.close_all(reason="shutdown"):
            from utils.colors import pnlc
            print(f"  Closed {ct.market_title[:45]}  ${ct.realized_pnl:+.2f}")
        save_memory(trader.to_memory())

        print(f"\n{'─'*62}")
        all_strats = [
            ("Copy Trader",    trader),
            ("MR",             mr),
            ("Sniper",         sniper),
            ("Contrarian",     contrarian),
            ("Momentum",       momentum),
            ("Always DOWN",    always_down),
            ("Last-Second",    last_second),
        ]
        for name, strat in all_strats:
            s   = strat.summary()
            pnl = s.get('realized', s.get('pnl', 0))
            wr  = s.get('win_rate', 0)
            n   = s.get('n_closed', 0)
            print(f"  {name:<14} P&L: ${pnl:+.2f}  WR: {wr:.0%}  Trades: {n}")
        print(f"{'─'*62}\n{green('Session saved.')}")


if __name__ == "__main__":
    asyncio.run(main())
