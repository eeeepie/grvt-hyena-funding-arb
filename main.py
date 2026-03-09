#!/usr/bin/env python3
"""
Funding Rate Arbitrage — Main Entry Point
==========================================
    python3 main.py status                # BTC (default)
    python3 main.py --asset HYPE status   # HYPE
    python3 main.py --asset HYPE entry    # open HYPE positions

Modes: status / rates / entry / exit / monitor
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from config import load_config, AssetConfig
from exchange_clients import HyenaClient, GrvtClient, ANNUAL_MULTIPLIER
from strategy_engine import StrategyEngine
from monitor import Monitor, Alerter
from ws_feed import HyenaWsFeed


def setup(asset_name: str = "BTC"):
    """Load config, create all objects. Returns (hyena, grvt, strategy, monitor, alerter, asset_cfg)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")

    exchange_cfg, strategy_cfg, monitor_cfg, asset_cfg = load_config(asset_name)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    h_dir = "Long" if asset_cfg.hyena_is_buy else "Short"
    g_dir = "Long" if not asset_cfg.hyena_is_buy else "Short"
    print(f"\n{'='*64}")
    print(f"  {asset_cfg.name} FUNDING RATE ARBITRAGE  |  {now}")
    print(f"  {h_dir} HyENA ({asset_cfg.hyena_coin}) + {g_dir} GRVT ({asset_cfg.grvt_instrument})")
    print(f"{'='*64}\n")

    alerter = Alerter(monitor_cfg.log_dir)

    alerter.info("Initializing exchange clients...")
    hyena = HyenaClient(exchange_cfg, asset_cfg)
    grvt = GrvtClient(exchange_cfg, asset_cfg)
    monitor = Monitor(hyena, grvt, monitor_cfg, alerter, asset_cfg)
    strategy = StrategyEngine(hyena, grvt, strategy_cfg, monitor_cfg, alerter, asset_cfg)

    return hyena, grvt, strategy, monitor, alerter, asset_cfg


# ── Commands ───────────────────────────────────────────────────────────

async def cmd_status(hyena, grvt, monitor, strategy_config=None, asset_cfg=None):
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
    asset_name = asset_cfg.name if asset_cfg else "BTC"
    avg_mid = (h_mid + g_mid) / 2 if h_mid and g_mid else 0
    if avg_mid > 0 and h_bal and g_bal:
        max_lev = strategy_config.max_leverage if strategy_config else 3.0
        h_equity = h_bal.get("account_value", 0)
        g_equity = g_bal.get("total_equity", 0)
        min_equity = min(h_equity, g_equity)
        max_notional = min_equity * max_lev
        max_qty = max_notional / avg_mid
        sz_dec = asset_cfg.hyena_sz_decimals if asset_cfg else 5
        print(f"\n--- Max Position ({max_lev:.0f}x leverage) ---")
        print(f"  Bottleneck: {'HyENA' if h_equity <= g_equity else 'GRVT'} (${min_equity:,.2f})")
        print(f"  Max Notional: ${max_notional:,.2f}")
        print(f"  Max {asset_name}:      {max_qty:.{sz_dec}f} {asset_name} (approx ${max_qty * avg_mid:,.2f})")

    # GRVT leverage
    g_lev = grvt.get_leverage()
    if g_lev > 0:
        print(f"\n--- GRVT Leverage ---")
        print(f"  Current: {g_lev:.0f}x")

    print("\n--- Positions ---")
    monitor.print_status(hyena.get_position(), grvt.get_position())


