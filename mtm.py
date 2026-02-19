#!/usr/bin/env python3
"""Mark-to-market PnL report — shows current P&L without closing positions."""
import asyncio
import time
import logging
from config import load_config
from exchange_clients import HyenaClient, GrvtClient
from strategy_engine import StrategyEngine
from monitor import Alerter

logging.basicConfig(level=logging.WARNING)


async def main():
    exchange_cfg, strategy_cfg, monitor_cfg = load_config()
    alerter = Alerter(monitor_cfg.log_dir)
    hyena = HyenaClient(exchange_cfg)
    grvt = GrvtClient(exchange_cfg)
    strategy = StrategyEngine(hyena, grvt, strategy_cfg, monitor_cfg, alerter)

    # Load current positions
    h_pos = hyena.get_position()
    g_pos = grvt.get_position()
    h_sz = h_pos.get("size", 0)
    g_sz = g_pos.get("size", 0)

    if abs(h_sz) < 0.001 and abs(g_sz) < 0.001:
        print("无持仓，无法生成报告")
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
        print("  (未找到 entry_state.json，部分数据为近似值)\n")

    # Infer entry time: state file > 0, else unknown
    if strategy.state.entry_time > 0:
        entry_time = strategy.state.entry_time
    else:
        print("  无法确定开仓时间，无法生成报告")
        return

    hold_secs = time.time() - entry_time
    holding_days = hold_secs / 86400
    entry_ms = int(entry_time * 1000) - 5000

    # ── Per-leg PnL from exchange (authoritative) ──
    h_unrealized = h_pos.get("unrealized_pnl", 0)
    g_unrealized = g_pos.get("unrealized_pnl", 0)

    # ── Funding ──
    h_funding = 0.0
    if h_size > 0:
        try:
            h_funding = hyena.get_accumulated_funding(entry_ms, h_size)
        except Exception as e:
            print(f"  (HyENA funding查询失败: {e})")

    # GRVT: derive funding from balance change
    # total_leg_pnl = balance_now - balance_entry (includes funding + unrealized)
    g_bal_now = g_bal.get("total_equity", 0) if isinstance(g_bal, dict) else 0
    g_total_leg_pnl = g_bal_now - entry_g_bal
    # Position PnL from price (short: entry - current)
    g_pos_pnl = (g_entry_px - g_mid) * g_size if g_entry_px else 0
    h_pos_pnl = (h_mid - h_entry_px) * h_size if h_entry_px else 0
    pos_total = h_pos_pnl + g_pos_pnl
    # GRVT funding ≈ total leg change - position unrealized PnL
    # Use exchange unrealized_pnl if available (more accurate than our price calc)
    g_pos_pnl_exchange = g_unrealized if g_unrealized else g_pos_pnl
    g_funding_est = g_total_leg_pnl - g_pos_pnl_exchange
    funding_total = h_funding + g_funding_est

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

    h_entry_fills = [f for f in h_fills if f.get("dir") == "Open Long" or f.get("side") == "B"]
    g_entry_fills = [f for f in g_fills if not f.get("is_buyer")]
    h_entry_fee = sum(f["fee"] for f in h_entry_fills)
    g_entry_fee = sum(f["fee"] for f in g_entry_fills)
    fee_total = h_entry_fee + g_entry_fee

    # ── Net PnL ──
    net_pnl = funding_total + pos_total - fee_total

    # ── Per-leg totals (matches frontend) ──
    h_bal_now = h_bal.get("account_value", 0) if isinstance(h_bal, dict) else 0
    h_leg_total = h_bal_now - entry_h_bal
    g_leg_total = g_total_leg_pnl

    # ── Rewards ──
    h_notional = h_size * h_entry_px
    g_notional = g_size * g_entry_px
    usde_reward = h_notional * (strategy_cfg.usde_reward_apr / 100) * (holding_days / 365)
    grvt_reward = g_notional * (strategy_cfg.grvt_reward_apr / 100) * (holding_days / 365)
    reward_total = usde_reward + grvt_reward

    # ── NAV ──
    nav_before = entry_h_bal + entry_g_bal
    nav_now = h_bal_now + g_bal_now

    # ── APR ──
    trade_apr = (net_pnl / nav_before) * (365 / holding_days) * 100 if nav_before > 0 and holding_days > 0 else 0
    total_apr = ((net_pnl + reward_total) / nav_before) * (365 / holding_days) * 100 if nav_before > 0 and holding_days > 0 else 0

    # ── Leverage ──
    h_lev = h_notional / h_bal_now if h_bal_now > 0 else 0
    g_lev = g_notional / g_bal_now if g_bal_now > 0 else 0

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
    lines.append(f"  持仓损益报告 (Mark-to-Market)")
    lines.append(f"{'═' * W}")
    lines.append(f"\n  持仓: {hold_str}")
    lines.append(f"  仓位: Long hyna:BTC / Short BTC_USDT_Perp")
    lines.append(f"  数量: {avg_size:.5f} BTC (≈ ${avg_size * h_entry_px:,.0f}/腿)")
    lines.append(f"  杠杆: HyENA {h_lev:.1f}x / GRVT {g_lev:.1f}x")

    # Per-leg totals (matches frontend display)
    lines.append(f"\n  ── 各腿总损益 (与前端一致) ──")
    lines.append(f"    HyENA:  {h_leg_total:+.2f} USD  (unrealized: {h_unrealized:+.2f})")
    lines.append(f"    GRVT:   {g_leg_total:+.2f} USDT (unrealized: {g_unrealized:+.2f})")

    lines.append(f"\n  ── PnL 归因 ──")
    lines.append(f"    Funding Income:          {funding_total:+10.2f}     ({_pct(funding_total):+5.1f}%)")
    lines.append(f"      HyENA (long):              {h_funding:+.2f}")
    lines.append(f"      GRVT (估算):               {g_funding_est:+.2f}  (余额变动-持仓浮盈)")

    lines.append(f"    Position PnL:            {pos_total:+10.2f}     ({_pct(pos_total):+5.1f}%)")
    lines.append(f"      HyENA: ${h_entry_px:,.0f} → ${h_mid:,.0f}    {h_pos_pnl:+.2f}")
    lines.append(f"      GRVT:  ${g_entry_px:,.1f} → ${g_mid:,.1f}    {g_pos_pnl:+.2f}")

    lines.append(f"    Trading Fees:            {-fee_total:+10.2f}     ({_pct(-fee_total):+5.1f}%)")
    lines.append(f"      开仓: H ${h_entry_fee:.2f} + G ${g_entry_fee:.2f}")
    lines.append(f"      (平仓费用待实际平仓后计入)")

    lines.append(f"    {'─' * 37}")
    lines.append(f"    实际 NET PnL:           {net_pnl:+10.2f}")

    lines.append(f"\n  ── External Rewards (估算) ──")
    lines.append(f"    USDe staking ({strategy_cfg.usde_reward_apr:.0f}% APR):  {usde_reward:+.2f}")
    lines.append(f"    GRVT equity ({strategy_cfg.grvt_reward_apr:.0f}% APR):   {grvt_reward:+.2f}")
    lines.append(f"    Reward 合计:             {reward_total:+.2f}")

    if nav_before > 0:
        nav_change = nav_now - nav_before
        nav_pct = nav_change / nav_before * 100
        lines.append(f"\n  ── 年化收益 ──")
        lines.append(f"    NAV: ${nav_before:,.2f} → ${nav_now:,.2f}  ({nav_pct:+.3f}%)")
        lines.append(f"    APR (纯交易):     {trade_apr:+.1f}%")
        lines.append(f"    APR (含rewards):  {total_apr:+.1f}%")

    lines.append(f"{'═' * W}\n")
    print("\n".join(lines), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
