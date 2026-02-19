
# Changelog

## 2026-02-20

### Entry robustness
- `open_position` retry up to 3 times on HyENA IOC rejection (hyna:BTC book can be momentarily empty)
- Re-query book + double offset on retry (5bps → 10bps)
- `_check_grvt_filled()`: verify GRVT order actually filled (PENDING + traded_size=0 != success)
- Default `aggressive_limit_offset_bps` raised from 2 → 5

### Exit robustness
- `close_position` checks results for exceptions (previously silently swallowed)
- Residual position auto-retry: up to 2 additional attempts per leg
- Returns `False` if positions remain (previously always returned `True`)
- `GrvtClient.market_close` retries 3x with fresh signature + escalating offset (0.5% → 1%)

### PnL attribution report
- Rewrote `print_exit_pnl()`: funding, position PnL, fees, slippage (bps + USD), NET PnL, external rewards (USDe/GRVT APR), annualized APR
- `_calc_vwap()` helper for multi-fill VWAP
- Exit mid price captured before close for slippage calculation
- GRVT funding estimated via balance-change method
- `config.py`: added `usde_reward_apr` (12%) and `grvt_reward_apr` (10%)

### New: `mtm.py`
- Mark-to-market PnL report without closing positions
- Auto-loads entry snapshot from `data/entry_state.json`
- Per-leg totals matching exchange frontend display

### Entry state persistence
- `data/entry_state.json` auto-saved on `open_position`, auto-deleted on `reset()`
- Enables cross-process PnL reporting (`mtm.py`, `main.py exit` from separate terminal)

---

## 2026-02-10

- Initial live test with small amounts (0.003 BTC)
- Confirmed both exchanges operational
- Discovered HyENA SDK silent rejection, GRVT leverage API absence, builder address format issues
