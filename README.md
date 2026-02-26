# BTC Funding Rate Arbitrage

Delta-neutral funding rate arbitrage: **Long HyENA** (`hyna:BTC`) + **Short GRVT** (`BTC_USDT_Perp`).

Core alpha = stacking margin rewards (USDe 12% + GRVT equity 10%), not the funding spread itself.

## Components

| File | Description |
|---|---|
| `main.py` | CLI entry point (status / rates / entry / exit / monitor) |
| `config.py` | Settings & thresholds (reads `.env`) |
| `exchange_clients.py` | HyENA + GRVT API wrappers + trading |
| `strategy_engine.py` | Entry / exit / mirror close / rebalance logic |
| `monitor.py` | Funding logging, alerts, circuit breaker |
| `mtm.py` | Mark-to-market PnL report (standalone) |
| `btc_funding_compare_v3.py` | Standalone multi-exchange rate comparison (read-only) |

## Quick Start

```bash
# Dependencies
pip3 install python-dotenv requests hyperliquid-python-sdk eth-account

# Read-only monitoring (no keys needed)
pip install requests pandas tabulate
python3 btc_funding_compare_v3.py

# Trading system
python3 main.py status    # connectivity + balances + max position
python3 main.py rates     # current funding rates & yield estimate
python3 main.py entry     # interactive: set USD amount & leverage, open positions
python3 main.py monitor   # re-attach to existing positions
python3 main.py exit      # close all positions + PnL attribution report
python3 mtm.py            # mark-to-market PnL snapshot (without closing)
```

## Configuration

Copy `.env.example` to `.env` and fill in:

```
HYENA_PRIVATE_KEY=       # Hyperliquid wallet private key
GRVT_API_KEY=            # GRVT API key
GRVT_PRIVATE_KEY=        # GRVT EIP-712 signing key
GRVT_TRADING_ACCOUNT_ID= # GRVT sub-account ID
```

## How It Works

1. **Entry**: Opens a long on HyENA and a short on GRVT simultaneously (IOC orders, delta-neutral)
2. **Monitor**: Tracks funding rates, detects position anomalies, triggers mirror close on unexpected changes
3. **Exit**: Closes both legs concurrently with retry logic (up to 9 attempts per leg)

### Yield Sources

| Source | Basis | Scales with leverage? |
|---|---|---|
| Funding spread | Notional | Yes (effective = spread x leverage / 2) |
| USDe 12% APR | min(USDe balance, notional) | No (capped at margin) |
| GRVT 10% APR | Account equity | No |

### Safety Features

- **Mirror close**: Double-confirmation anomaly detection with automatic emergency close
- **Entry retry**: Up to 3 attempts with fresh book query and escalating price offset
- **Exit retry**: Each `market_close` retries 3x internally; outer loop retries 2 more times
- **State preservation**: Entry snapshot persisted to disk; not wiped on partial failure
- **Leverage guard**: Pre-entry check against configurable max leverage

## License

Private / not for redistribution.
what