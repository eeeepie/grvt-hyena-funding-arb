#!/usr/bin/env python3
"""
BTC Funding Rate Arbitrage — Main Entry Point
==============================================
    python3 main.py status    # connectivity + positions
    python3 main.py rates     # current funding rates & yield estimate
    python3 main.py entry     # open positions + start monitoring
    python3 main.py monitor   # monitor (positions already open)
    python3 main.py exit      # close all positions
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from config import load_config
from exchange_clients import HyenaClient, GrvtClient, ANNUAL_MULTIPLIER
from strategy_engine import StrategyEngine
from monitor import Monitor, Alerter


def setup():
    """Load config, create all objects. Returns (hyena, grvt, strategy, monitor, alerter)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'='*64}")
    print(f"  BTC FUNDING RATE ARBITRAGE  |  {now}")
    print(f"  Long HyENA (hyna:BTC) + Short GRVT (BTC_USDT_Perp)")
    print(f"{'='*64}\n")

    exchange_cfg, strategy_cfg, monitor_cfg = load_config()
    alerter = Alerter(monitor_cfg.log_dir)

    alerter.info("初始化交易所客户端...")
    hyena = HyenaClient(exchange_cfg)
    grvt = GrvtClient(exchange_cfg)
    monitor = Monitor(hyena, grvt, monitor_cfg, alerter)
    strategy = StrategyEngine(hyena, grvt, strategy_cfg, monitor_cfg, alerter)

    return hyena, grvt, strategy, monitor, alerter


# ── Commands ───────────────────────────────────────────────────────────

async def cmd_status(hyena, grvt, monitor, strategy_config=None):
    h_mid = g_mid = 0
    print("--- Connectivity ---")
    for name, fn in [("HyENA", hyena.get_mid_price), ("GRVT", grvt.get_mid_price)]:
        try:
            mid = fn()
            if name == "HyENA": h_mid = mid
            else: g_mid = mid
            print(f"  {name}: Connected. Mid: ${mid:,.1f}")
        except Exception as e:
            print(f"  {name}: FAILED — {e}")

    h_bal = g_bal = None
    print("\n--- Balances ---")
    for name, fn in [("HyENA", hyena.get_balance), ("GRVT", grvt.get_balance)]:
        try:
            bal = fn()
            if name == "HyENA": h_bal = bal
            else: g_bal = bal
            print(f"  {name}: {bal}")
        except Exception as e:
            print(f"  {name}: {e}")

    # Max position estimate
    avg_mid = (h_mid + g_mid) / 2 if h_mid and g_mid else 0
    if avg_mid > 0 and h_bal and g_bal:
        max_lev = strategy_config.max_leverage if strategy_config else 3.0
        h_equity = h_bal.get("account_value", 0)
        g_equity = g_bal.get("total_equity", 0)
        min_equity = min(h_equity, g_equity)
        max_notional = min_equity * max_lev
        max_btc = max_notional / avg_mid
        print(f"\n--- 最大可开仓 (杠杆 {max_lev:.0f}x) ---")
        print(f"  瓶颈账户: {'HyENA' if h_equity <= g_equity else 'GRVT'} (${min_equity:,.2f})")
        print(f"  最大名义: ${max_notional:,.2f}")
        print(f"  最大 BTC:  {max_btc:.5f} BTC (≈ ${max_btc * avg_mid:,.2f})")

    print("\n--- Positions ---")
    monitor.print_status(hyena.get_position(), grvt.get_position())


async def cmd_rates(hyena, grvt):
    h = hyena.get_funding_rate()
    g = grvt.get_funding_rate()

    # 已结算 spread
    spread_settled = g["funding_8h_decimal"] - h["funding_8h"]
    spread_settled_ann = spread_settled * ANNUAL_MULTIPLIER
    # 预测 spread
    spread_pred = g["funding_8h_decimal"] - h.get("predicted_8h", h["funding_8h"])
    spread_pred_ann = spread_pred * ANNUAL_MULTIPLIER

    ts = h.get("timestamp")
    ts_str = f" (settle: {ts.strftime('%H:%M UTC')})" if ts else ""

    print(f"  HyENA 已结算: {h['funding_8h']:+.6f}/8h  {h['annual_pct']:+.2f}% ann{ts_str}")
    print(f"  HyENA 预测:   {h.get('predicted_8h',0):+.6f}/8h  {h.get('predicted_ann',0):+.2f}% ann")
    print(f"  GRVT:          {g['funding_8h_decimal']:+.6f}/8h  {g['annual_pct']:+.2f}% ann")
    print(f"  Spread(settled): {spread_settled:+.6f}/8h  {spread_settled_ann:+.2f}% ann")
    print(f"  Spread(predict): {spread_pred:+.6f}/8h  {spread_pred_ann:+.2f}% ann")

    # Yield estimate — 用预测费率更有参考价值
    h_pred_ann = h.get("predicted_ann", h["annual_pct"])
    h_long = -h_pred_ann          # long pays when funding positive
    g_short = g["annual_pct"]     # short receives when funding positive
    usde, equity = 12.0, 10.0
    net = h_long + g_short + usde + equity

    print(f"\n  收益估算 (基于预测费率):")
    print(f"    HyENA Long Funding: {h_long:+.1f}% (预测)")
    print(f"    GRVT Short Funding: {g_short:+.1f}%")
    print(f"    USDe Reward:        +{usde:.0f}% (链外奖励，不含在API费率中)")
    print(f"    GRVT Equity:        +{equity:.0f}% (链外奖励)")
    print(f"    ─────────────────────")
    print(f"    预估净收益:          {net:+.1f}% ann")


