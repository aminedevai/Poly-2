"""
backtest/run_backtest.py
========================
CLI launcher for running backtests.

Usage examples:

  # Backtest Mean Reversion on last 7 days (auto-fetches data)
  python -m backtest.run_backtest --strategy mean_reversion --days 7

  # Backtest on a specific date range
  python -m backtest.run_backtest --strategy mean_reversion --from 2025-02-01 --to 2025-02-23

  # Use already-fetched data file
  python -m backtest.run_backtest --strategy mean_reversion --file backtest/data/markets_20250216_20250223.json

  # Run all strategies and compare
  python -m backtest.run_backtest --strategy all --days 7

  # Tune trigger parameter
  python -m backtest.run_backtest --strategy mean_reversion --days 30 --trigger 0.03

  # Save results to JSON
  python -m backtest.run_backtest --strategy mean_reversion --days 7 --save
"""
import argparse, json, os, sys, time
from datetime import datetime, timezone, timedelta

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.fetch_data import fetch_range, save, load, list_saved, DATA_DIR
from backtest.engine     import run
from backtest.strategies import STRATEGIES, MeanReversionBacktest

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def print_result(res):
    """Print a backtest result in a clean terminal format."""
    W  = 70
    OK = "\033[92m✓\033[0m"
    NO = "\033[91m✗\033[0m"

    print(f"\n{'─'*W}")
    print(f"  BACKTEST RESULTS — {res.strategy_name}")
    print(f"  {res.date_from}  →  {res.date_to}")
    print(f"{'─'*W}")
    print(f"  Markets in range : {res.n_markets:,}")
    print(f"  Signals fired    : {res.n_signals:,}")
    print(f"  Wins / Losses    : {res.n_wins} / {res.n_losses}")
    wr = res.win_rate
    wr_s = (f"\033[92m{wr:.1%}\033[0m" if wr >= 0.65
            else f"\033[91m{wr:.1%}\033[0m" if wr < 0.5
            else f"\033[93m{wr:.1%}\033[0m")
    print(f"  Win rate         : {wr_s}")
    pnl_c = "\033[92m" if res.total_pnl >= 0 else "\033[91m"
    print(f"  Total P&L        : {pnl_c}${res.total_pnl:+.2f}\033[0m")
    print(f"  Starting capital : ${res.starting_capital:.2f}")
    print(f"  Final capital    : ${res.final_capital:.2f}")
    roi_c = "\033[92m" if res.roi_pct >= 0 else "\033[91m"
    print(f"  ROI              : {roi_c}{res.roi_pct:+.1f}%\033[0m")
    print(f"  Avg profit/trade : ${res.avg_profit:+.2f}")
    print(f"  Max drawdown     : {res.max_drawdown:.1f}%")
    print(f"{'─'*W}")

    if res.trades:
        print(f"\n  Last 10 trades:")
        print(f"  {'Date':>12}  {'Dir':>4}  {'Entry':>6}  {'Out':>4}  {'P&L':>8}  URL")
        print(f"  {'─'*10}  {'─'*4}  {'─'*6}  {'─'*4}  {'─'*8}")
        for t in res.trades[-10:]:
            dt  = t.open_dt[:10]
            dc  = "\033[92m" if t.direction == "UP" else "\033[91m"
            oc  = "\033[92m" if t.won else "\033[91m"
            pc  = "\033[92m" if t.profit >= 0 else "\033[91m"
            print(f"  {dt:>12}  {dc}{t.direction:>4}\033[0m  "
                  f"{t.entry_price:.4f}  "
                  f"{oc}{t.outcome:>4}\033[0m  "
                  f"{pc}${t.profit:>+7.2f}\033[0m  "
                  f"\033[94m{t.url}\033[0m")
    print()


