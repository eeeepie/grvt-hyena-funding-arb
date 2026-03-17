

# Funding Rate Arbitrage

Delta-neutral funding rate arbitrage across HyENA and GRVT perpetual futures.

## Supported Assets

| Asset | Direction | Funding Spread | Net Yield (est.) |
|---|---|---|---|
| **BTC** | Long HyENA (`hyna:BTC`) + Short GRVT (`BTC_USDT_Perp`) | ~+1.3% ann | ~12.4% ann |
| **HYPE** | Short HyENA (`hyna:HYPE`) + Long GRVT (`HYPE_USDT_Perp`) | ~+7.1% ann | ~17.6% ann + 5x GRVT points |
| **SOL** | Short HyENA (`hyna:SOL`) + Long GRVT (`SOL_USDT_Perp`) | ~+8.5% ann | ~19.0% ann + 5x GRVT points |

Core alpha = stacking margin rewards (USDe 12% + GRVT equity 10%), not the funding spread itself.

## Components

| File | Description |
|---|---|
| `main.py` | CLI entry point (`--asset BTC\|HYPE\|SOL` + status / rates / entry / exit / monitor) |
| `config.py` | Settings, `AssetConfig` (BTC_CONFIG, HYPE_CONFIG, SOL_CONFIG), reads `.env` |
| `exchange_clients.py` | HyENA + GRVT API wrappers + trading |
| `strategy_engine.py` | Entry / exit / mirror close / rebalance logic |
| `ws_feed.py` | HyENA WebSocket feed for sub-second mirror-close detection |
| `monitor.py` | Funding logging, alerts, circuit breaker |
| `mtm.py` | Mark-to-market PnL report (`--asset BTC\|HYPE\|SOL`) |
| `btc_funding_compare_v3.py` | Standalone BTC multi-exchange rate comparison (read-only) |
| `funding_backtest.py` | Multi-asset funding rate backtest with real historical data (read-only) |
| `research.md` | HYPE strategy research with 80-day backtest results |

## Quick Start

```bash
# Dependencies
pip3 install python-dotenv requests hyperliquid-python-sdk eth-account

# Read-only tools (no keys needed)
pip install requests pandas tabulate
python3 btc_funding_compare_v3.py       # BTC rate comparison
python3 funding_backtest.py              # HYPE backtest (default)
python3 funding_backtest.py --asset BTC # BTC backtest
python3 funding_backtest.py --asset SOL # any listed pair

# Trading system — BTC (default)
python3 main.py status    # connectivity + balances + max position
python3 main.py rates     # current funding rates & yield estimate
python3 main.py entry     # interactive: set USD amount & leverage, open positions
python3 main.py monitor   # re-attach to existing positions
python3 main.py exit      # close all positions + PnL attribution report
python3 mtm.py            # mark-to-market PnL snapshot (without closing)

# Trading system — HYPE
python3 main.py --asset HYPE status
python3 main.py --asset HYPE entry    # default 2x leverage (configurable at prompt)
python3 main.py --asset HYPE exit
python3 mtm.py --asset HYPE

# Trading system — SOL
python3 main.py --asset SOL status
python3 main.py --asset SOL entry     # default 2x leverage
python3 main.py --asset SOL exit
python3 mtm.py --asset SOL
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

1. **Entry**: Opens opposite positions on HyENA and GRVT simultaneously (IOC orders, delta-neutral). Leverage is set interactively (default: 3x BTC, 2x HYPE).
2. **Monitor**: Tracks funding rates, detects position anomalies, triggers mirror close on unexpected changes.
3. **Exit**: Closes both legs concurrently with retry logic (up to 9 attempts per leg).

### Yield Sources

| Source | Basis | Scales with leverage? |
|---|---|---|
| Funding spread | Notional | Yes (effective = spread x leverage / 2) |
| USDe 12% APR | min(USDe balance, notional) | No (capped at margin) |
| GRVT 10% APR | Account equity | No |

### Asset-Specific Notes

**BTC** — Long HyENA + Short GRVT
- Default leverage: 3x
- HyENA tick: $1, GRVT tick: $0.1, min size: 0.001 BTC

**HYPE** — Short HyENA + Long GRVT (collects the higher HyENA funding rate)
- Default leverage: 2x (isolated margin on HyENA, more volatile)
- HyENA tick: $0.001, GRVT tick: $0.001, min size: 1.0 HYPE (integer)
- GRVT funding interval: 4h (not 8h like BTC)
- GRVT 5x altcoin points multiplier
- Separate state file: `data/entry_state_HYPE.json`

**SOL** — Short HyENA + Long GRVT (collects spread, best alt liquidity)
- Default leverage: 2x
- HyENA tick: $0.01, GRVT tick: $0.01, min size: 0.1 SOL
- GRVT funding interval: 8h, cross margin on HyENA
- GRVT 5x altcoin points multiplier
- Deepest OI of any alt (~$30M on GRVT)
- Separate state file: `data/entry_state_SOL.json`

### Safety Features

- **Margin ratio (MMR) monitoring**: Per-leg maintenance margin ratio checked every 10s. Warning at 50%, emergency at 70%. Prevents liquidation on either exchange independently.
- **Mirror close**: Double-confirmation anomaly detection with automatic emergency close. WebSocket feed on HyENA provides sub-second detection (REST polling as fallback)
- **Entry retry**: Up to 3 attempts with fresh book query and escalating price offset
- **Exit retry**: Each `market_close` retries 3x internally; outer loop retries 2 more times
- **State preservation**: Entry snapshot persisted to disk; not wiped on partial failure
- **Leverage guard**: Pre-entry check against configurable max leverage

## License

Private / not for redistribution.
