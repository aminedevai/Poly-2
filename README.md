# Polymarket Bot v2

Paper trading bot for Polymarket — three strategies, one process.

## Structure

```
poly2/
├── main.py                   ← Entry point — run this
├── config.yaml               ← All parameters (edit here)
├── requirements.txt
│
├── core/
│   ├── api.py                ← All Polymarket API calls
│   ├── models.py             ← Shared data classes
│   ├── copy_trader.py        ← Mirrors target wallet at 50% scale
│   ├── collector.py          ← BTC 5-min market snapshots (every 8s)
│   └── dashboard.py          ← Terminal UI + dashboard_data.json
│
├── strategy/
│   ├── mean_reversion.py     ← Fade price extremes at 30s, hold to settle
│   └── sniper.py             ← Follow volume spikes >10x
│
├── utils/
│   ├── config.py             ← Loads config.yaml once, shared everywhere
│   ├── colors.py             ← Terminal color helpers
│   ├── time_helpers.py       ← Slug/timestamp utilities
│   └── logger.py             ← Logging setup
│
└── logs/                     ← Auto-created on first run
    ├── bot.log
    ├── live_market_data.csv
    ├── mr_trades.csv
    ├── sniper_trades.csv
    └── trader_memory.json
```

## Quick Start

```bash
pip install -r requirements.txt
python main.py
```

## Strategies

| Strategy       | Budget | Signal                              | Exit        |
|----------------|--------|-------------------------------------|-------------|
| Copy Trader    | $100   | Target wallet opens position        | Target closes |
| Mean Reversion | $100   | Price >5% from $0.50 at 30s mark   | Settlement  |
| Sniper         | $100   | Volume spike >10x + move >12%      | Settlement  |

## Key Config (config.yaml)

```yaml
copy_trader:
  target_wallet: "0x..."   # wallet to mirror
  scale: 0.5               # bet 50% of what they bet

mean_reversion:
  trigger_dist: 0.05       # deviation from $0.50 needed to fire

sniper:
  vol_ratio: 10.0          # volume spike multiplier
  min_move:  0.12          # minimum price move
```

## Manual Controls

While running, type a position number (0, 1, 2...) and press Enter to
manually close that copy-trader position.
