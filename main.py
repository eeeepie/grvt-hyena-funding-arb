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

    alerter.info("Initializing exchange clients...")
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
        print(f"\n--- Max Position ({max_lev:.0f}x leverage) ---")
        print(f"  Bottleneck: {'HyENA' if h_equity <= g_equity else 'GRVT'} (${min_equity:,.2f})")
        print(f"  Max Notional: ${max_notional:,.2f}")
        print(f"  Max BTC:      {max_btc:.5f} BTC (approx ${max_btc * avg_mid:,.2f})")

    # GRVT leverage
    g_lev = grvt.get_leverage()
    if g_lev > 0:
        print(f"\n--- GRVT Leverage ---")
        print(f"  Current: {g_lev:.0f}x")

    print("\n--- Positions ---")
    monitor.print_status(hyena.get_position(), grvt.get_position())


async def cmd_rates(hyena, grvt):
    h = hyena.get_funding_rate()
    g = grvt.get_funding_rate()

    # Spread for strategy: Long HyENA + Short GRVT (both standard convention)
    # positive rate = longs pay shorts
    # Long HyENA income = -h_rate, Short GRVT income = +g_rate
    # Net spread = g_rate - h_rate
    spread_settled = g["funding_8h_decimal"] - h["funding_8h"]
    spread_settled_ann = spread_settled * ANNUAL_MULTIPLIER
    spread_pred = g["funding_8h_decimal"] - h.get("predicted_8h", h["funding_8h"])
    spread_pred_ann = spread_pred * ANNUAL_MULTIPLIER

    ts = h.get("timestamp")
    ts_str = f" (settle: {ts.strftime('%H:%M UTC')})" if ts else ""

    print(f"  HyENA Settled:   {h['funding_8h']:+.6f}/8h  {h['annual_pct']:+.2f}% ann{ts_str}")
    print(f"  HyENA Predicted: {h.get('predicted_8h',0):+.6f}/8h  {h.get('predicted_ann',0):+.2f}% ann")
    print(f"  GRVT:          {g['funding_8h_decimal']:+.6f}/8h  {g['annual_pct']:+.2f}% ann")
    print(f"  Net Spread (Long H + Short G):")
    print(f"    settled: {spread_settled:+.6f}/8h  {spread_settled_ann:+.2f}% ann")
    print(f"    predict: {spread_pred:+.6f}/8h  {spread_pred_ann:+.2f}% ann")

    # Yield estimate — predicted rate is more indicative
    h_pred_ann = h.get("predicted_ann", h["annual_pct"])
    h_long = -h_pred_ann          # long pays when rate positive
    g_short = g["annual_pct"]     # short receives when rate positive (standard)
    usde, equity = 12.0, 10.0
    net = h_long + g_short + usde + equity

    print(f"\n  Yield Estimate (based on predicted rate):")
    print(f"    HyENA Long Funding: {h_long:+.1f}% (predicted)")
    print(f"    GRVT Short Funding: {g_short:+.1f}%")
    print(f"    USDe Reward:        +{usde:.0f}% (off-chain reward, not in API rate)")
    print(f"    GRVT Equity:        +{equity:.0f}% (off-chain reward)")
    print(f"    ─────────────────────")
    print(f"    Est. Net Yield:      {net:+.1f}% ann")


