# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

BTC perpetual funding rate arbitrage system: **Long HyENA** (`hyna:BTC`) + **Short GRVT** (`BTC_USDT_Perp`).

Core alpha = stacking margin rewards (USDe 12% + GRVT equity 10% = 22% base APR), not the funding spread itself.

Two components:
- `btc_funding_compare_v3.py` — read-only monitoring tool (standalone, no keys needed)
- `main.py` + modules — automated trading system (requires API keys)

## Running

```bash
# Monitoring only (public data, no keys)
pip install requests pandas tabulate
python3 btc_funding_compare_v3.py

# Trading system
pip3 install python-dotenv requests hyperliquid-python-sdk eth-account
python3 main.py status    # connectivity + balances + max position
python3 main.py rates     # current funding rates & yield estimate
python3 main.py entry     # interactive: set USD amount & leverage → open + monitor
python3 main.py monitor   # re-attach to existing positions
python3 main.py exit      # close all positions
```

## File Structure

```
main.py              ← CLI entry point (status/rates/entry/exit/monitor)
config.py            ← Settings & thresholds (reads .env)
exchange_clients.py  ← HyENA + GRVT API wrappers + trading
strategy_engine.py   ← Entry/exit/mirror close/rebalance logic
monitor.py           ← Funding logging, alerts, circuit breaker
btc_funding_compare_v3.py ← Standalone rate comparison (read-only)
```

## Key Architecture Details

### Funding Rate Normalization
All rates normalized to 8h basis:
- **HyENA**: API returns 1h rate → multiply by 8
- **GRVT**: API returns percentage (0.01 = 0.01%) → divide by 100 for decimal
- Annualization: `rate_8h_decimal * 3 * 365 * 100`

### HyENA vs Hyperliquid Native (Critical)
`hyna:BTC` and `BTC` are **separate markets**. HIP-3 assets do NOT appear in `metaAndAssetCtxs` — use `{"type": "meta", "dex": "hyna"}`.

### Tick Sizes
- **HyENA BTC**: tick = **$1** (whole dollars only), szDecimals = 5
- **GRVT BTC**: tick = **$0.1**, min_size = 0.001, min_notional = $100

### API Patterns
- **Hyperliquid**: POST to `https://api.hyperliquid.xyz/info` with JSON `{"type": ...}`
- **GRVT**: POST to `https://market-data.grvt.io/full/v1/...` with JSON body

### GRVT Trading API (Critical — Easy to Get Wrong)

**Dual-domain auth**: Login at `edge.grvt.io`, trade at `trades.grvt.io`. Extract the `gravity` cookie from the login response and pass it via `cookies=` param (NOT via `Cookie:` header).

**EIP-712 signed orders are REQUIRED.** Cookie auth alone returns 403 on `create_order`. Use `GRVT_PRIVATE_KEY` (the API key's associated private key) to sign.

**TimeInForce values for EIP-712 signing (official SDK):**
- GTT = **1**, AON = **2**, IOC = **3**, FOK = **4**

⚠️ Community gist (minhbsq/40842859) has IOC=2 which is **WRONG**.

**Decimal precision**: Use `Decimal(str(x))` not `float(x)` for signing.

**GRVT book API**: Requires `"depth": 10`. Other values return 400.

**GRVT leverage**: No API to set leverage — must be configured in GRVT frontend UI. Default can be up to 50x.

### HyENA Balance (Spot + Perps Combined)
Query both `clearinghouseState` (dex=hyna) AND `spotClearinghouseState` and sum for true balance.

### HyENA Order Error Detection
The SDK returns `{'status': 'ok'}` even on rejected orders. Must check nested `statuses` for `error` keys.

### Language
Output strings and strategy commentary are in Chinese (中文).