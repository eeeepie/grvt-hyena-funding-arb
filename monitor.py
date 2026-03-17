"""
Monitoring: alerts, funding rate CSV logging, USDe peg, circuit breaker.
"""

import asyncio
import csv
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import MonitorConfig, AssetConfig, BTC_CONFIG
from exchange_clients import ANNUAL_MULTIPLIER


# ============================================================================
# Alerter — console + log file
# ============================================================================

class Alerter:
    def __init__(self, log_dir: str):
        Path(log_dir).mkdir(exist_ok=True)
        self.logger = logging.getLogger("alerts")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        for handler in [
            logging.FileHandler(f"{log_dir}/alerts.log"),
            logging.StreamHandler(),
        ]:
            handler.setFormatter(fmt)
            self.logger.addHandler(handler)

    def emergency(self, msg): self.logger.critical(f"🚨 EMERGENCY: {msg}")
    def critical(self, msg):  self.logger.critical(f"⚠️  CRITICAL: {msg}")
    def warning(self, msg):   self.logger.warning(f"WARNING: {msg}")
    def info(self, msg):      self.logger.info(msg)


# ============================================================================
# Monitor
# ============================================================================

class Monitor:
    def __init__(self, hyena_client, grvt_client, config: MonitorConfig, alerter: Alerter,
                 asset: AssetConfig = None):
        self.hyena = hyena_client
        self.grvt = grvt_client
        self.config = config
        self.alerter = alerter
        self.asset = asset or BTC_CONFIG
        self.data_dir = Path(config.data_dir)
        self.data_dir.mkdir(exist_ok=True)

        self.prices_1h: list[tuple[float, float]] = []
        self.circuit_breaker_active = False
        self.negative_funding_start = None
        self._csv_headers_written: set[str] = set()

    # --- Helper: run a task in a loop with error handling ---

    async def _run_loop(self, name: str, interval: int, fn):
        """Generic async loop: call fn(), sleep, repeat. Catches all errors."""
        self.alerter.info(f"{name} started (every {interval}s)")
        while True:
            try:
                await fn()
            except Exception as e:
                self.alerter.warning(f"{name} error: {e}")
            await asyncio.sleep(interval)

    # --- Funding Rates ---

    async def collect_funding_rates(self):
        await self._run_loop("Funding collector", self.config.funding_rate_interval,
                             self._collect_funding_once)

    async def _collect_funding_once(self):
        ts = datetime.now(timezone.utc)
        day = ts.strftime("%Y%m%d")

        h = self.hyena.get_funding_rate()
        g = self.grvt.get_funding_rate()

        h8 = h.get("funding_8h", 0)
        g8 = g.get("funding_8h_decimal", 0)
        # Direction-aware spread
        if self.asset.hyena_is_buy:
            spread = g8 - h8  # Long HyENA + Short GRVT
        else:
            spread = h8 - g8  # Short HyENA + Long GRVT
        spread_ann = spread * ANNUAL_MULTIPLIER

        # CSV — asset-specific filename
        prefix = f"funding_{self.asset.name.lower()}" if self.asset.name != "BTC" else "funding"
        csv_path = self.data_dir / f"{prefix}_{day}.csv"
        write_header = day not in self._csv_headers_written
        with open(csv_path, "a", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["timestamp", "hyena_8h", "hyena_ann", "grvt_8h", "grvt_ann", "spread_8h", "spread_ann"])
                self._csv_headers_written.add(day)
            w.writerow([ts.isoformat(),
                        f"{h8:.8f}", f"{h.get('annual_pct',0):.2f}",
                        f"{g8:.8f}", f"{g.get('annual_pct',0):.2f}",
                        f"{spread:.8f}", f"{spread_ann:.2f}"])

        pred_ann = h.get("predicted_ann", 0)
        self.alerter.info(
            f"Funding | HyENA: settled {h8:+.6f}/8h ({h.get('annual_pct',0):+.1f}%) "
            f"predicted ({pred_ann:+.1f}%) "
            f"| GRVT: {g8:+.6f}/8h ({g.get('annual_pct',0):+.1f}%) "
            f"| Spread(settled): {spread_ann:+.1f}% ann"
        )

        # Sustained negative spread alert
        if spread_ann < self.config.funding_negative_threshold_annual:
            if self.negative_funding_start is None:
                self.negative_funding_start = ts
            else:
                hrs = (ts - self.negative_funding_start).total_seconds() / 3600
                if hrs >= self.config.funding_negative_duration_hours:
                    self.alerter.warning(f"Funding spread negative for {hrs:.1f}h! {spread_ann:.1f}% ann")
        else:
            self.negative_funding_start = None

    # --- USDe Peg ---

    async def monitor_usde_peg(self):
        await self._run_loop("USDe peg monitor", self.config.usde_peg_interval,
                             self._check_usde_once)

    async def _check_usde_once(self):
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "ethena-usde", "vs_currencies": "usd"}, timeout=10,
        )
        if resp.status_code == 429:
            return  # rate limited, skip
        if resp.status_code != 200:
            self.alerter.warning(f"USDe API returned {resp.status_code}")
            return

        price = resp.json().get("ethena-usde", {}).get("usd", 1.0)
        dev = abs(price - 1.0)

        if dev > self.config.usde_depeg_exit:
            self.alerter.emergency(f"USDe severe depeg! ${price:.4f} (deviation {dev*100:.2f}% > 1%) — close positions immediately!")
        elif dev > self.config.usde_depeg_warning:
            self.alerter.critical(f"USDe depeg warning: ${price:.4f} (deviation {dev*100:.2f}% > 0.5%)")

    # --- Margin Ratio (MMR) ---

    async def monitor_margin_ratio(self):
        await self._run_loop("MMR monitor", self.config.position_poll_interval,
                             self._check_mmr_once)

    async def _check_mmr_once(self):
        h_mmr = self.hyena.get_margin_ratio()
        g_mmr = self.grvt.get_margin_ratio()

        h_pct = h_mmr.get("mmr_pct", 0)
        g_pct = g_mmr.get("mmr_pct", 0)

        # Skip if no positions (both MMR = 0)
        if h_pct == 0 and g_pct == 0:
            return

        worst = max(h_pct, g_pct)
        worst_leg = "HyENA" if h_pct >= g_pct else "GRVT"

        if worst >= self.config.mmr_emergency_pct:
            self.alerter.emergency(
                f"MMR CRITICAL! {worst_leg} at {worst:.1f}% "
                f"(HyENA: {h_pct:.1f}%, GRVT: {g_pct:.1f}%). "
                f"Liquidation imminent — close positions NOW!"
            )
        elif worst >= self.config.mmr_warning_pct:
            self.alerter.critical(
                f"MMR WARNING: {worst_leg} at {worst:.1f}% "
                f"(HyENA: {h_pct:.1f}%, GRVT: {g_pct:.1f}%)"
            )
        else:
            self.alerter.info(
                f"MMR | HyENA: {h_pct:.1f}% (${h_mmr.get('maintenance_margin',0):,.2f} / "
                f"${h_mmr.get('equity',0):,.2f}) | "
                f"GRVT: {g_pct:.1f}% (${g_mmr.get('maintenance_margin',0):,.2f} / "
                f"${g_mmr.get('equity',0):,.2f})"
            )

    # --- Circuit Breaker ---

    async def check_circuit_breaker(self):
        await self._run_loop("Circuit breaker", self.config.book_price_interval,
                             self._check_circuit_once)

    async def _check_circuit_once(self):
        mid = self.hyena.get_mid_price()
        now = time.time()
        self.prices_1h.append((now, mid))
        self.prices_1h = [(t, p) for t, p in self.prices_1h if now - t < 3600]

        if len(self.prices_1h) < 2:
            return

        oldest = self.prices_1h[0][1]
        if oldest <= 0:
            return

        move = abs(mid - oldest) / oldest
        if move > self.config.circuit_breaker_pct and not self.circuit_breaker_active:
            self.alerter.critical(f"Circuit Breaker! {self.asset.name} 1h move {move*100:.1f}% > 15%. Automation paused.")
            self.circuit_breaker_active = True
        elif move <= self.config.circuit_breaker_pct and self.circuit_breaker_active:
            self.alerter.info("Circuit Breaker lifted")
            self.circuit_breaker_active = False

    # --- Status Report ---

    def print_status(self, hyena_pos: dict, grvt_pos: dict):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        sep = "=" * 60
        asset_name = self.asset.name
        flat_thresh = self.asset.flat_threshold

        h_sz = abs(hyena_pos.get("size", 0))
        g_sz = abs(grvt_pos.get("size", 0))
        delta = abs(h_sz - g_sz)
        delta_str = "MATCHED" if delta < flat_thresh else f"MISMATCH ({delta:.4f} {asset_name})"

        lines = [
            f"\n{sep}", f"  STATUS — {now}", sep,
            f"\n  HyENA ({self.asset.hyena_coin}):",
            f"    Size: {hyena_pos.get('size',0):.4f} {asset_name} | Entry: ${hyena_pos.get('entry_px',0):,.2f}",
            f"    PnL: ${hyena_pos.get('unrealized_pnl',0):,.2f} | Lev: {hyena_pos.get('leverage_value',0):.1f}x",
            f"\n  GRVT ({self.asset.grvt_instrument}):",
            f"    Size: {grvt_pos.get('size',0):.4f} {asset_name} | Entry: ${grvt_pos.get('entry_px',0):,.2f}",
            f"    PnL: ${grvt_pos.get('unrealized_pnl',0):,.2f} | Lev: {grvt_pos.get('leverage_value',0):.1f}x",
            f"\n  Delta: {delta_str}",
        ]

        # Rates
        try:
            h = self.hyena.get_funding_rate()
            g = self.grvt.get_funding_rate()
            # Direction-dependent spread (must match cmd_rates logic)
            if self.asset.hyena_is_buy:
                spread_settled = g.get("funding_8h_decimal", 0) - h.get("funding_8h", 0)
                spread_pred = g.get("funding_8h_decimal", 0) - h.get("predicted_8h", 0)
            else:
                spread_settled = h.get("funding_8h", 0) - g.get("funding_8h_decimal", 0)
                spread_pred = h.get("predicted_8h", 0) - g.get("funding_8h_decimal", 0)
            lines += [
                f"\n  Rates (settled / predicted):",
                f"    HyENA settled: {h.get('funding_8h',0):+.6f}/8h ({h.get('annual_pct',0):+.1f}%)",
                f"    HyENA predict: {h.get('predicted_8h',0):+.6f}/8h ({h.get('predicted_ann',0):+.1f}%)",
                f"    GRVT:          {g.get('funding_8h_decimal',0):+.6f}/8h ({g.get('annual_pct',0):+.1f}%)",
                f"    Spread(settled): {spread_settled * ANNUAL_MULTIPLIER:+.1f}% ann",
                f"    Spread(predict): {spread_pred * ANNUAL_MULTIPLIER:+.1f}% ann",
                f"    Note: USDe 12% + GRVT 10% rewards are NOT included in the rates above",
            ]
        except Exception:
            pass

        # MMR
        try:
            h_mmr = self.hyena.get_margin_ratio()
            g_mmr = self.grvt.get_margin_ratio()
            h_pct = h_mmr.get("mmr_pct", 0)
            g_pct = g_mmr.get("mmr_pct", 0)
            if h_pct > 0 or g_pct > 0:
                lines += [
                    f"\n  Margin Ratio (MMR):",
                    f"    HyENA: {h_pct:.1f}% (maint ${h_mmr.get('maintenance_margin',0):,.2f} / equity ${h_mmr.get('equity',0):,.2f})",
                    f"    GRVT:  {g_pct:.1f}% (maint ${g_mmr.get('maintenance_margin',0):,.2f} / equity ${g_mmr.get('equity',0):,.2f})",
                    f"    Warning: {self.config.mmr_warning_pct:.0f}% | Emergency: {self.config.mmr_emergency_pct:.0f}%",
                ]
        except Exception:
            pass

        lines += [
            f"\n  Circuit Breaker: {'ACTIVE' if self.circuit_breaker_active else 'OFF'}",
            sep,
        ]
        print("\n".join(lines))