async def cmd_entry(strategy, monitor, alerter):
    # Interactive input for position parameters
    print("── 开仓参数 ──")
    print()
    print("  ⚠  请先在 GRVT 前端确认杠杆倍数!")
    print("     grvt.io → BTC_USDT_Perp → 调整杠杆至目标倍数")
    print()
    try:
        usd_input = input(f"  每腿金额 USD [{strategy.config.usd_per_leg}]: ").strip()
        usd_per_leg = float(usd_input) if usd_input else strategy.config.usd_per_leg

        lev_input = input(f"  最大杠杆倍数 [{strategy.config.max_leverage}x]: ").strip().rstrip("xX")
        max_leverage = float(lev_input) if lev_input else strategy.config.max_leverage
    except (ValueError, EOFError):
        alerter.critical("输入无效，中止开仓")
        return

    if usd_per_leg < 100:
        alerter.critical(f"金额 ${usd_per_leg} 低于 GRVT 最小名义值 $100")
        return
    if max_leverage < 1 or max_leverage > 10:
        alerter.critical(f"杠杆 {max_leverage}x 不在合理范围 [1, 10]")
        return

    print(f"\n  → 每腿 ${usd_per_leg:,.0f} | 最大杠杆 {max_leverage}x")
    confirm = input("  确认开仓? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("  已取消")
        return

    alerter.info("执行开仓...")
    if not await strategy.open_position(usd_per_leg=usd_per_leg, max_leverage=max_leverage):
        alerter.critical("开仓失败!")
        return
    alerter.info("开仓成功 → 启动监控")
    await _monitor_loop(strategy, monitor, alerter)


async def cmd_exit(strategy, alerter):
    alerter.info("执行平仓...")
    h_pos, g_pos = strategy.hyena.get_position(), strategy.grvt.get_position()
    if abs(h_pos.get("size", 0)) < 0.001 and abs(g_pos.get("size", 0)) < 0.001:
        alerter.info("无持仓")
        return

    # Snapshot balances before close (for NAV comparison)
    h_bal, g_bal = strategy.hyena.get_balance(), strategy.grvt.get_balance()

    strategy.state.load_from_exchange(h_pos, g_pos)

    # Save snapshot before close resets state
    has_entry_data = strategy.state.entry_time > 0
    entry_time = strategy.state.entry_time
    entry_h_balance = strategy.state.entry_h_balance
    entry_g_balance = strategy.state.entry_g_balance
    entry_h_mid = strategy.state.entry_h_mid
    entry_g_mid = strategy.state.entry_g_mid
    entry_h_fills = strategy.state.entry_h_fills[:]
    entry_g_fills = strategy.state.entry_g_fills[:]
    h_size = strategy.state.hyena.size
    g_size = strategy.state.grvt.size

    if await strategy.close_position():
        alerter.info("平仓完成 ✓")
        # Restore snapshot for PnL report
        if has_entry_data:
            strategy.state.entry_time = entry_time
            strategy.state.entry_h_balance = entry_h_balance
            strategy.state.entry_g_balance = entry_g_balance
            strategy.state.entry_h_mid = entry_h_mid
            strategy.state.entry_g_mid = entry_g_mid
            strategy.state.entry_h_fills = entry_h_fills
            strategy.state.entry_g_fills = entry_g_fills
        strategy.state.hyena.size = h_size
        strategy.state.grvt.size = g_size
        await strategy.print_exit_pnl(
            pre_h_pos=h_pos, pre_g_pos=g_pos,
            pre_h_bal=h_bal, pre_g_bal=g_bal,
        )
    else:
        alerter.critical("平仓可能不完整!")


async def cmd_monitor(strategy, monitor, alerter):
    h_pos, g_pos = strategy.hyena.get_position(), strategy.grvt.get_position()
    strategy.state.load_from_exchange(h_pos, g_pos)

    if strategy.state.state.value == "open":
        alerter.info(f"已加载仓位: HyENA={strategy.state.hyena.size:+.4f} GRVT={strategy.state.grvt.size:+.4f}")
    else:
        alerter.warning("无持仓 — 仅运行费率监控")

    await _monitor_loop(strategy, monitor, alerter)


async def _monitor_loop(strategy, monitor, alerter):
    """Run all monitoring tasks concurrently. Ctrl+C to stop."""
    mc = strategy.mon_config
    alerter.info(f"监控启动: position={mc.position_poll_interval}s, funding={mc.funding_rate_interval}s, "
                 f"peg={mc.usde_peg_interval}s, circuit={mc.book_price_interval}s")

    async def loop(fn, interval):
        while True:
            await fn()
            await asyncio.sleep(interval)

    try:
        await asyncio.gather(
            loop(strategy.check_mirror_close, mc.position_poll_interval),
            monitor.collect_funding_rates(),
            monitor.monitor_usde_peg(),
            monitor.check_circuit_breaker(),
            loop(strategy.check_rebalance, 300),
        )
    except asyncio.CancelledError:
        alerter.info("监控已停止")


# ── Main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BTC Funding Rate Arbitrage")
    parser.add_argument("mode", choices=["status", "rates", "entry", "exit", "monitor"])
    args = parser.parse_args()

    hyena, grvt, strategy, monitor, alerter = setup()

    commands = {
        "status":  lambda: cmd_status(hyena, grvt, monitor, strategy.config),
        "rates":   lambda: cmd_rates(hyena, grvt),
        "entry":   lambda: cmd_entry(strategy, monitor, alerter),
        "exit":    lambda: cmd_exit(strategy, alerter),
        "monitor": lambda: cmd_monitor(strategy, monitor, alerter),
    }

    try:
        asyncio.run(commands[args.mode]())
    except KeyboardInterrupt:
        print("\nShutdown ✓")
        sys.exit(0)


if __name__ == "__main__":
    main()