async def cmd_entry(strategy, monitor, alerter):
    # Interactive input for position parameters
    print("── Entry Parameters ──\n")
    try:
        usd_input = input(f"  USD per leg [{strategy.config.usd_per_leg}]: ").strip()
        usd_per_leg = float(usd_input) if usd_input else strategy.config.usd_per_leg

        lev_input = input(f"  Max leverage [{strategy.config.max_leverage}x]: ").strip().rstrip("xX")
        max_leverage = float(lev_input) if lev_input else strategy.config.max_leverage
    except (ValueError, EOFError):
        alerter.critical("Invalid input, aborting entry")
        return

    if usd_per_leg < 100:
        alerter.critical(f"Amount ${usd_per_leg} below GRVT min notional $100")
        return
    if max_leverage < 1 or max_leverage > 10:
        alerter.critical(f"Leverage {max_leverage}x out of valid range [1, 10]")
        return

    # Check GRVT leverage (API set is deprecated, must use frontend)
    g_lev = strategy.grvt.get_leverage()
    if g_lev > 0 and abs(g_lev - max_leverage) > 0.5:
        print(f"\n  WARNING: GRVT leverage is {g_lev:.0f}x, expected {max_leverage:.0f}x")
        print(f"  Please set it at: grvt.io -> BTC_USDT_Perp -> adjust leverage")
    elif g_lev > 0:
        print(f"\n  GRVT leverage: {g_lev:.0f}x OK")

    print(f"\n  -> ${usd_per_leg:,.0f} per leg | max leverage {max_leverage}x")
    confirm = input("  Confirm entry? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("  Cancelled")
        return

    alerter.info("Executing entry...")
    if not await strategy.open_position(usd_per_leg=usd_per_leg, max_leverage=max_leverage):
        alerter.critical("Entry failed!")
        return
    alerter.info("Entry successful -> starting monitor")
    await _monitor_loop(strategy, monitor, alerter)


async def cmd_exit(strategy, alerter):
    alerter.info("Executing exit...")
    h_pos, g_pos = strategy.hyena.get_position(), strategy.grvt.get_position()
    if abs(h_pos.get("size", 0)) < 0.001 and abs(g_pos.get("size", 0)) < 0.001:
        alerter.info("No positions")
        return

    # Snapshot balances + book mid before close (for NAV & slippage comparison)
    h_bal, g_bal = strategy.hyena.get_balance(), strategy.grvt.get_balance()
    h_book, g_book = strategy.hyena.get_book(), strategy.grvt.get_book()
    h_mid_pre = h_book["mid"]
    g_mid_pre = g_book["mid"]

    strategy.state.load_from_exchange(h_pos, g_pos)

    # Try loading persisted entry state (cross-process)
    if strategy.state.entry_time == 0:
        strategy.state.load_entry_state()

    # Save snapshot before close resets state
    has_entry_data = strategy.state.entry_time > 0
    entry_time = strategy.state.entry_time
    entry_h_balance = strategy.state.entry_h_balance
    entry_h_perps_margin = strategy.state.entry_h_perps_margin
    entry_g_balance = strategy.state.entry_g_balance
    entry_h_mid = strategy.state.entry_h_mid
    entry_g_mid = strategy.state.entry_g_mid
    entry_h_fills = strategy.state.entry_h_fills[:]
    entry_g_fills = strategy.state.entry_g_fills[:]
    h_size = strategy.state.hyena.size
    g_size = strategy.state.grvt.size

    if await strategy.close_position():
        alerter.info("Exit complete")
        # Restore snapshot for PnL report
        if has_entry_data:
            strategy.state.entry_time = entry_time
            strategy.state.entry_h_balance = entry_h_balance
            strategy.state.entry_h_perps_margin = entry_h_perps_margin
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
            pre_h_mid=h_mid_pre, pre_g_mid=g_mid_pre,
        )
    else:
        alerter.critical("Exit may be incomplete!")


async def cmd_monitor(strategy, monitor, alerter):
    h_pos, g_pos = strategy.hyena.get_position(), strategy.grvt.get_position()
    strategy.state.load_from_exchange(h_pos, g_pos)

    if strategy.state.state.value == "open":
        alerter.info(f"Loaded positions: HyENA={strategy.state.hyena.size:+.4f} GRVT={strategy.state.grvt.size:+.4f}")
    else:
        alerter.warning("No positions -- running rate monitor only")

    await _monitor_loop(strategy, monitor, alerter)


async def _monitor_loop(strategy, monitor, alerter):
    """Run all monitoring tasks concurrently. Ctrl+C to stop."""
    mc = strategy.mon_config
    alerter.info(f"Monitor started: position={mc.position_poll_interval}s, funding={mc.funding_rate_interval}s, "
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
        alerter.info("Monitor stopped")


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
        print("\nShutdown complete")
        sys.exit(0)


if __name__ == "__main__":
    main()
