# Polymarket Sniper v2

Paper trading bot for Polymarket BTC 5-minute markets.

## Structure

```
polymarket-sniper-v2/
├── main.py                  ← Entry point (run this)
├── config.yaml              ← All parameters (edit here)
├── requirements.txt
│
├── core/
│   ├── api.py               ← All API calls (Gamma, Data API)
│   ├── models.py            ← Data classes (Position, Trade, etc.)
│   ├── copy_trader.py       ← Mirrors target wallet at 50% scale
│   ├── collector.py         ← BTC 5-min market snapshots
│   └── dashboard.py         ← Terminal UI + JSON export
│
├── strategy/
│   ├── mean_reversion.py    ← Fade price extremes at 30s, hold to settle
│   └── sniper.py            ← Follow volume spikes >10x
│
├── utils/
│   ├── config.py            ← Config loader (single source of truth)
│   ├── colors.py            ← Terminal colors
│   ├── time_helpers.py      ← Slug/timestamp utilities
│   └── logger.py            ← Logging setup
│
└── logs/                    ← Auto-created on first run
    ├── bot.log
    ├── live_market_data.csv
    ├── mr_trades.csv
    └── sniper_trades.csv
```

## Quick Start

```bash
pip install -r requirements.txt
python main.py
```

Dashboard (in a second terminal):
```bash
python -m http.server 8080
# open http://localhost:8080/dashboard.html
```

## Strategies

| Strategy | Budget | Signal | Exit |
|---|---|---|---|
| Copy Trader | $100 | Target wallet opens position | Target closes |
| Mean Reversion | $100 | Price >5% from $0.50 at 30s | Settlement ($1 or $0) |
| Sniper | $100 | Volume spike >10x + move >12% | Settlement |

## Config

All parameters in `config.yaml`. Key settings:

```yaml
copy_trader:
  target_wallet: "0x..."   # wallet to mirror
  scale: 0.5               # bet 50% of what they bet

mean_reversion:
  trigger_dist: 0.05       # min deviation from $0.50 to fire

sniper:
  vol_ratio: 10.0          # volume spike multiplier
  min_move:  0.12          # min price move
```
