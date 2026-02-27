"""
strategy/base.py
================
Shared CSV-loading mixin used by all paper strategies.

Solves two bugs:
  1. Duplicate trades on restart: _fired set was in-memory only.
     Now we load all past slugs from CSV into _fired on __init__.
  2. History lost on restart: closed_trades list was empty after restart.
     Now we reload from CSV so dashboard always shows full history.
"""
import csv
from typing import List


def load_fired_from_csv(csv_path: str, slug_col: str = 'slug') -> set:
    """Return set of all slugs already written to CSV (prevents duplicate entries)."""
    try:
        with open(csv_path, encoding='utf-8') as f:
            return {row[slug_col] for row in csv.DictReader(f) if row.get(slug_col)}
    except FileNotFoundError:
        return set()


def load_closed_trades_from_csv(csv_path: str, builder) -> list:
    """
    Reload closed trades from CSV into memory so history survives restarts.
    builder(row) -> dict  — converts a CSV row dict into the summary dict format.
    """
    trades = []
    try:
        with open(csv_path, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                try:
                    trades.append(builder(row))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return trades
