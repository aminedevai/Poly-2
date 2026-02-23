"""
risk/fee_calculator.py — Polymarket taker fee calculator.

Formula (Jan 2026 update):
    fee = 0.25 × (p × (1 - p))²

Max fee: ~1.56% at p = 0.50
Drops to ~0% at extremes (p → 0 or p → 1)

Makers pay ZERO fees and earn daily USDC rebates.
"""

def taker_fee(price: float) -> float:
    """
    Returns taker fee as a fraction of the trade notional.
    Example: price=0.50 → 0.015625 (1.56%)
             price=0.30 → 0.011025 (1.10%)
    """
    p = max(0.001, min(0.999, price))
    return 0.25 * (p * (1 - p)) ** 2

def taker_fee_usd(price: float, notional: float) -> float:
    """Fee in USD for a given notional amount."""
    return taker_fee(price) * notional

def is_fee_zone_ok(price: float, threshold: float = 0.02) -> bool:
    """
    Returns True if the fee is below the threshold.
    Default: only trade when fee < 2% (price < 0.20 or price > 0.80).
    """
    return taker_fee(price) < threshold

def net_profit(entry_price: float, exit_price: float,
               bet_size: float) -> float:
    """
    Estimate net profit after taker fees on both legs.
    Assumes taker execution on entry and exit.
    """
    shares      = bet_size / entry_price
    gross       = shares * exit_price - bet_size
    fee_entry   = taker_fee_usd(entry_price, bet_size)
    fee_exit    = taker_fee_usd(exit_price,  shares * exit_price)
    return gross - fee_entry - fee_exit
