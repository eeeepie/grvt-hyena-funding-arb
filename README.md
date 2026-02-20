# Grvt < > HyENA BTC Funding Rate Arbitrage System

Delta-neutral funding rate arb: **Long HyENA** (`hyna:BTC`) + **Short GRVT** (`BTC_USDT_Perp`).

Core alpha = stacking margin rewards (USDe 12% + GRVT 10% = **22% APR base**), not the funding spread itself.

| | Reward Basis | Note |
|--|-------------|------|
| **HyENA 12% APR** | `min(USDe balance, long notional)` | Leverage multiplies reward; keep USDe balance ≥ notional |
| **GRVT 10% APR** | Account equity (margin only) | Leverage-agnostic; tiered caps at 1K / 20K / 100K USDT |

> Full strategy spec & backtest: `PRD_Cross_Exchange_Funding_Rate_Arbitrage_v3.md`

---

## Quick Start

```bash
pip3 install python-dotenv requests hyperliquid-python-sdk eth-account
cp .env.example .env && nano .env   # fill in your keys
python3 main.py status              # verify connectivity
python3 main.py rates               # check market conditions
python3 main.py entry               # open positions (interactive)
```

---

## Commands

| Command | Purpose |
|---------|---------|
| `python3 main.py status` | Connectivity, balances, positions, max position size |
| `python3 main.py rates` | Current funding rates, spread, yield estimate |
| `python3 main.py entry` | Interactive entry: set USD amount & max leverage → open + monitor |
| `python3 main.py monitor` | Re-attach monitoring to existing positions (Ctrl+C to stop) |
| `python3 main.py exit` | Close both legs simultaneously + PnL attribution report |
| `python3 mtm.py` | Mark-to-market PnL snapshot |

### Entry Flow

```
── Entry Parameters ──

  USD per leg [100.0]: 200
  Max leverage [3.0x]: 2

  GRVT leverage: 3x OK

  -> $200 per leg | max leverage 2.0x
  Confirm entry? [y/N]: y
```

BTC quantity is auto-calculated from USD amount and live price.

---

## Setup Credentials

You need 4 credentials in `.env`:

| Credential | Source |
|------------|--------|
| `HYENA_PRIVATE_KEY` | MetaMask → Account Details → Show Private Key |
| `GRVT_API_KEY` | grvt.io → Settings → API Management → Create API Key |
| `GRVT_PRIVATE_KEY` | Private key of the wallet connected to GRVT |
| `GRVT_TRADING_ACCOUNT_ID` | Shown in GRVT dashboard/settings |

> `.env` is git-ignored — your keys never leave your machine.


---

## What the Monitor Does (24/7)

| Task | Interval | Purpose |
|------|----------|---------|
| Position poll | 10s | **Mirror close: if one leg gets ADL'd, auto-close the other** |
| Funding rates | 5min | Log to CSV, alert if spread stays negative 6h+ |
| USDe peg | 1min | Alert at 0.5% depeg, emergency at 1% |
| Circuit breaker | 1min | Pause if BTC moves >15% in 1h |
| Rebalance check | 5min | Warn if leverage exceeds limit on either leg |

### Run in Background

```bash
nohup python3 main.py monitor > logs/stdout.log 2>&1 &
echo $! > logs/monitor.pid

# Stop
kill $(cat logs/monitor.pid)
```

---

## Daily Operations

```bash
python3 main.py status               # positions + rates
python3 main.py rates                 # quick rate check
python3 mtm.py                        # mark-to-market PnL report
tail -30 logs/alerts.log              # recent alerts
```

---

## File Structure

```
main.py              ← CLI entry point
config.py            ← Settings & thresholds (reads .env)
exchange_clients.py  ← HyENA + GRVT API wrappers + trading
strategy_engine.py   ← Entry/exit/mirror close/rebalance
monitor.py           ← Funding logging, alerts, circuit breaker
mtm.py               ← Mark-to-market PnL report (standalone)
btc_funding_compare_v3.py ← Standalone rate comparison (read-only, no keys)
data/entry_state.json ← Entry snapshot (auto-managed)
```

---

## Account Setup

If you don't have exchange accounts yet, signing up through these links helps support the project:

- **GRVT**: [https://grvt.io/?ref=eeeeepie](https://grvt.io/?ref=eeeeepie)
- **HyENA / Hyperliquid**: Sign up at [app.hyena.trade](https://app.hyena.trade)

> **GRVT leverage**: Must be set manually in the GRVT frontend (API is deprecated). Go to grvt.io -> BTC_USDT_Perp -> adjust leverage to your target before running `entry`.

---

## Fees & Transparency

This script includes a **1 bps (0.01%) Hyperliquid builder fee** on HyENA trades. This is how the project sustains itself — a tiny fraction of each trade goes to the developer via Hyperliquid's native [builder mechanism](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/builder-codes).

The builder address and fee are visible in `config.py` — nothing is hidden.

---

## Security Notes

- **`.env` is git-ignored** — keys never committed
- **To revoke access**: delete `.env`, or revoke GRVT API key in their UI
- **Wallet keys**: if compromised, move funds immediately via exchange UI

---

## Support This Project

Building and maintaining a production-grade arbitrage system takes effort, especially with Grvt's suck API.

This project is open source and will continue to be actively maintained. If it helps you earn yield, consider supporting its development:

- **Use the referral links above** (https://grvt.io/?ref=eeeeepie) when creating your accounts
- **Star this repo** to help others find it
- **Report issues** — bug reports and feature requests keep the project improving

Thank you for your support. Great supporters get dev's feet pics. (wink wink;^)