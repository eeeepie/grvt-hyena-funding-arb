# BTC Funding Rate Arbitrage System

Delta-neutral funding rate arb: **Long HyENA** (`hyna:BTC`) + **Short GRVT** (`BTC_USDT_Perp`).

Core alpha = stacking margin rewards (USDe 12% + GRVT 10% = **22% APR base**), not the funding spread itself.

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
| `python3 main.py exit` | Close both legs simultaneously |

### Entry Flow

```
── 开仓参数 ──

  ⚠  请先在 GRVT 前端确认杠杆倍数!
     grvt.io → BTC_USDT_Perp → 调整杠杆至目标倍数

  每腿金额 USD [100.0]: 200
  最大杠杆倍数 [3.0x]: 2

  → 每腿 $200 | 最大杠杆 2.0x
  确认开仓? [y/N]: y
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

### Pre-flight Checklist

Before first entry:
- [ ] Both exchanges say "Connected" in `status`
- [ ] Both balances show sufficient funds
- [ ] **GRVT leverage set to 2–3x in frontend UI** (grvt.io → BTC_USDT_Perp)
- [ ] USDe is pegged (~$1.00)

---

## What the Monitor Does (24/7)

| Task | Interval | Purpose |
|------|----------|---------|
| Position poll | 10s | Mirror close: if one leg gets ADL'd, auto-close the other |
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
btc_funding_compare_v3.py ← Standalone rate comparison (read-only, no keys)
```

---

## Security Notes

- **`.env` is git-ignored** — keys never committed
- **To revoke access**: delete `.env`, or revoke GRVT API key in their UI
- **Wallet keys**: if compromised, move funds immediately via exchange UI