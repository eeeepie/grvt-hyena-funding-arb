# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

BTC perpetual funding rate arbitrage system: **Long HyENA** (`hyna:BTC`) + **Short GRVT** (`BTC_USDT_Perp`).

Core alpha = stacking margin rewards (USDe 12% + GRVT equity 10% = 22% base APR), not the funding spread itself.

### Reward Basis
- **HyENA 12% APR**: basis = `min(USDe balance, long notional)` — leverage multiplies reward; require USDe balance ≥ notional or reward is capped at balance
- **GRVT 10% APR**: basis = account equity (margin only), leverage-agnostic; tiered caps at 1K / 20K / 100K USDT

Three components:
- `btc_funding_compare_v3.py` — read-only monitoring tool (standalone, no keys needed)
- `main.py` + modules — automated trading system (requires API keys)
- `mtm.py` — mark-to-market PnL report (reads `data/entry_state.json`)

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
python3 main.py exit      # close all positions + PnL attribution report
python3 mtm.py            # mark-to-market PnL snapshot (不平仓)
```

## File Structure

```
main.py              ← CLI entry point (status/rates/entry/exit/monitor)
config.py            ← Settings & thresholds (reads .env)
exchange_clients.py  ← HyENA + GRVT API wrappers + trading
strategy_engine.py   ← Entry/exit/mirror close/rebalance logic
monitor.py           ← Funding logging, alerts, circuit breaker
mtm.py               ← Mark-to-market PnL report (standalone)
btc_funding_compare_v3.py ← Standalone rate comparison (read-only)
data/entry_state.json ← Entry snapshot (auto-created on open, auto-deleted on close)
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

**GRVT leverage**: API exists — `POST /full/v1/set_initial_leverage` and `GET /full/v1/get_all_initial_leverage`. Can also be set in GRVT frontend UI.

**GRVT funding payments**: `POST /full/v1/funding_payment_history` returns per-settlement funding amounts. Use `start_time` in **nanoseconds**. Response: `result[].amount` (positive = received).

### HyENA Balance (Spot + Perps Combined)
Query both `clearinghouseState` (dex=hyna) AND `spotClearinghouseState` and sum for true balance.

### HyENA Order Error Detection
The SDK returns `{'status': 'ok'}` even on rejected orders. Must check nested `statuses` for `error` keys.

### GRVT Order Fill Detection
GRVT `create_order` can return `status: PENDING` with `traded_size: ['0.0']` — this means NOT filled. Must check `traded_size > 0` or `status == FILLED` before treating as success.

### Entry Retry (hyna:BTC Thin Liquidity)
hyna:BTC order book can be momentarily empty. `open_position` retries up to 3 times with 2s delay and fresh book query. Offset doubles on retry (5bps → 10bps). If GRVT was also not filled, both are retried together.

### Exit Retry
`close_position` verifies residual positions after close and retries each failed leg up to 2 more times. `GrvtClient.market_close` itself retries 3 times with fresh signature (avoids stale nonce issue). Returns `False` if positions remain.

### Entry State Persistence
`open_position` saves entry snapshot to `data/entry_state.json`. This enables:
- `mtm.py` to generate PnL reports without manual input
- `main.py exit` (cross-process) to load entry data for accurate PnL attribution

### PnL Attribution Report
`print_exit_pnl()` outputs: funding income, position PnL, trading fees, slippage (bps + USD), NET PnL, external reward estimates (USDe + GRVT APR), and annualized APR. GRVT funding uses `funding_payment_history` API for precise values.

### Language
All script output is in English.