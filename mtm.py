#!/usr/bin/env python3
"""Mark-to-market PnL report — shows current P&L without closing positions."""
import argparse
import asyncio
import time
import logging
from config import load_config
from exchange_clients import HyenaClient, GrvtClient
from strategy_engine import StrategyEngine
from monitor import Alerter

logging.basicConfig(level=logging.WARNING)


async def main():
    parser = argparse.ArgumentParser(description="Mark-to-Market PnL Report")
    parser.add_argument("--asset", default="BTC", choices=["BTC", "HYPE", "SOL"])
    args = parser.parse_args()

    exchange_cfg, strategy_cfg, monitor_cfg, asset_cfg = load_config(args.asset)
    alerter = Alerter(monitor_cfg.log_dir)
    hyena = HyenaClient(exchange_cfg, asset_cfg)
    grvt = GrvtClient(exchange_cfg, asset_cfg)
    strategy = StrategyEngine(hyena, grvt, strategy_cfg, monitor_cfg, alerter, asset_cfg)

    # Load current positions
    h_pos = hyena.get_position()
    g_pos = grvt.get_position()
    h_sz = h_pos.get("size", 0)
    g_sz = g_pos.get("size", 0)

    flat = asset_cfg.flat_threshold
    if abs(h_sz) < flat and abs(g_sz) < flat:
        print("No positions found, cannot generate report")
        return

    # Current books + balances
    h_book = hyena.get_book()
    g_book = grvt.get_book()
    h_mid = h_book["mid"]
    g_mid = g_book["mid"]
    h_bal = hyena.get_balance()
    g_bal = grvt.get_balance()

    h_entry_px = h_pos.get("entry_px", 0)
    g_entry_px = g_pos.get("entry_px", 0)
    h_size = abs(h_sz)
    g_size = abs(g_sz)

    # ── Load entry snapshot from state file ──
    has_snapshot = strategy.state.load_entry_state()

    if has_snapshot:
        entry_h_bal = strategy.state.entry_h_balance
        entry_g_bal = strategy.state.entry_g_balance
    else:
        # No state file → use current balance as fallback (NAV/APR will be approximate)
        entry_h_bal = h_bal.get("account_value", 0) if isinstance(h_bal, dict) else 0
        entry_g_bal = g_bal.get("total_equity", 0) if isinstance(g_bal, dict) else 0
        # Use position entry_px as mid approximation
        strategy.state.entry_h_mid = h_entry_px
        strategy.state.entry_g_mid = g_entry_px
        print("  (entry_state.json not found, some values are approximate)\n")

    # Infer entry time: state file > 0, else unknown
    if strategy.state.entry_time > 0:
        entry_time = strategy.state.entry_time
    else:
        print("  Cannot determine entry time, cannot generate report")
        return

    hold_secs = time.time() - entry_time
    holding_days = hold_secs / 86400
    entry_ms = int(entry_time * 1000) - 5000

    # ── Per-leg PnL from exchange (authoritative) ──
    h_unrealized = h_pos.get("unrealized_pnl", 0)
    g_unrealized = g_pos.get("unrealized_pnl", 0)

    # Direction (needed for funding sign correction)
    hyena_is_buy = asset_cfg.hyena_is_buy
    grvt_is_buy = not hyena_is_buy

    # ── Funding ──
    h_funding = 0.0
    if h_size > 0:
        try:
            # Pass signed size: positive = long, negative = short
            signed_h_size = h_size if hyena_is_buy else -h_size
            h_funding = hyena.get_accumulated_funding(entry_ms, signed_h_size)
        except Exception as e:
            print(f"  (HyENA funding query failed: {e})")

    # GRVT: use funding_payment_history API (precise)
    # GRVT amounts are from long perspective — pass actual direction
    g_funding = 0.0
    try:
        start_ns = int(entry_time * 1e9)
        g_funding = grvt.get_funding_payments(start_ns, is_buy=grvt_is_buy)
    except Exception as e:
        print(f"  (GRVT funding query failed: {e})")
    funding_total = h_funding + g_funding

    # Position PnL from price (direction-aware)
    g_bal_now = g_bal.get("total_equity", 0) if isinstance(g_bal, dict) else 0
    if hyena_is_buy:
        h_pos_pnl = (h_mid - h_entry_px) * h_size if h_entry_px else 0
    else:
        h_pos_pnl = (h_entry_px - h_mid) * h_size if h_entry_px else 0
    if grvt_is_buy:
        g_pos_pnl = (g_mid - g_entry_px) * g_size if g_entry_px else 0
    else:
        g_pos_pnl = (g_entry_px - g_mid) * g_size if g_entry_px else 0
    pos_total = h_pos_pnl + g_pos_pnl

    # ── Fees from entry fills ──
    h_fills = []
    try:
        h_fills = hyena.get_fills(entry_ms)
    except Exception:
        pass
    g_fills = []
    try:
        g_fills = grvt.get_fills(limit=50)
    except Exception:
        pass

    if hyena_is_buy:
        h_entry_fills = [f for f in h_fills if f.get("dir") == "Open Long" or f.get("side") == "B"]
    else:
        h_entry_fills = [f for f in h_fills if f.get("dir") == "Open Short" or f.get("side") == "A"]
    # Filter GRVT fills: entry side + after entry time
    def _grvt_time_secs(t):
        """Normalize GRVT timestamp (could be ns/ms/s) to seconds."""
        t = int(str(t))
        if t > 1e15: return t / 1e9   # nanoseconds
        if t > 1e12: return t / 1e3   # milliseconds
        return t                       # seconds

    if grvt_is_buy:
        g_entry_fills = [f for f in g_fills
                         if f.get("is_buyer")
                         and _grvt_time_secs(f.get("time", 0)) >= entry_time - 60]
    else:
        g_entry_fills = [f for f in g_fills
                         if not f.get("is_buyer")
                         and _grvt_time_secs(f.get("time", 0)) >= entry_time - 60]
    h_entry_fee = sum(f["fee"] for f in h_entry_fills)
    g_entry_fee = sum(f["fee"] for f in g_entry_fills)
    fee_total = h_entry_fee + g_entry_fee

    # ── Net PnL ──
    net_pnl = funding_total + pos_total - fee_total

    # ── Per-leg totals (matches frontend) ──
    h_bal_now = h_bal.get("account_value", 0) if isinstance(h_bal, dict) else 0
    h_leg_total = h_bal_now - entry_h_bal
    g_leg_total = g_bal_now - entry_g_bal

    # ── Rewards ──
    h_notional = h_size * h_entry_px
    g_notional = g_size * g_entry_px
    # HyENA: eligible = min(actual USDe balance, notional)
    h_usde = h_bal.get("spot_usde", h_bal.get("account_value", 0)) if isinstance(h_bal, dict) else 0
    h_eligible = min(h_usde, h_notional)
    # GRVT: eligible = initial_margin (account equity, leverage-agnostic)
    g_margin = (g_bal_now - g_bal.get("available_balance", 0)) if isinstance(g_bal, dict) else g_bal_now
    g_eligible = g_bal.get("initial_margin", g_margin) if isinstance(g_bal, dict) else g_margin
    usde_reward = h_eligible * (strategy_cfg.usde_reward_apr / 100) * (holding_days / 365)
    grvt_reward = g_eligible * (strategy_cfg.grvt_reward_apr / 100) * (holding_days / 365)
    reward_total = usde_reward + grvt_reward

    # ── NAV ──
    nav_before = entry_h_bal + entry_g_bal
    nav_now = h_bal_now + g_bal_now

    # ── APR ──
    trade_apr = (net_pnl / nav_before) * (365 / holding_days) * 100 if nav_before > 0 and holding_days > 0 else 0
    total_apr = ((net_pnl + reward_total) / nav_before) * (365 / holding_days) * 100 if nav_before > 0 and holding_days > 0 else 0

    # ── Leverage (margin used, not total equity) ──
    h_perps = h_bal.get("perps_margin", h_bal_now) if isinstance(h_bal, dict) else h_bal_now
    h_lev = h_notional / h_perps if h_perps > 0 else 0
    g_margin = (g_bal_now - g_bal.get("available_balance", 0)) if isinstance(g_bal, dict) else g_bal_now
    g_lev = g_notional / g_margin if g_margin > 0 else 0

    # ── Attribution % ──
    abs_sum = abs(funding_total) + abs(pos_total) + fee_total
    def _pct(val):
        return val / abs_sum * 100 if abs_sum > 0 else 0

    avg_size = (h_size + g_size) / 2

    # ── Print ──
    W = 54
    days_i = int(holding_days)
    hours = int((hold_secs % 86400) // 3600)
    mins = int((hold_secs % 3600) // 60)
    hold_str = f"{days_i}d {hours}h {mins}m" if days_i else f"{hours}h {mins}m"

    lines = []
    lines.append(f"\n{'═' * W}")
    lines.append(f"  P&L Report (Mark-to-Market)")
    lines.append(f"{'═' * W}")
    lines.append(f"\n  Holding: {hold_str}")
    h_dir = "Long" if hyena_is_buy else "Short"
    g_dir = "Long" if grvt_is_buy else "Short"
    lines.append(f"  Position: {h_dir} {asset_cfg.hyena_coin} / {g_dir} {asset_cfg.grvt_instrument}")
    lines.append(f"  Size: {avg_size:.5f} {asset_cfg.name} (approx ${avg_size * h_entry_px:,.0f}/leg)")
    lines.append(f"  Leverage: HyENA {h_lev:.1f}x / GRVT {g_lev:.1f}x")

    # Per-leg totals (matches frontend display)
    lines.append(f"\n  -- Per-Leg Total P&L (matches frontend) --")
    lines.append(f"    HyENA:  {h_leg_total:+.2f} USD  (unrealized: {h_unrealized:+.2f})")
    lines.append(f"    GRVT:   {g_leg_total:+.2f} USDT (unrealized: {g_unrealized:+.2f})")

    lines.append(f"\n  -- P&L Attribution --")
    lines.append(f"    Funding Income:          {funding_total:+10.2f}     ({_pct(funding_total):+5.1f}%)")
    lines.append(f"      HyENA ({h_dir.lower()}):              {h_funding:+.2f}")
    lines.append(f"      GRVT ({g_dir.lower()}):              {g_funding:+.2f}")

    lines.append(f"    Position PnL:            {pos_total:+10.2f}     ({_pct(pos_total):+5.1f}%)")
    lines.append(f"      HyENA: ${h_entry_px:,.0f} → ${h_mid:,.0f}    {h_pos_pnl:+.2f}")
    lines.append(f"      GRVT:  ${g_entry_px:,.1f} → ${g_mid:,.1f}    {g_pos_pnl:+.2f}")

    lines.append(f"    Trading Fees:            {-fee_total:+10.2f}     ({_pct(-fee_total):+5.1f}%)")
    lines.append(f"      Entry: H ${h_entry_fee:.2f} + G ${g_entry_fee:.2f}")
    lines.append(f"      (Exit fees will be included after closing)")

    lines.append(f"    {'─' * 37}")
    lines.append(f"    Actual NET PnL:         {net_pnl:+10.2f}")

    lines.append(f"\n  -- External Rewards (estimated) --")
    lines.append(f"    USDe staking ({strategy_cfg.usde_reward_apr:.0f}% APR):  {usde_reward:+.2f}")
    lines.append(f"    GRVT equity ({strategy_cfg.grvt_reward_apr:.0f}% APR):   {grvt_reward:+.2f}")
    lines.append(f"    Reward Total:            {reward_total:+.2f}")

    if nav_before > 0:
        nav_change = nav_now - nav_before
        nav_pct = nav_change / nav_before * 100
        lines.append(f"\n  -- Annualized Returns --")
        lines.append(f"    NAV: ${nav_before:,.2f} → ${nav_now:,.2f}  ({nav_pct:+.3f}%)")
        lines.append(f"    APR (trading only):  {trade_apr:+.1f}%")
        lines.append(f"    APR (w/ rewards):    {total_apr:+.1f}%")

    lines.append(f"{'═' * W}\n")
    print("\n".join(lines), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
