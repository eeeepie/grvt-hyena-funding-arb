"""
Strategy Engine: entry, exit, mirror close, rebalance.
Long HyENA + Short GRVT, delta-neutral.
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from config import StrategyConfig, MonitorConfig

logger = logging.getLogger("strategy")


# ============================================================================
# State
# ============================================================================

class PositionState(Enum):
    FLAT = "flat"
    ENTERING = "entering"
    OPEN = "open"
    CLOSING = "closing"
    ERROR = "error"


@dataclass
class LegInfo:
    exchange: str
    size: float = 0.0           # positive = long, negative = short
    entry_px: float = 0.0
    last_known_size: float = 0.0
    last_poll_time: float = 0.0


@dataclass
class StrategyState:
    state: PositionState = PositionState.FLAT
    hyena: LegInfo = field(default_factory=lambda: LegInfo("hyena"))
    grvt: LegInfo = field(default_factory=lambda: LegInfo("grvt"))
    mirror_close_active: bool = False
    paused: bool = False
    # When not None, a self-initiated trade is in flight — skip mirror close check
    pending_trade: bool = False
    # Double-confirmation for mirror close (防误触)
    _pending_anomaly: Optional[dict] = None  # {"leg": str, "expected": float, "got": float, "time": float}
    # ERROR retry state (失败后持续重试)
    _retry_count: int = 0
    _retry_last_attempt: float = 0.0
    # Entry snapshot for PnL tracking
    entry_time: float = 0.0
    entry_h_balance: float = 0.0
    entry_g_balance: float = 0.0
    entry_h_mid: float = 0.0
    entry_g_mid: float = 0.0
    entry_h_fills: list = field(default_factory=list)
    entry_g_fills: list = field(default_factory=list)

    def reset(self):
        """Reset to flat — called after close or mirror close."""
        self.state = PositionState.FLAT
        for leg in (self.hyena, self.grvt):
            leg.size = leg.last_known_size = leg.entry_px = 0
        self.mirror_close_active = False
        self.pending_trade = False
        self._pending_anomaly = None
        self._retry_count = 0
        self._retry_last_attempt = 0.0
        self.entry_time = 0.0
        self.entry_h_balance = self.entry_g_balance = 0.0
        self.entry_h_mid = self.entry_g_mid = 0.0
        self.entry_h_fills = []
        self.entry_g_fills = []

    def load_from_exchange(self, h_pos: dict, g_pos: dict):
        """Sync state from live exchange positions."""
        for leg, pos in [(self.hyena, h_pos), (self.grvt, g_pos)]:
            leg.size = pos.get("size", 0)
            leg.last_known_size = leg.size
            leg.entry_px = pos.get("entry_px", 0)
        if abs(self.hyena.size) > 0.001 or abs(self.grvt.size) > 0.001:
            self.state = PositionState.OPEN


# ============================================================================
# Engine
# ============================================================================

class StrategyEngine:
    def __init__(self, hyena_client, grvt_client,
                 config: StrategyConfig, mon_config: MonitorConfig, alerter):
        self.hyena = hyena_client
        self.grvt = grvt_client
        self.config = config
        self.mon_config = mon_config
        self.alerter = alerter
        self.state = StrategyState()
        self._poll_fails = {"hyena": 0, "grvt": 0}

    # --- Helpers ---

    async def _both(self, hyena_fn, grvt_fn):
        """Run two blocking calls concurrently via threads."""
        return await asyncio.gather(
            asyncio.to_thread(hyena_fn),
            asyncio.to_thread(grvt_fn),
            return_exceptions=True,
        )

    # ── Entry ──────────────────────────────────────────────────────────────

    async def open_position(self, usd_per_leg: float = None, max_leverage: float = None) -> bool:
        if self.state.state != PositionState.FLAT:
            self.alerter.warning(f"无法开仓: 当前状态={self.state.state.value}")
            return False

        self.state.state = PositionState.ENTERING
        usd_per_leg = usd_per_leg or self.config.usd_per_leg
        max_lev = max_leverage or self.config.max_leverage

        # 1. Pre-flight
        self.alerter.info("开仓前检查...")
        h_bal, g_bal = await self._both(self.hyena.get_balance, self.grvt.get_balance)
        self.alerter.info(f"  HyENA: {h_bal}  |  GRVT: {g_bal}")

        # 2. Get books
        self.alerter.info("查询订单簿...")
        h_book, g_book = await self._both(self.hyena.get_book, self.grvt.get_book)
        if isinstance(h_book, Exception) or isinstance(g_book, Exception):
            self.alerter.critical(f"订单簿失败: {h_book} / {g_book}")
            self.state.state = PositionState.FLAT
            return False

        avg_mid = (h_book["mid"] + g_book["mid"]) / 2
        self.alerter.info(
            f"  HyENA mid=${h_book['mid']:,.1f} ({h_book['spread_bps']:.1f}bps) | "
            f"GRVT mid=${g_book['mid']:,.1f} ({g_book['spread_bps']:.1f}bps)"
        )

        # 2b. Compute BTC quantity from USD amount
        qty = round(usd_per_leg / avg_mid, self.config.quantity_precision)
        # Enforce GRVT minimums
        if qty < 0.001:
            self.alerter.critical(f"数量 {qty} BTC < GRVT 最小 0.001")
            self.state.state = PositionState.FLAT
            return False
        if qty * avg_mid < 100:
            self.alerter.critical(f"名义值 ${qty * avg_mid:.2f} < GRVT 最小 $100")
            self.state.state = PositionState.FLAT
            return False
        self.alerter.info(f"  开仓数量: {qty} BTC (≈ ${qty * avg_mid:,.2f}/腿)")

        # 2c. Leverage guard
        notional = qty * avg_mid
        h_equity = h_bal.get("account_value", 0) if isinstance(h_bal, dict) else 0
        g_equity = g_bal.get("total_equity", 0) if isinstance(g_bal, dict) else 0
        h_eff_lev = notional / h_equity if h_equity > 0 else 999
        g_eff_lev = notional / g_equity if g_equity > 0 else 999

        if h_eff_lev > max_lev or g_eff_lev > max_lev:
            self.alerter.critical(
                f"杠杆超限! HyENA={h_eff_lev:.1f}x GRVT={g_eff_lev:.1f}x > {max_lev:.0f}x — 中止开仓"
            )
            self.state.state = PositionState.FLAT
            return False
        self.alerter.info(f"  杠杆检查: HyENA={h_eff_lev:.1f}x GRVT={g_eff_lev:.1f}x (上限{max_lev:.0f}x) ✓")

        # 3. Prices (mid ± 2bps aggressive)
        offset = self.config.aggressive_limit_offset_bps / 10000
        buy_px = round(h_book["mid"] * (1 + offset), 0)  # HyENA tick = $1
        sell_px = round(g_book["mid"] * (1 - offset), 1)  # GRVT tick = $0.1

        # 4. Submit both orders
        self.state.pending_trade = True
        self.alerter.info(f"并发下单: HyENA BUY {qty}@${buy_px:,.1f} | GRVT SELL {qty}@${sell_px:,.2f}")

        h_res, g_res = await self._both(
            lambda: self.hyena.place_order(True, qty, buy_px),
            lambda: self.grvt.place_order(False, qty, sell_px),
        )

        h_ok = not isinstance(h_res, Exception)
        g_ok = not isinstance(g_res, Exception)

        # 5a. Both filled
        if h_ok and g_ok:
            self.alerter.info(f"HyENA: {h_res}")
            self.alerter.info(f"GRVT:  {g_res}")
            await asyncio.sleep(1)

            h_pos, g_pos = await self._both(self.hyena.get_position, self.grvt.get_position)
            self.state.load_from_exchange(h_pos, g_pos)
            self.state.pending_trade = False
            self.alerter.info(f"仓位开启! HyENA:{self.state.hyena.size:+.4f} GRVT:{self.state.grvt.size:+.4f}")

            # Record entry snapshot
            entry_ms = int(time.time() * 1000) - 30000  # 30s ago to capture fills
            self.state.entry_time = time.time()
            self.state.entry_h_mid = h_book["mid"]
            self.state.entry_g_mid = g_book["mid"]
            self.state.entry_h_balance = h_bal.get("account_value", 0) if isinstance(h_bal, dict) else 0
            self.state.entry_g_balance = g_bal.get("total_equity", 0) if isinstance(g_bal, dict) else 0

            # Query fills and print entry costs
            await self._print_entry_costs(entry_ms, h_book["mid"], g_book["mid"])

            if abs(abs(self.state.hyena.size) - abs(self.state.grvt.size)) > self.config.qty_mismatch_threshold:
                self.alerter.warning("BTC数量不匹配!")
            return True

        # 5b. One side failed — close the other
        if h_ok and not g_ok:
            self.alerter.emergency(f"GRVT失败! {g_res}. 正在关闭HyENA...")
            try: await asyncio.to_thread(self.hyena.market_close, qty)
            except Exception as e: self.alerter.emergency(f"HyENA关仓也失败: {e}")
        elif g_ok and not h_ok:
            self.alerter.emergency(f"HyENA失败! {h_res}. 正在关闭GRVT...")
            try: await asyncio.to_thread(self.grvt.market_close, qty, False)
            except Exception as e: self.alerter.emergency(f"GRVT关仓也失败: {e}")
        else:
            self.alerter.critical(f"双边都失败: {h_res} / {g_res}")

        self.state.reset()
        return False

    # ── PnL Reporting ─────────────────────────────────────────────────────

    async def _print_entry_costs(self, entry_ms: int, h_mid: float, g_mid: float):
        """Query fills after entry and print cost breakdown."""
        try:
            h_fills, g_fills = await self._both(
                lambda: self.hyena.get_fills(entry_ms),
                lambda: self.grvt.get_fills(limit=10),
            )
            if isinstance(h_fills, Exception):
                h_fills = []
            if isinstance(g_fills, Exception):
                g_fills = []

            self.state.entry_h_fills = h_fills
            self.state.entry_g_fills = g_fills

            lines = ["\n── 开仓成本 ──"]
            total_fee = 0.0

            if h_fills:
                f = h_fills[0]
                slip_bps = abs(f["px"] - h_mid) / h_mid * 10000 if h_mid else 0
                lines.append(f"  HyENA BUY:  fill ${f['px']:,.0f} | mid ${h_mid:,.0f} → slippage {slip_bps:.1f}bps | fee ${f['fee']:.4f}")
                total_fee += f["fee"]
            else:
                lines.append("  HyENA: 未获取到成交记录")

            if g_fills:
                f = g_fills[0]
                slip_bps = abs(f["px"] - g_mid) / g_mid * 10000 if g_mid else 0
                fee_pct = f["fee_rate"] if f["fee_rate"] else (f["fee"] / (f["px"] * f["sz"]) * 100 if f["px"] * f["sz"] > 0 else 0)
                lines.append(f"  GRVT SELL:  fill ${f['px']:,.1f} | mid ${g_mid:,.1f} → slippage {slip_bps:.1f}bps | fee ${f['fee']:.4f} ({fee_pct:.3f}%)")
                total_fee += f["fee"]
            else:
                lines.append("  GRVT: 未获取到成交记录")

            lines.append(f"  总开仓费用: ${total_fee:.4f}")
            lines.append("")
            print("\n".join(lines), flush=True)
        except Exception as e:
            logger.error(f"Entry cost report failed: {e}")

    async def print_exit_pnl(self, pre_h_pos: dict = None, pre_g_pos: dict = None,
                             pre_h_bal: dict = None, pre_g_bal: dict = None):
        """Print full PnL breakdown after closing positions.
        Works both with entry snapshot (same process) and without (cross-process).
        pre_*: position/balance snapshots taken BEFORE close_position()."""
        try:
            has_snapshot = self.state.entry_time > 0

            # Determine entry prices — from snapshot fills, or from pre-close position data
            h_entry_px = 0.0
            g_entry_px = 0.0
            h_size = 0.0
            g_size = 0.0

            if has_snapshot:
                entry_ms = int(self.state.entry_time * 1000) - 5000
                hold_secs = time.time() - self.state.entry_time
            else:
                # No snapshot — use 10min lookback for exit fills only
                entry_ms = int((time.time() - 600) * 1000)
                hold_secs = 0

            hours = int(hold_secs // 3600)
            mins = int((hold_secs % 3600) // 60)

            # Query recent fills (primarily to find exit fills)
            h_fills, g_fills = await self._both(
                lambda: self.hyena.get_fills(entry_ms),
                lambda: self.grvt.get_fills(limit=10),
            )
            if isinstance(h_fills, Exception):
                h_fills = []
            if isinstance(g_fills, Exception):
                g_fills = []

            # Exit fills
            h_exit_fills = [f for f in h_fills if f.get("dir") == "Close Long" or f.get("side") == "A"]
            g_exit_fills = [f for f in g_fills if f.get("is_buyer")]

            # Entry fills (from snapshot if available)
            if has_snapshot and self.state.entry_h_fills:
                h_entry_fills = [f for f in self.state.entry_h_fills if f.get("dir") == "Open Long" or f.get("side") == "B"]
            else:
                h_entry_fills = []
            if has_snapshot and self.state.entry_g_fills:
                g_entry_fills = [f for f in self.state.entry_g_fills if not f.get("is_buyer")]
            else:
                g_entry_fills = []

            # Entry prices — prefer snapshot fills, then position entry_px from exchange
            h_entry_px = (h_entry_fills[0]["px"] if h_entry_fills else
                          pre_h_pos.get("entry_px", 0) if pre_h_pos else
                          self.state.entry_h_mid)
            g_entry_px = (g_entry_fills[0]["px"] if g_entry_fills else
                          pre_g_pos.get("entry_px", 0) if pre_g_pos else
                          self.state.entry_g_mid)
            h_exit_px = h_exit_fills[0]["px"] if h_exit_fills else 0
            g_exit_px = g_exit_fills[0]["px"] if g_exit_fills else 0

            # Sizes
            h_size = abs(self.state.hyena.size) if self.state.hyena.size else (
                abs(pre_h_pos.get("size", 0)) if pre_h_pos else 0)
            g_size = abs(self.state.grvt.size) if self.state.grvt.size else (
                abs(pre_g_pos.get("size", 0)) if pre_g_pos else 0)

            # Position PnL
            h_pos_pnl = (h_exit_px - h_entry_px) * h_size if h_exit_px and h_entry_px else 0
            g_pos_pnl = (g_entry_px - g_exit_px) * g_size if g_exit_px and g_entry_px else 0
            pos_total = h_pos_pnl + g_pos_pnl

            # Funding PnL
            h_funding = 0.0
            if h_size > 0:
                try:
                    h_funding = await asyncio.to_thread(
                        self.hyena.get_accumulated_funding, entry_ms, h_size
                    )
                except Exception:
                    pass

            g_rpnl = sum(f.get("realized_pnl", 0) for f in g_exit_fills)

            # Fees
            h_entry_fee = sum(f["fee"] for f in h_entry_fills)
            g_entry_fee = sum(f["fee"] for f in g_entry_fills)
            h_exit_fee = sum(f["fee"] for f in h_exit_fills)
            g_exit_fee = sum(f["fee"] for f in g_exit_fills)
            fee_total = h_entry_fee + g_entry_fee + h_exit_fee + g_exit_fee

            # Balance change (NAV)
            h_bal_now, g_bal_now = await self._both(self.hyena.get_balance, self.grvt.get_balance)
            h_val_now = h_bal_now.get("account_value", 0) if isinstance(h_bal_now, dict) else 0
            g_val_now = g_bal_now.get("total_equity", 0) if isinstance(g_bal_now, dict) else 0
            nav_after = h_val_now + g_val_now

            if has_snapshot and self.state.entry_h_balance > 0:
                nav_before = self.state.entry_h_balance + self.state.entry_g_balance
            elif pre_h_bal and pre_g_bal:
                nav_before = (pre_h_bal.get("account_value", 0) if isinstance(pre_h_bal, dict) else 0) + \
                             (pre_g_bal.get("total_equity", 0) if isinstance(pre_g_bal, dict) else 0)
            else:
                nav_before = 0

            nav_change = nav_after - nav_before if nav_before else 0
            nav_pct = (nav_change / nav_before * 100) if nav_before > 0 else 0

            # Print report
            lines = []
            lines.append(f"\n{'═'*50}")
            lines.append(f"  平仓损益报告")
            lines.append(f"{'═'*50}")
            if hold_secs > 0:
                lines.append(f"\n  持仓时间: {hours}h {mins}m")

            lines.append(f"\n  ── 仓位损益 ──")
            if h_exit_px and h_entry_px:
                lines.append(f"    HyENA Long:  entry ${h_entry_px:,.0f} → exit ${h_exit_px:,.0f} = ${h_pos_pnl:+.4f}")
            elif h_entry_px:
                lines.append(f"    HyENA Long:  entry ${h_entry_px:,.0f} → (未获取exit价格)")
            if g_exit_px and g_entry_px:
                lines.append(f"    GRVT Short:  entry ${g_entry_px:,.1f} → exit ${g_exit_px:,.1f} = ${g_pos_pnl:+.4f}")
            elif g_entry_px:
                lines.append(f"    GRVT Short:  entry ${g_entry_px:,.1f} → (未获取exit价格)")
            lines.append(f"    仓位合计: ${pos_total:+.4f}")

            lines.append(f"\n  ── Funding 收益 ──")
            lines.append(f"    HyENA: ${h_funding:+.4f}")
            if g_rpnl:
                lines.append(f"    GRVT realized_pnl: ${g_rpnl:+.4f}")
            lines.append(f"    Funding 合计: ${h_funding:+.4f}")

            lines.append(f"\n  ── 交易费用 ──")
            lines.append(f"    开仓: HyENA ${h_entry_fee:.4f} + GRVT ${g_entry_fee:.4f} = ${h_entry_fee + g_entry_fee:.4f}")
            lines.append(f"    平仓: HyENA ${h_exit_fee:.4f} + GRVT ${g_exit_fee:.4f} = ${h_exit_fee + g_exit_fee:.4f}")
            lines.append(f"    费用合计: -${fee_total:.4f}")

            lines.append(f"\n  ── 总计 ──")
            if nav_before > 0:
                lines.append(f"    NAV变化: ${nav_before:,.2f} → ${nav_after:,.2f} = ${nav_change:+.4f}")
                lines.append(f"    收益率: {nav_pct:+.4f}%")
            else:
                lines.append(f"    当前NAV: ${nav_after:,.2f}")
                lines.append(f"    (无开仓NAV快照，无法计算收益率)")
            lines.append(f"{'═'*50}\n")
            print("\n".join(lines), flush=True)

        except Exception as e:
            logger.error(f"Exit PnL report failed: {e}")
            print(f"\n  ⚠ 损益报告生成失败: {e}", flush=True)

    # ── Mirror Close ───────────────────────────────────────────────────────

    async def check_mirror_close(self):
        """Called every 10s. Detects unexpected position changes → two-leg emergency close.

        Three modes:
        1. ERROR retry — mirror_close_active + ERROR state → backoff retry _emergency_close_all
        2. Normal detection — OPEN state → double-confirmation before triggering
        3. Skip — any other state or pending_trade
        """
        # ── Mode 1: ERROR retry (失败后持续重试，永不放弃) ──
        if self.state.state == PositionState.ERROR and self.state.mirror_close_active:
            elapsed = time.time() - self.state._retry_last_attempt
            base = self.mon_config.mirror_close_retry_base
            cap = self.mon_config.mirror_close_retry_max
            backoff = min(base * (2 ** self.state._retry_count), cap)
            if elapsed >= backoff:
                self.alerter.warning(f"Mirror Close重试 #{self.state._retry_count + 1} (退避{backoff}s)")
                await self._emergency_close_all()
            return

        # ── Mode 3: Skip ──
        if self.state.state != PositionState.OPEN or self.state.mirror_close_active or self.state.pending_trade:
            return

        # ── Mode 2: Normal detection with double-confirmation ──
        h_pos, g_pos = await self._poll_both_positions()
        if h_pos is None or g_pos is None:
            return  # poll failed, already logged

        threshold = self.mon_config.position_change_threshold
        now = time.time()

        # Detect anomaly on either leg
        anomaly_detected = False
        for leg, pos in [(self.state.hyena, h_pos), (self.state.grvt, g_pos)]:
            current = pos.get("size", 0)
            delta = abs(current - leg.last_known_size)
            if delta > threshold:
                anomaly_detected = True
                anomaly_info = {
                    "leg": leg.exchange,
                    "expected": leg.last_known_size,
                    "got": current,
                    "time": now,
                }
                break

        if not anomaly_detected:
            # Normal — update last_known and clear any pending anomaly
            if self.state._pending_anomaly:
                self.alerter.info(f"Mirror Close: 误报消除 (上次异常腿={self.state._pending_anomaly['leg']})")
                self.state._pending_anomaly = None
            for leg, pos in [(self.state.hyena, h_pos), (self.state.grvt, g_pos)]:
                leg.last_known_size = pos.get("size", 0)
                leg.last_poll_time = now
            return

        # First detection → record pending, wait for next poll
        if self.state._pending_anomaly is None:
            self.state._pending_anomaly = anomaly_info
            self.alerter.warning(
                f"{anomaly_info['leg']}仓位异常，等待确认... "
                f"({anomaly_info['expected']:+.4f} → {anomaly_info['got']:+.4f})"
            )
            return

        # Second detection (next poll) → re-poll to confirm
        self.alerter.warning("Mirror Close: 二次确认，重新查询仓位...")
        h_pos2, g_pos2 = await self._poll_both_positions()
        if h_pos2 is None or g_pos2 is None:
            return  # poll failed, keep pending_anomaly for next cycle

        # Check if anomaly persists
        still_anomalous = False
        for leg, pos in [(self.state.hyena, h_pos2), (self.state.grvt, g_pos2)]:
            current = pos.get("size", 0)
            delta = abs(current - leg.last_known_size)
            if delta > threshold:
                still_anomalous = True
                break

        if not still_anomalous:
            self.alerter.info("Mirror Close: 二次确认通过，异常已恢复，误报消除")
            self.state._pending_anomaly = None
            for leg, pos in [(self.state.hyena, h_pos2), (self.state.grvt, g_pos2)]:
                leg.last_known_size = pos.get("size", 0)
                leg.last_poll_time = time.time()
            return

        # Confirmed anomaly — trigger emergency close all
        self.alerter.emergency(
            f"Mirror Close确认! {self.state._pending_anomaly['leg']}仓位异变 "
            f"({self.state._pending_anomaly['expected']:+.4f} → {self.state._pending_anomaly['got']:+.4f})"
        )
        self.state._pending_anomaly = None
        await self._emergency_close_all()

    async def _poll_both_positions(self):
        """Poll both legs, return (h_pos, g_pos) or (None, None) on failure."""
        try:
            h_pos = await asyncio.to_thread(self.hyena.get_position)
        except Exception as e:
            self._poll_fails["hyena"] += 1
            if self._poll_fails["hyena"] >= self.mon_config.api_failure_threshold:
                self.alerter.critical(f"HyENA poll连续失败 {self._poll_fails['hyena']}x: {e}")
            return None, None

        try:
            g_pos = await asyncio.to_thread(self.grvt.get_position)
        except Exception as e:
            self._poll_fails["grvt"] += 1
            if self._poll_fails["grvt"] >= self.mon_config.api_failure_threshold:
                self.alerter.critical(f"GRVT poll连续失败 {self._poll_fails['grvt']}x: {e}")
            return None, None

        # Check for API-level errors (e.g. auth expired returning size=0)
        for name, pos in [("hyena", h_pos), ("grvt", g_pos)]:
            if pos.get("error"):
                self._poll_fails[name] += 1
                if self._poll_fails[name] >= self.mon_config.api_failure_threshold:
                    self.alerter.critical(f"{name} position查询异常(连续{self._poll_fails[name]}次): {pos['error']}")
                return None, None
            self._poll_fails[name] = 0

        return h_pos, g_pos

    async def _emergency_close_all(self):
        """ADL confirmed: query both legs' actual positions, close everything."""
        self.state.mirror_close_active = True

        h_pos, g_pos = await self._both(self.hyena.get_position, self.grvt.get_position)
        h_sz = h_pos.get("size", 0) if isinstance(h_pos, dict) else 0
        g_sz = g_pos.get("size", 0) if isinstance(g_pos, dict) else 0

        if abs(h_sz) < 0.001 and abs(g_sz) < 0.001:
            self.alerter.emergency("两腿都已无仓位，紧急全平完成")
            self.state.reset()
            return

        self.alerter.emergency(f"紧急全平: HyENA={h_sz:+.4f} GRVT={g_sz:+.4f}")

        # Close both legs concurrently (each with independent error handling)
        h_ok, g_ok = True, True
        try:
            results = await self._both(
                lambda: self.hyena.market_close(h_sz) if abs(h_sz) >= 0.001 else "no_pos",
                lambda: self.grvt.market_close(abs(g_sz), g_sz > 0) if abs(g_sz) >= 0.001 else "no_pos",
            )
            h_result, g_result = results
            if isinstance(h_result, Exception):
                h_ok = False
                self.alerter.emergency(f"HyENA平仓失败: {h_result}")
            if isinstance(g_result, Exception):
                g_ok = False
                self.alerter.emergency(f"GRVT平仓失败: {g_result}")
        except Exception as e:
            h_ok = g_ok = False
            self.alerter.emergency(f"紧急全平异常: {e}")

        if h_ok and g_ok:
            self.alerter.emergency("紧急全平成功 ✓")
            self.state.reset()
        else:
            # Partial or full failure → enter ERROR, keep retrying
            self.state.state = PositionState.ERROR
            self.state._retry_count += 1
            self.state._retry_last_attempt = time.time()
            self.alerter.emergency(
                f"紧急全平部分失败! 进入ERROR状态，将持续重试 "
                f"(第{self.state._retry_count}次, 下次退避"
                f"{min(self.mon_config.mirror_close_retry_base * (2 ** self.state._retry_count), self.mon_config.mirror_close_retry_max)}s)"
            )

    # ── Exit ───────────────────────────────────────────────────────────────

    async def close_position(self) -> bool:
        if self.state.state not in (PositionState.OPEN, PositionState.ERROR):
            self.alerter.warning(f"无法平仓: 状态={self.state.state.value}")
            return False

        self.state.state = PositionState.CLOSING
        self.state.pending_trade = True

        # Re-query live positions to avoid closing already-flat legs
        h_pos, g_pos = await self._both(self.hyena.get_position, self.grvt.get_position)
        h_sz = h_pos.get("size", 0) if isinstance(h_pos, dict) else self.state.hyena.size
        g_sz = g_pos.get("size", 0) if isinstance(g_pos, dict) else self.state.grvt.size

        if abs(h_sz) < 0.001 and abs(g_sz) < 0.001:
            self.alerter.info("双边已无持仓，无需平仓")
            self.state.reset()
            return True

        self.alerter.info(f"并发平仓: HyENA={h_sz:+.4f} GRVT={g_sz:+.4f}")
        results = await self._both(
            lambda: self.hyena.market_close(h_sz) if abs(h_sz) >= 0.001 else None,
            lambda: self.grvt.market_close(abs(g_sz), g_sz > 0) if abs(g_sz) >= 0.001 else None,
        )
        self.alerter.info(f"平仓结果: {results}")

        await asyncio.sleep(1)
        h_pos, g_pos = await self._both(self.hyena.get_position, self.grvt.get_position)
        h_rem = h_pos.get("size", 0) if not isinstance(h_pos, Exception) else 999
        g_rem = g_pos.get("size", 0) if not isinstance(g_pos, Exception) else 999

        if abs(h_rem) > 0.001 or abs(g_rem) > 0.001:
            self.alerter.warning(f"残留: HyENA={h_rem:.4f} GRVT={g_rem:.4f}")
        else:
            self.alerter.info("双边已完全平仓 ✓")

        self.state.reset()
        return True

    # ── Rebalance ──────────────────────────────────────────────────────────

    async def check_rebalance(self):
        if self.state.state != PositionState.OPEN:
            return

        try:
            h_pos, g_pos = await self._both(self.hyena.get_position, self.grvt.get_position)
            h_lev = h_pos.get("leverage_value", 0)
            g_lev = g_pos.get("leverage_value", 0)

            max_lev = self.config.max_leverage
            if h_lev > max_lev:
                self.alerter.warning(f"HyENA杠杆 {h_lev:.1f}x > {max_lev:.0f}x! 需追加保证金.")
            if g_lev > max_lev:
                self.alerter.warning(f"GRVT杠杆 {g_lev:.1f}x > {max_lev:.0f}x! 需追加保证金.")
            if abs(h_lev - g_lev) > self.config.leverage_diff_trigger:
                self.alerter.warning(f"杠杆差: HyENA={h_lev:.1f}x GRVT={g_lev:.1f}x. 建议调整.")
        except Exception as e:
            self.alerter.warning(f"Rebalance check error: {e}")
