"""
Strategy Engine: entry, exit, mirror close, rebalance.
Long HyENA + Short GRVT, delta-neutral.
"""

import asyncio
import json
import os
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from config import StrategyConfig, MonitorConfig

STATE_FILE = os.path.join(os.path.dirname(__file__), "data", "entry_state.json")

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
        self.clear_entry_state()

    def save_entry_state(self):
        """Persist entry snapshot to disk for cross-process PnL reporting."""
        data = {
            "entry_time": self.entry_time,
            "entry_h_balance": self.entry_h_balance,
            "entry_g_balance": self.entry_g_balance,
            "entry_h_mid": self.entry_h_mid,
            "entry_g_mid": self.entry_g_mid,
            "entry_h_fills": self.entry_h_fills,
            "entry_g_fills": self.entry_g_fills,
            "h_size": self.hyena.size,
            "g_size": self.grvt.size,
            "h_entry_px": self.hyena.entry_px,
            "g_entry_px": self.grvt.entry_px,
        }
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Entry state saved to {STATE_FILE}")

    def load_entry_state(self) -> bool:
        """Load entry snapshot from disk. Returns True if loaded."""
        if not os.path.exists(STATE_FILE):
            return False
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            self.entry_time = data["entry_time"]
            self.entry_h_balance = data["entry_h_balance"]
            self.entry_g_balance = data["entry_g_balance"]
            self.entry_h_mid = data["entry_h_mid"]
            self.entry_g_mid = data["entry_g_mid"]
            self.entry_h_fills = data.get("entry_h_fills", [])
            self.entry_g_fills = data.get("entry_g_fills", [])
            logger.info(f"Entry state loaded from {STATE_FILE}")
            return True
        except Exception as e:
            logger.warning(f"Failed to load entry state: {e}")
            return False

    @staticmethod
    def clear_entry_state():
        """Remove entry state file after close."""
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)

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

    @staticmethod
    def _calc_vwap(fills: list) -> float:
        """VWAP from fills: Σ(px × sz) / Σ(sz). Returns 0 if no fills."""
        total_notional = sum(f["px"] * f["sz"] for f in fills if f.get("px") and f.get("sz"))
        total_sz = sum(f["sz"] for f in fills if f.get("sz"))
        return total_notional / total_sz if total_sz > 0 else 0.0

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

        # 3–5. Submit orders with retry (hyna:BTC liquidity can be thin)
        self.state.pending_trade = True
        max_entry_retries = 3
        for attempt in range(max_entry_retries):
            # Re-query book on retry to get fresh prices
            if attempt > 0:
                self.alerter.info(f"开仓重试 {attempt}/{max_entry_retries - 1}: 重新获取订单簿...")
                await asyncio.sleep(2)
                h_book, g_book = await self._both(self.hyena.get_book, self.grvt.get_book)
                if isinstance(h_book, Exception) or isinstance(g_book, Exception):
                    self.alerter.warning(f"订单簿获取失败，继续重试...")
                    continue

            # Price offset: 5bps first try, 10bps on retry (hyna:BTC book can be thin)
            offset_bps = self.config.aggressive_limit_offset_bps if attempt == 0 else (self.config.aggressive_limit_offset_bps * 2)
            offset = offset_bps / 10000
            buy_px = round(h_book["mid"] * (1 + offset), 0)   # HyENA tick = $1
            sell_px = round(g_book["mid"] * (1 - offset), 1)   # GRVT tick = $0.1

            self.alerter.info(
                f"并发下单{f' (重试{attempt})' if attempt else ''}: "
                f"HyENA BUY {qty}@${buy_px:,.0f} | GRVT SELL {qty}@${sell_px:,.1f}"
            )

            h_res, g_res = await self._both(
                lambda: self.hyena.place_order(True, qty, buy_px),
                lambda: self.grvt.place_order(False, qty, sell_px),
            )

            h_ok = not isinstance(h_res, Exception)
            g_ok = not isinstance(g_res, Exception)

            # Both succeeded → verify and record
            if h_ok and g_ok:
                self.alerter.info(f"HyENA: {h_res}")
                self.alerter.info(f"GRVT:  {g_res}")
                await asyncio.sleep(1)

                h_pos, g_pos = await self._both(self.hyena.get_position, self.grvt.get_position)
                self.state.load_from_exchange(h_pos, g_pos)
                self.state.pending_trade = False
                self.alerter.info(f"仓位开启! HyENA:{self.state.hyena.size:+.4f} GRVT:{self.state.grvt.size:+.4f}")

                # Record entry snapshot
                entry_ms = int(time.time() * 1000) - 30000
                self.state.entry_time = time.time()
                self.state.entry_h_mid = h_book["mid"]
                self.state.entry_g_mid = g_book["mid"]
                self.state.entry_h_balance = h_bal.get("account_value", 0) if isinstance(h_bal, dict) else 0
                self.state.entry_g_balance = g_bal.get("total_equity", 0) if isinstance(g_bal, dict) else 0

                await self._print_entry_costs(entry_ms, h_book["mid"], g_book["mid"])
                self.state.save_entry_state()

                if abs(abs(self.state.hyena.size) - abs(self.state.grvt.size)) > self.config.qty_mismatch_threshold:
                    self.alerter.warning("BTC数量不匹配!")
                return True

            # One side failed — if it's a retryable IOC rejection on HyENA, and GRVT
            # also failed or was not filled, we can retry both.
            # But if GRVT succeeded and HyENA failed, must unwind GRVT first.

            if h_ok and not g_ok:
                self.alerter.emergency(f"GRVT失败! {g_res}. 正在关闭HyENA...")
                try:
                    await asyncio.to_thread(self.hyena.market_close, qty)
                except Exception as e:
                    self.alerter.emergency(f"HyENA关仓也失败: {e}")
                break  # Don't retry — one side was filled + unwound

            if g_ok and not h_ok:
                # Check if GRVT actually filled (status might be PENDING with traded_size=0)
                g_filled = self._check_grvt_filled(g_res)
                if g_filled:
                    self.alerter.emergency(f"HyENA失败! {h_res}. GRVT已成交，正在关闭GRVT...")
                    try:
                        await asyncio.to_thread(self.grvt.market_close, qty, False)
                    except Exception as e:
                        self.alerter.emergency(f"GRVT关仓也失败: {e}")
                    break  # Don't retry — GRVT was filled + unwound
                else:
                    # GRVT was PENDING/not filled — can retry both
                    h_err_msg = str(h_res) if isinstance(h_res, Exception) else ""
                    is_ioc_reject = "could not immediately match" in h_err_msg.lower()
                    if is_ioc_reject and attempt < max_entry_retries - 1:
                        self.alerter.warning(f"HyENA IOC 未成交 (book可能为空), GRVT未成交 → 重试")
                        continue
                    else:
                        self.alerter.critical(f"HyENA失败: {h_res}")
                        break

            # Both failed
            if not h_ok and not g_ok:
                h_err_msg = str(h_res) if isinstance(h_res, Exception) else ""
                is_ioc_reject = "could not immediately match" in h_err_msg.lower()
                if is_ioc_reject and attempt < max_entry_retries - 1:
                    self.alerter.warning(f"双边都未成交 → 重试")
                    continue
                self.alerter.critical(f"双边都失败: {h_res} / {g_res}")
                break

        self.state.reset()
        return False

    @staticmethod
    def _check_grvt_filled(res) -> bool:
        """Check if GRVT order response indicates actual fill (not just PENDING)."""
        if isinstance(res, Exception):
            return False
        if not isinstance(res, dict):
            return False
        result = res.get("result", {})
        state = result.get("state", {})
        status = state.get("status", "")
        traded = state.get("traded_size", ["0.0"])
        # FILLED or traded_size > 0 means it actually executed
        if status == "FILLED":
            return True
        try:
            return any(float(s) > 0 for s in traded)
        except (ValueError, TypeError):
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
                             pre_h_bal: dict = None, pre_g_bal: dict = None,
                             pre_h_mid: float = 0, pre_g_mid: float = 0):
        """Print professional PnL attribution report after closing positions.
        pre_h_mid / pre_g_mid: book mid prices captured BEFORE close orders."""
        try:
            has_snapshot = self.state.entry_time > 0

            if has_snapshot:
                entry_ms = int(self.state.entry_time * 1000) - 5000
                hold_secs = time.time() - self.state.entry_time
            else:
                entry_ms = int((time.time() - 600) * 1000)
                hold_secs = 0

            holding_days = hold_secs / 86400

            # ── Gather fills ──
            h_fills, g_fills = await self._both(
                lambda: self.hyena.get_fills(entry_ms),
                lambda: self.grvt.get_fills(limit=20),
            )
            if isinstance(h_fills, Exception):
                h_fills = []
            if isinstance(g_fills, Exception):
                g_fills = []

            h_exit_fills = [f for f in h_fills if f.get("dir") == "Close Long" or f.get("side") == "A"]
            g_exit_fills = [f for f in g_fills if f.get("is_buyer")]

            if has_snapshot and self.state.entry_h_fills:
                h_entry_fills = [f for f in self.state.entry_h_fills if f.get("dir") == "Open Long" or f.get("side") == "B"]
            else:
                h_entry_fills = []
            if has_snapshot and self.state.entry_g_fills:
                g_entry_fills = [f for f in self.state.entry_g_fills if not f.get("is_buyer")]
            else:
                g_entry_fills = []

            # ── Prices (VWAP preferred, fallback to first fill / position entry_px) ──
            h_entry_px = (self._calc_vwap(h_entry_fills) if h_entry_fills else
                          pre_h_pos.get("entry_px", 0) if pre_h_pos else
                          self.state.entry_h_mid)
            g_entry_px = (self._calc_vwap(g_entry_fills) if g_entry_fills else
                          pre_g_pos.get("entry_px", 0) if pre_g_pos else
                          self.state.entry_g_mid)
            h_exit_px = self._calc_vwap(h_exit_fills) if h_exit_fills else 0
            g_exit_px = self._calc_vwap(g_exit_fills) if g_exit_fills else 0

            # Entry mid prices (for slippage calc)
            entry_h_mid = self.state.entry_h_mid if has_snapshot else h_entry_px
            entry_g_mid = self.state.entry_g_mid if has_snapshot else g_entry_px

            # ── Sizes ──
            h_size = abs(self.state.hyena.size) if self.state.hyena.size else (
                abs(pre_h_pos.get("size", 0)) if pre_h_pos else 0)
            g_size = abs(self.state.grvt.size) if self.state.grvt.size else (
                abs(pre_g_pos.get("size", 0)) if pre_g_pos else 0)
            avg_size = (h_size + g_size) / 2

            # ── Position PnL ──
            h_pos_pnl = (h_exit_px - h_entry_px) * h_size if h_exit_px and h_entry_px else 0
            g_pos_pnl = (g_entry_px - g_exit_px) * g_size if g_exit_px and g_entry_px else 0
            pos_total = h_pos_pnl + g_pos_pnl

            # ── Funding PnL ──
            h_funding = 0.0
            if h_size > 0:
                try:
                    h_funding = await asyncio.to_thread(
                        self.hyena.get_accumulated_funding, entry_ms, h_size
                    )
                except Exception:
                    pass

            g_rpnl = sum(f.get("realized_pnl", 0) for f in g_exit_fills)
            funding_total = h_funding + g_rpnl

            # ── Fees ──
            h_entry_fee = sum(f["fee"] for f in h_entry_fills)
            g_entry_fee = sum(f["fee"] for f in g_entry_fills)
            h_exit_fee = sum(f["fee"] for f in h_exit_fills)
            g_exit_fee = sum(f["fee"] for f in g_exit_fills)
            fee_total = h_entry_fee + g_entry_fee + h_exit_fee + g_exit_fee

            # ── Slippage ──
            def _slip_bps(fill_px, mid_px):
                return abs(fill_px - mid_px) / mid_px * 10000 if mid_px else 0

            h_entry_slip = _slip_bps(h_entry_px, entry_h_mid) if h_entry_fills and entry_h_mid else 0
            g_entry_slip = _slip_bps(g_entry_px, entry_g_mid) if g_entry_fills and entry_g_mid else 0
            h_exit_slip = _slip_bps(h_exit_px, pre_h_mid) if h_exit_fills and pre_h_mid else 0
            g_exit_slip = _slip_bps(g_exit_px, pre_g_mid) if g_exit_fills and pre_g_mid else 0

            # Slippage cost in USD: |fill - mid| * size per leg
            h_entry_slip_usd = abs(h_entry_px - entry_h_mid) * h_size if h_entry_fills and entry_h_mid else 0
            g_entry_slip_usd = abs(g_entry_px - entry_g_mid) * g_size if g_entry_fills and entry_g_mid else 0
            h_exit_slip_usd = abs(h_exit_px - pre_h_mid) * h_size if h_exit_fills and pre_h_mid else 0
            g_exit_slip_usd = abs(g_exit_px - pre_g_mid) * g_size if g_exit_fills and pre_g_mid else 0
            slip_total = h_entry_slip_usd + g_entry_slip_usd + h_exit_slip_usd + g_exit_slip_usd

            # ── NAV ──
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

            # ── Net PnL ──
            net_pnl = funding_total + pos_total - fee_total - slip_total

            # ── Reward estimates ──
            h_notional = h_size * h_entry_px if h_entry_px else 0
            g_notional = g_size * g_entry_px if g_entry_px else 0
            usde_reward = h_notional * (self.config.usde_reward_apr / 100) * (holding_days / 365) if holding_days > 0 else 0
            grvt_reward = g_notional * (self.config.grvt_reward_apr / 100) * (holding_days / 365) if holding_days > 0 else 0
            reward_total = usde_reward + grvt_reward

            # ── APR ──
            trade_apr = (net_pnl / nav_before) * (365 / holding_days) * 100 if nav_before > 0 and holding_days > 0 else 0
            total_apr = ((net_pnl + reward_total) / nav_before) * (365 / holding_days) * 100 if nav_before > 0 and holding_days > 0 else 0

            # ── Attribution percentages (relative to |net_pnl| baseline or sum of absolute components) ──
            abs_sum = abs(funding_total) + abs(pos_total) + fee_total + slip_total
            def _pct(val):
                return val / abs_sum * 100 if abs_sum > 0 else 0

            # ── Leverage at entry ──
            h_equity_entry = self.state.entry_h_balance if has_snapshot else (
                pre_h_bal.get("account_value", 0) if isinstance(pre_h_bal, dict) else 0)
            g_equity_entry = self.state.entry_g_balance if has_snapshot else (
                pre_g_bal.get("total_equity", 0) if isinstance(pre_g_bal, dict) else 0)
            h_lev = h_notional / h_equity_entry if h_equity_entry > 0 else 0
            g_lev = g_notional / g_equity_entry if g_equity_entry > 0 else 0

            # ── Print report ──
            W = 54
            lines = []
            lines.append(f"\n{'═' * W}")
            lines.append(f"  平仓损益报告")
            lines.append(f"{'═' * W}")

            # Holding period
            if hold_secs > 0:
                days = int(holding_days)
                hours = int((hold_secs % 86400) // 3600)
                mins = int((hold_secs % 3600) // 60)
                hold_str = f"{days}d {hours}h {mins}m" if days else f"{hours}h {mins}m"
                lines.append(f"\n  持仓: {hold_str}")

            # Position summary
            lines.append(f"  仓位: Long hyna:BTC / Short BTC_USDT_Perp")
            lines.append(f"  数量: {avg_size:.5f} BTC (≈ ${avg_size * (h_entry_px or g_entry_px):,.0f}/腿)")
            if h_lev > 0 or g_lev > 0:
                lines.append(f"  杠杆: HyENA {h_lev:.1f}x / GRVT {g_lev:.1f}x")

            # PnL Attribution
            lines.append(f"\n  ── PnL 归因 ──")

            lines.append(f"    Funding Income:          {funding_total:+10.2f}     ({_pct(funding_total):+5.1f}%)")
            lines.append(f"      HyENA (long):              {h_funding:+.2f}")
            lines.append(f"      GRVT realized_pnl:         {g_rpnl:+.2f}  (含funding结算)")

            lines.append(f"    Position PnL:            {pos_total:+10.2f}     ({_pct(pos_total):+5.1f}%)")
            if h_entry_px and h_exit_px:
                lines.append(f"      HyENA: ${h_entry_px:,.0f} → ${h_exit_px:,.0f}    {h_pos_pnl:+.2f}")
            if g_entry_px and g_exit_px:
                lines.append(f"      GRVT:  ${g_entry_px:,.1f} → ${g_exit_px:,.1f}    {g_pos_pnl:+.2f}")

            lines.append(f"    Trading Fees:            {-fee_total:+10.2f}     ({_pct(-fee_total):+5.1f}%)")
            lines.append(f"      开仓: H ${h_entry_fee:.2f} + G ${g_entry_fee:.2f}")
            lines.append(f"      平仓: H ${h_exit_fee:.2f} + G ${g_exit_fee:.2f}")

            lines.append(f"    Slippage:                {-slip_total:+10.2f}     ({_pct(-slip_total):+5.1f}%)")
            lines.append(f"      开仓: H {h_entry_slip:.1f}bps / G {g_entry_slip:.1f}bps")
            lines.append(f"      平仓: H {h_exit_slip:.1f}bps / G {g_exit_slip:.1f}bps")

            lines.append(f"    {'─' * 37}")
            lines.append(f"    实际 NET PnL:           {net_pnl:+10.2f}")

            # External rewards
            if holding_days > 0:
                lines.append(f"\n  ── External Rewards (估算) ──")
                lines.append(f"    USDe staking ({self.config.usde_reward_apr:.0f}% APR):  {usde_reward:+.2f}")
                lines.append(f"    GRVT equity ({self.config.grvt_reward_apr:.0f}% APR):   {grvt_reward:+.2f}")
                lines.append(f"    Reward 合计:             {reward_total:+.2f}")

            # Annualized returns
            if nav_before > 0 and holding_days > 0:
                nav_change = nav_after - nav_before
                nav_pct = nav_change / nav_before * 100
                lines.append(f"\n  ── 年化收益 ──")
                lines.append(f"    NAV: ${nav_before:,.2f} → ${nav_after:,.2f}  ({nav_pct:+.3f}%)")
                lines.append(f"    APR (纯交易):     {trade_apr:+.1f}%")
                lines.append(f"    APR (含rewards):  {total_apr:+.1f}%")
            elif nav_before == 0:
                lines.append(f"\n  (无开仓NAV快照，无法计算年化)")

            lines.append(f"{'═' * W}\n")
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

        h_result, g_result = results
        if isinstance(h_result, Exception):
            self.alerter.warning(f"HyENA平仓异常: {h_result}")
        else:
            self.alerter.info(f"HyENA平仓: {h_result}")
        if isinstance(g_result, Exception):
            self.alerter.warning(f"GRVT平仓异常: {g_result}")
        else:
            self.alerter.info(f"GRVT平仓: {g_result}")

        # Verify + retry residual (up to 2 retries per leg)
        all_closed = False
        for retry in range(3):
            await asyncio.sleep(2)
            h_pos, g_pos = await self._both(self.hyena.get_position, self.grvt.get_position)
            h_rem = h_pos.get("size", 0) if not isinstance(h_pos, Exception) else 999
            g_rem = g_pos.get("size", 0) if not isinstance(g_pos, Exception) else 999

            if abs(h_rem) < 0.001 and abs(g_rem) < 0.001:
                self.alerter.info("双边已完全平仓 ✓")
                all_closed = True
                break

            if retry < 2:
                self.alerter.warning(f"残留仓位 (retry {retry + 1}/2): HyENA={h_rem:.4f} GRVT={g_rem:.4f}")
                # Retry only the failed leg(s) sequentially
                if abs(h_rem) >= 0.001:
                    try:
                        r = await asyncio.to_thread(self.hyena.market_close, h_rem)
                        self.alerter.info(f"HyENA重试: {r}")
                    except Exception as e:
                        self.alerter.warning(f"HyENA重试失败: {e}")
                if abs(g_rem) >= 0.001:
                    try:
                        r = await asyncio.to_thread(
                            self.grvt.market_close, abs(g_rem), g_rem > 0
                        )
                        self.alerter.info(f"GRVT重试: {r}")
                    except Exception as e:
                        self.alerter.warning(f"GRVT重试失败: {e}")

        if not all_closed:
            self.alerter.critical(
                f"平仓不完整! 残留: HyENA={h_rem:.4f} GRVT={g_rem:.4f} — 请手动处理"
            )

        self.state.reset()
        return all_closed

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