async def cmd_rates(hyena, grvt, asset_cfg=None):
    h = hyena.get_funding_rate()
    g = grvt.get_funding_rate()

    grvt_fh = g.get("funding_interval_hours", 8)

    ts = h.get("timestamp")
    ts_str = f" (settle: {ts.strftime('%H:%M UTC')})" if ts else ""

    print(f"  HyENA Settled:   {h['funding_8h']:+.6f}/8h  {h['annual_pct']:+.2f}% ann{ts_str}")
    print(f"  HyENA Predicted: {h.get('predicted_8h',0):+.6f}/8h  {h.get('predicted_ann',0):+.2f}% ann")
    print(f"  GRVT ({grvt_fh}h→8h): {g['funding_8h_decimal']:+.6f}/8h  {g['annual_pct']:+.2f}% ann")

    # Show spread for both directions
    h_pred = h.get("predicted_8h", h["funding_8h"])
    h_pred_ann = h.get("predicted_ann", h["annual_pct"])
    usde, equity = 12.0, 10.0

    for label, hib in [("[A] Long H + Short G", True), ("[B] Short H + Long G", False)]:
        if hib:
            spread_s = g["funding_8h_decimal"] - h["funding_8h"]
            spread_p = g["funding_8h_decimal"] - h_pred
            h_fund = -h_pred_ann
            g_fund = g["annual_pct"]
        else:
            spread_s = h["funding_8h"] - g["funding_8h_decimal"]
            spread_p = h_pred - g["funding_8h_decimal"]
            h_fund = h_pred_ann
            g_fund = -g["annual_pct"]
        net = h_fund + g_fund + usde + equity
        default = " (default)" if hib == (asset_cfg.hyena_is_buy if asset_cfg else True) else ""
        print(f"\n  {label}{default}:")
        print(f"    settled: {spread_s:+.6f}/8h  {spread_s * ANNUAL_MULTIPLIER:+.2f}% ann")
        print(f"    predict: {spread_p:+.6f}/8h  {spread_p * ANNUAL_MULTIPLIER:+.2f}% ann")
        print(f"    yield est: funding {h_fund + g_fund:+.1f}% + rewards {usde + equity:+.0f}% = {net:+.1f}% ann")
    if asset_cfg and asset_cfg.name != "BTC":
        print(f"\n  Note: GRVT 5x points multiplier (altcoin)")