def save_result(res, label: str):
    """Save result dict to JSON file."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    fname = f"result_{res.strategy_name.lower().replace(' ', '_')}_{label}.json"
    path  = os.path.join(RESULTS_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res.to_dict(), f, indent=2)
    print(f"  Results saved → {path}")
    return path


def get_or_fetch_markets(args) -> list:
    """Get market data from file or by fetching."""
    if args.file:
        print(f"  Loading data from {args.file}")
        return load(args.file)

    now = datetime.now(timezone.utc)
    if args.date_from:
        start = datetime.fromisoformat(args.date_from).replace(tzinfo=timezone.utc)
        end   = (datetime.fromisoformat(args.date_to).replace(tzinfo=timezone.utc)
                 if args.date_to else now)
    else:
        end   = now
        start = now - timedelta(days=args.days)

    # Check if we already have this data
    label = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    cached = os.path.join(DATA_DIR, f"markets_{label}.json")
    if os.path.exists(cached) and not args.refetch:
        print(f"  Using cached data: {cached}")
        return load(cached)

    markets = fetch_range(start, end)
    save(markets, label)
    return markets


def main():
    parser = argparse.ArgumentParser(description="Run a strategy backtest")
    parser.add_argument("--strategy", default="mean_reversion",
                        choices=list(STRATEGIES.keys()) + ["all"],
                        help="Strategy to backtest")
    parser.add_argument("--days",      type=int,   default=7,
                        help="Days of history (default: 7)")
    parser.add_argument("--from",      dest="date_from",
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--to",        dest="date_to",
                        help="End date YYYY-MM-DD")
    parser.add_argument("--file",
                        help="Path to existing JSON data file")
    parser.add_argument("--capital",   type=float, default=100.0,
                        help="Starting capital (default: 100)")
    parser.add_argument("--bet",       type=float, default=10.0,
                        help="Bet size per trade (default: 10)")
    parser.add_argument("--trigger",   type=float, default=0.05,
                        help="MR trigger distance from 0.50 (default: 0.05)")
    parser.add_argument("--min-vol",   type=float, default=100.0,
                        help="Minimum market volume filter (default: 100)")
    parser.add_argument("--save",      action="store_true",
                        help="Save results to JSON file")
    parser.add_argument("--refetch",   action="store_true",
                        help="Re-fetch data even if cache exists")
    args = parser.parse_args()

    print(f"\n  Polymarket Backtest Engine")
    print(f"  {'─'*40}")

    markets = get_or_fetch_markets(args)
    if not markets:
        print("  No market data available. Try --refetch or check your network.")
        return

    print(f"  Loaded {len(markets)} markets")

    # Date label for saving results
    now   = datetime.now(timezone.utc)
    end   = now
    start = now - timedelta(days=args.days)
    label = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"

    strategies_to_run = (
        list(STRATEGIES.keys()) if args.strategy == "all"
        else [args.strategy]
    )

    all_results = []
    for name in strategies_to_run:
        cls  = STRATEGIES[name]
        # Pass relevant kwargs per strategy
        if name == "mean_reversion":
            strat = cls(trigger_dist=args.trigger, bet_size=args.bet,
                        min_volume=args.min_vol)
        else:
            strat = cls(bet_size=args.bet, min_volume=args.min_vol)

        res = run(markets, strat, starting_capital=args.capital)
        print_result(res)
        all_results.append(res)

        if args.save:
            save_result(res, label)

    # Comparison summary when running all
    if len(all_results) > 1:
        print(f"\n  {'─'*70}")
        print(f"  COMPARISON SUMMARY")
        print(f"  {'─'*70}")
        print(f"  {'Strategy':<35} {'Signals':>8} {'Win%':>6} {'P&L':>9} {'ROI':>7}")
        print(f"  {'─'*35} {'─'*8} {'─'*6} {'─'*9} {'─'*7}")
        for r in all_results:
            wr_c = "\033[92m" if r.win_rate >= 0.65 else "\033[91m"
            pc   = "\033[92m" if r.total_pnl >= 0 else "\033[91m"
            print(
                f"  {r.strategy_name:<35} {r.n_signals:>8} "
                f"{wr_c}{r.win_rate:.0%}\033[0m   "
                f"{pc}${r.total_pnl:>+8.2f}\033[0m "
                f"{r.roi_pct:>+6.1f}%"
            )
        print()


if __name__ == "__main__":
    main()