async def cmd_entry(strategy, monitor, alerter, asset_cfg=None):
    min_notional = asset_cfg.grvt_min_notional if asset_cfg else 100.0
    hyena = strategy.hyena
    grvt = strategy.grvt

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

    if usd_per_leg < min_notional:
        alerter.critical(f"Amount ${usd_per_leg} below GRVT min notional ${min_notional}")
        return
    if max_leverage < 1 or max_leverage > 10:
        alerter.critical(f"Leverage {max_leverage}x out of valid range [1, 10]")
        return

    # Direction picker: show current rates and let user choose
    default_hib = asset_cfg.hyena_is_buy if asset_cfg else True
    try:
        h = hyena.get_funding_rate()
        g = grvt.get_funding_rate()
        h_pred = h.get("predicted_8h", h["funding_8h"])

        print(f"\n  Current rates:")
        print(f"    HyENA: {h.get('predicted_8h', h['funding_8h']):+.6f}/8h ({h.get('predicted_ann', h['annual_pct']):+.1f}% ann)")
        print(f"    GRVT:  {g['funding_8h_decimal']:+.6f}/8h ({g['annual_pct']:+.1f}% ann)")

        # Compute spread for both directions
        spread_a = g["funding_8h_decimal"] - h_pred  # Long H + Short G
        spread_b = h_pred - g["funding_8h_decimal"]  # Short H + Long G
        spread_a_ann = spread_a * ANNUAL_MULTIPLIER
        spread_b_ann = spread_b * ANNUAL_MULTIPLIER

        a_default = " (default)" if default_hib else ""
        b_default = " (default)" if not default_hib else ""
        print(f"\n  Direction:")
        print(f"    [A] Long HyENA + Short GRVT{a_default} — spread: {spread_a_ann:+.1f}% ann")
        print(f"    [B] Short HyENA + Long GRVT{b_default} — spread: {spread_b_ann:+.1f}% ann")

        default_letter = "A" if default_hib else "B"
        dir_input = input(f"  Choose [{default_letter}]: ").strip().upper()
        if dir_input == "":
            hyena_is_buy = default_hib
        elif dir_input == "A":
            hyena_is_buy = True
        elif dir_input == "B":
            hyena_is_buy = False
        else:
            alerter.critical(f"Invalid direction '{dir_input}', aborting")
            return
    except Exception as e:
        alerter.warning(f"Could not fetch rates for direction picker: {e}")
        alerter.info(f"Using default direction")
        hyena_is_buy = default_hib

    h_dir = "Long" if hyena_is_buy else "Short"
    g_dir = "Short" if hyena_is_buy else "Long"

    # Check GRVT leverage (API set is deprecated, must use frontend)
    g_lev = strategy.grvt.get_leverage()
    if g_lev > 0 and abs(g_lev - max_leverage) > 0.5:
        print(f"\n  WARNING: GRVT leverage is {g_lev:.0f}x, expected {max_leverage:.0f}x")
        inst = asset_cfg.grvt_instrument if asset_cfg else "BTC_USDT_Perp"
        print(f"  Please set it at: grvt.io -> {inst} -> adjust leverage")
    elif g_lev > 0:
        print(f"\n  GRVT leverage: {g_lev:.0f}x OK")

    print(f"\n  -> ${usd_per_leg:,.0f} per leg | {max_leverage}x | {h_dir} HyENA + {g_dir} GRVT")
    confirm = input("  Confirm entry? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("  Cancelled")
        return

    alerter.info("Executing entry...")
    if not await strategy.open_position(usd_per_leg=usd_per_leg, max_leverage=max_leverage,
                                         hyena_is_buy=hyena_is_buy):
        alerter.critical("Entry failed!")
        return
    alerter.info("Entry successful -> starting monitor")
    await _monitor_loop(strategy, monitor, alerter)


async def cmd_exit(strategy, alerter, asset_cfg=None):
    alerter.info("Executing exit...")
    flat_threshold = asset_cfg.flat_threshold if asset_cfg else 0.001
    h_pos, g_pos = strategy.hyena.get_position(), strategy.grvt.get_position()
    if abs(h_pos.get("size", 0)) < flat_threshold and abs(g_pos.get("size", 0)) < flat_threshold:
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
    # Apply saved direction so PnL report uses correct sides
    strategy.load_direction_from_state()

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
    # Load persisted entry state (includes direction)
    strategy.state.load_entry_state()
    strategy.load_direction_from_state()

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

    tasks = [
        loop(strategy.check_mirror_close, mc.position_poll_interval),
        monitor.collect_funding_rates(),
        monitor.monitor_usde_peg(),
        monitor.check_circuit_breaker(),
        loop(strategy.check_rebalance, 300),
    ]

    # Start WebSocket feed for sub-second mirror-close detection (HyENA leg)
    wallet = getattr(strategy.hyena, '_account', None)
    if wallet:
        ws_feed = _create_ws_feed(strategy, alerter, wallet.address)
        tasks.append(ws_feed.run())
        alerter.info(f"WS feed enabled for {strategy.asset.hyena_coin} (sub-second mirror-close)")
    else:
        alerter.warning("WS feed disabled: HyENA SDK not initialized (no wallet address)")

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        alerter.info("Monitor stopped")


def _create_ws_feed(strategy, alerter, wallet_address: str) -> HyenaWsFeed:
    """Create WS feed with callbacks wired to strategy engine."""

    def on_position_change(coin: str, fill_data: dict):
        """WS callback — schedule async mirror-close check on the event loop."""
        asyncio.ensure_future(strategy.on_ws_position_event(coin, fill_data))

    return HyenaWsFeed(
        wallet_address=wallet_address,
        coin=strategy.asset.hyena_coin,
        on_position_change=on_position_change,
    )


# ── Main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Funding Rate Arbitrage")
    parser.add_argument("--asset", default="BTC", choices=["BTC", "HYPE", "SOL"],
                        help="Asset to trade (default: BTC)")
    parser.add_argument("mode", choices=["status", "rates", "entry", "exit", "monitor"])
    args = parser.parse_args()

    hyena, grvt, strategy, monitor, alerter, asset_cfg = setup(args.asset)

    commands = {
        "status":  lambda: cmd_status(hyena, grvt, monitor, strategy.config, asset_cfg),
        "rates":   lambda: cmd_rates(hyena, grvt, asset_cfg),
        "entry":   lambda: cmd_entry(strategy, monitor, alerter, asset_cfg),
        "exit":    lambda: cmd_exit(strategy, alerter, asset_cfg),
        "monitor": lambda: cmd_monitor(strategy, monitor, alerter),
    }

    try:
        asyncio.run(commands[args.mode]())
    except KeyboardInterrupt:
        print("\nShutdown complete")
        sys.exit(0)


if __name__ == "__main__":
    main()
