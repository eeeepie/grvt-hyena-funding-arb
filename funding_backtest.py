#!/usr/bin/env python3
"""
Funding Rate Backtest: HyENA vs GRVT (any trading pair)
========================================================================
Run:
  python3 funding_backtest.py              # HYPE (default)
  python3 funding_backtest.py --asset BTC  # BTC
  python3 funding_backtest.py --asset ETH  # any pair (see --help)

Analyzes full funding history for both directions:
  Strategy A: Long HyENA + Short GRVT
  Strategy B: Short HyENA + Long GRVT

Data sources:
  HyENA: POST api.hyperliquid.xyz/info  {"type": "fundingHistory", "coin": "hyna:XXX"}
  GRVT:  POST market-data.grvt.io/full/v1/funding  {"instrument": "XXX_USDT_Perp"}

Adding a new trading pair:
  1. Add an entry to ASSET_PRESETS below with:
     - hyena_coin: the HyENA coin name (e.g. "hyna:SOL")
     - grvt_instrument: GRVT instrument (e.g. "SOL_USDT_Perp")
     - grvt_funding_hours: GRVT funding interval (check via ticker API)
     - grvt_points_multiplier: "1x" for BTC/ETH, "5x" for altcoins
  2. Or pass --hyena-coin and --grvt-instrument directly for unlisted pairs
"""

import argparse
import requests
from datetime import datetime, timezone, timedelta

# ============================================================================
# ASSET PRESETS — add new pairs here
# ============================================================================

ASSET_PRESETS = {
    "BTC": {
        "hyena_coin": "hyna:BTC",
        "grvt_instrument": "BTC_USDT_Perp",
        "grvt_funding_hours": 8,
        "grvt_points_multiplier": "1x",
    },
    "HYPE": {
        "hyena_coin": "hyna:HYPE",
        "grvt_instrument": "HYPE_USDT_Perp",
        "grvt_funding_hours": 4,
        "grvt_points_multiplier": "5x",
    },
    "ETH": {
        "hyena_coin": "hyna:ETH",
        "grvt_instrument": "ETH_USDT_Perp",
        "grvt_funding_hours": 8,
        "grvt_points_multiplier": "1x",
    },
    "SOL": {
        "hyena_coin": "hyna:SOL",
        "grvt_instrument": "SOL_USDT_Perp",
        "grvt_funding_hours": 8,
        "grvt_points_multiplier": "5x",
    },
}

# ============================================================================
# CONSTANTS & HELPERS
# ============================================================================

HL_API = "https://api.hyperliquid.xyz/info"
GRVT_API = "https://market-data.grvt.io/full/v1"


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def rate_to_annual(rate_8h_decimal):
    """Convert 8h funding rate (decimal) to annualized percentage."""
    return rate_8h_decimal * 3 * 365 * 100


# ============================================================================
# DATA FETCHING
# ============================================================================

def fetch_hyena_history(coin, days=180):
    """Fetch HyENA funding history (1h intervals).
    Paginates through API (500 records per call) to get full history."""
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    all_raw = []

    while True:
        resp = requests.post(HL_API, json={
            "type": "fundingHistory", "coin": coin, "startTime": start_ms
        }, timeout=15)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_raw.extend(batch)
        start_ms = batch[-1]["time"] + 1
        if len(batch) < 500:
            break

    records = []
    for e in all_raw:
        rate_1h = safe_float(e["fundingRate"])
        hour_ts = int(e["time"] / 1000)
        hour_ts = hour_ts - (hour_ts % 3600)
        records.append({
            "ts": datetime.fromtimestamp(e["time"] / 1000, tz=timezone.utc),
            "hour_ts": hour_ts,
            "rate_1h": rate_1h,
            "rate_8h": rate_1h * 8,
        })
    return records


def fetch_grvt_history(instrument, funding_hours):
    """Fetch GRVT historical funding rates.
    Endpoint: POST /full/v1/funding with pagination via cursor.
    funding_hours: expected interval (4 for HYPE, 8 for BTC).
    Returns (records, actual_funding_hours)."""
    all_records = []
    cursor = None

    for _ in range(60):
        payload = {"instrument": instrument, "limit": 500}
        if cursor:
            payload["cursor"] = cursor
        resp = requests.post(f"{GRVT_API}/funding", json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("result", [])
        if not batch:
            break
        all_records.extend(batch)
        cursor = data.get("next")
        if not cursor:
            break

    # Auto-detect interval from most recent record if preset might be wrong
    if all_records:
        latest_interval = all_records[0].get("funding_interval_hours", 0)
        if latest_interval > 0 and latest_interval != funding_hours:
            print(f"  Note: GRVT actual interval is {latest_interval}h (preset said {funding_hours}h), using {latest_interval}h")
            funding_hours = latest_interval

    # Filter to expected interval only (old data may have 0h/minute intervals)
    valid = [r for r in all_records if r.get("funding_interval_hours") == funding_hours]

    # Convert to common format, expand to hourly keys for alignment
    scale = 8 / funding_hours  # normalize to 8h: e.g. 4h * 2 = 8h, 8h * 1 = 8h
    records = []
    for r in valid:
        ts_ns = int(r["funding_time"])
        ts_s = ts_ns // 10**9
        rate_pct = safe_float(r["funding_rate"])
        rate_8h_dec = (rate_pct / 100) * scale

        for h in range(funding_hours):
            hour_ts = ts_s + h * 3600
            records.append({
                "ts": datetime.fromtimestamp(hour_ts, tz=timezone.utc),
                "hour_ts": hour_ts,
                "rate_8h": rate_8h_dec,
            })
    return records, funding_hours


def fetch_grvt_current(instrument, funding_hours):
    """Fetch current GRVT funding rate from ticker."""
    try:
        resp = requests.post(
            f"{GRVT_API}/ticker",
            json={"instrument": instrument},
            headers={"Content-Type": "application/json"}, timeout=15,
        )
        resp.raise_for_status()
        r = resp.json().get("result", {})
        pct = safe_float(r.get("funding_rate_8h_curr", 0))
        dec = pct / 100
        scale = 8 / funding_hours
        return {
            "funding_raw_pct": pct,
            "funding_8h_decimal": dec * scale,
            "annual_pct": dec * scale * 3 * 365 * 100,
            "mark_price": safe_float(r.get("mark_price")),
            "open_interest": safe_float(r.get("open_interest")),
        }
    except Exception as e:
        print(f"  GRVT ticker error: {e}")
        return None


# ============================================================================
# ANALYSIS
# ============================================================================

def analyze_rates(rates_8h, label):
    """Compute statistics on a list of 8h-normalized rate values."""
    if not rates_8h:
        return None
    avg = sum(rates_8h) / len(rates_8h)
    positive = sum(1 for r in rates_8h if r > 0)
    sorted_rates = sorted(rates_8h)
    return {
        "label": label,
        "count": len(rates_8h),
        "avg_8h": avg,
        "annual_pct": rate_to_annual(avg),
        "max_8h": max(rates_8h),
        "min_8h": min(rates_8h),
        "median_8h": sorted_rates[len(rates_8h) // 2],
        "positive_pct": positive / len(rates_8h) * 100,
        "negative_pct": (len(rates_8h) - positive) / len(rates_8h) * 100,
        "max_ann": rate_to_annual(max(rates_8h)),
        "min_ann": rate_to_annual(min(rates_8h)),
    }


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Funding Rate Backtest: HyENA vs GRVT",
        epilog="For unlisted pairs, use --hyena-coin and --grvt-instrument directly.",
    )
    parser.add_argument("--asset", default="HYPE",
                        help=f"Asset preset ({', '.join(ASSET_PRESETS.keys())}) or custom name")
    parser.add_argument("--hyena-coin", help="Override HyENA coin (e.g. hyna:SOL)")
    parser.add_argument("--grvt-instrument", help="Override GRVT instrument (e.g. SOL_USDT_Perp)")
    parser.add_argument("--grvt-funding-hours", type=int, help="Override GRVT funding interval (4 or 8)")
    parser.add_argument("--days", type=int, default=180, help="Max history to fetch (default: 180)")
    args = parser.parse_args()

    # Resolve asset config
    asset_name = args.asset.upper()
    preset = ASSET_PRESETS.get(asset_name, {})
    hyena_coin = args.hyena_coin or preset.get("hyena_coin", f"hyna:{asset_name}")
    grvt_instrument = args.grvt_instrument or preset.get("grvt_instrument", f"{asset_name}_USDT_Perp")
    grvt_fh = args.grvt_funding_hours or preset.get("grvt_funding_hours", 8)
    points = preset.get("grvt_points_multiplier", "1x")

    now = datetime.now(timezone.utc)
    W = 72

    print("=" * W)
    print(f"  {asset_name} FUNDING RATE BACKTEST — HyENA ({hyena_coin}) vs GRVT ({grvt_instrument})")
    print(f"  {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * W)

    # ── Fetch Data ──
    print("\nFetching data...")

    hyena_hist = fetch_hyena_history(hyena_coin, days=args.days)
    print(f"  HyENA: {len(hyena_hist)} records (1h intervals)")

    grvt_hist, grvt_fh = fetch_grvt_history(grvt_instrument, grvt_fh)
    print(f"  GRVT:  {len(grvt_hist)} records ({grvt_fh}h intervals, expanded to hourly)")

    grvt_snap = fetch_grvt_current(grvt_instrument, grvt_fh)
    if grvt_snap:
        print(f"  GRVT current: {grvt_snap['funding_raw_pct']}% ({grvt_snap['annual_pct']:+.2f}% ann)")
        print(f"  GRVT OI: {grvt_snap['open_interest']:,.0f} {asset_name}, mark: ${grvt_snap['mark_price']:,.3f}")

    if not hyena_hist:
        print(f"\n  ERROR: No HyENA data. {hyena_coin} may not be listed yet.")
        return
    if not grvt_hist:
        print(f"\n  ERROR: No GRVT historical funding data for {grvt_instrument}.")
        return

    # ── Build lookup maps keyed by hour_ts ──
    hyena_map = {r["hour_ts"]: r["rate_8h"] for r in hyena_hist}
    grvt_map = {r["hour_ts"]: r["rate_8h"] for r in grvt_hist}

    common_hours = sorted(set(hyena_map.keys()) & set(grvt_map.keys()))
    if not common_hours:
        print("\n  ERROR: No overlapping hours between HyENA and GRVT data.")
        return

    t0 = datetime.fromtimestamp(common_hours[0], tz=timezone.utc)
    t1 = datetime.fromtimestamp(common_hours[-1], tz=timezone.utc)
    days = (t1 - t0).days

    print(f"\n  Overlapping range: {t0.strftime('%Y-%m-%d')} — {t1.strftime('%Y-%m-%d')} ({days} days)")
    print(f"  Matched hours: {len(common_hours)}")

    # ── Individual Rate Stats ──
    h_rates = [hyena_map[ts] for ts in common_hours]
    g_rates = [grvt_map[ts] for ts in common_hours]

    print(f"\n{'='*W}")
    print("  INDIVIDUAL RATE STATISTICS (8h normalized)")
    print(f"{'='*W}")

    for label, rates in [
        (f"HyENA {hyena_coin} (1h -> 8h)", h_rates),
        (f"GRVT {grvt_instrument} ({grvt_fh}h -> 8h)", g_rates),
    ]:
        stats = analyze_rates(rates, label)
        print(f"\n  {stats['label']}  ({stats['count']} data points)")
        print(f"    Avg 8h Rate:    {stats['avg_8h']:+.8f}  ({stats['annual_pct']:+.2f}% ann)")
        print(f"    Max:            {stats['max_8h']:+.8f}  ({stats['max_ann']:+.2f}% ann)")
        print(f"    Min:            {stats['min_8h']:+.8f}  ({stats['min_ann']:+.2f}% ann)")
        print(f"    Median:         {stats['median_8h']:+.8f}")
        print(f"    Positive:       {stats['positive_pct']:.1f}% | Negative: {stats['negative_pct']:.1f}%")

    # ── Spread Analysis ──
    print(f"\n{'='*W}")
    print(f"  SPREAD ANALYSIS (actual paired data, {len(common_hours)} hours)")
    print(f"{'='*W}")

    a_spreads = []
    b_spreads = []
    monthly_a = {}
    monthly_b = {}

    for ts in common_hours:
        h8 = hyena_map[ts]
        g8 = grvt_map[ts]
        a = g8 - h8
        b = h8 - g8
        a_spreads.append(a)
        b_spreads.append(b)
        month = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
        monthly_a.setdefault(month, []).append(a)
        monthly_b.setdefault(month, []).append(b)

    a_avg = sum(a_spreads) / len(a_spreads)
    b_avg = sum(b_spreads) / len(b_spreads)
    a_pos = sum(1 for r in a_spreads if r > 0) / len(a_spreads) * 100
    b_pos = sum(1 for r in b_spreads if r > 0) / len(b_spreads) * 100

    print(f"\n  Strategy A: Long HyENA + Short GRVT")
    print(f"    Avg spread (8h):  {a_avg:+.8f}  ({rate_to_annual(a_avg):+.2f}% ann)")
    print(f"    Max spread:       {max(a_spreads):+.8f}  ({rate_to_annual(max(a_spreads)):+.2f}% ann)")
    print(f"    Min spread:       {min(a_spreads):+.8f}  ({rate_to_annual(min(a_spreads)):+.2f}% ann)")
    print(f"    Positive periods: {a_pos:.1f}%")

    print(f"\n  Strategy B: Short HyENA + Long GRVT")
    print(f"    Avg spread (8h):  {b_avg:+.8f}  ({rate_to_annual(b_avg):+.2f}% ann)")
    print(f"    Max spread:       {max(b_spreads):+.8f}  ({rate_to_annual(max(b_spreads)):+.2f}% ann)")
    print(f"    Min spread:       {min(b_spreads):+.8f}  ({rate_to_annual(min(b_spreads)):+.2f}% ann)")
    print(f"    Positive periods: {b_pos:.1f}%")

    # ── Projected Yield ──
    print(f"\n{'='*W}")
    print("  PROJECTED YIELD (per direction, at 2x leverage)")
    print(f"{'='*W}")

    usde_eff = 6.0
    grvt_eff = 5.0
    est_fees = 0.5

    for label, avg_spread, pos_pct in [
        ("Strategy A (Long HyENA + Short GRVT)", a_avg, a_pos),
        ("Strategy B (Short HyENA + Long GRVT)", b_avg, b_pos),
    ]:
        funding_ann = rate_to_annual(avg_spread)
        gross = funding_ann + usde_eff + grvt_eff
        net = gross - est_fees

        print(f"\n  {label}")
        print(f"    Funding spread:     {funding_ann:+.2f}% ann (at 2x leverage)")
        print(f"    USDe reward:        +{usde_eff:.1f}% (on total capital)")
        print(f"    GRVT reward:        +{grvt_eff:.1f}% (on total capital) + {points} points")
        print(f"    Est. fees:          -{est_fees:.1f}%")
        print(f"    ────────────────────")
        print(f"    Net yield:          {net:+.2f}% ann + {points} GRVT points")
        print(f"    Spread positive:    {pos_pct:.1f}% of periods")

    # ── Recommendation ──
    print(f"\n{'='*W}")
    print("  RECOMMENDATION")
    print(f"{'='*W}")

    better = "B" if b_avg > a_avg else "A"
    edge = abs(rate_to_annual(b_avg) - rate_to_annual(a_avg))

    if better == "B":
        print(f"\n  Strategy B (Short HyENA + Long GRVT) is better by {edge:.1f}% ann")
    else:
        print(f"\n  Strategy A (Long HyENA + Short GRVT) is better by {edge:.1f}% ann")

    # ── Monthly Breakdown ──
    print(f"\n{'='*W}")
    print("  MONTHLY BREAKDOWN")
    print(f"{'='*W}")

    print(f"\n  {'Month':<10} {'A (Long H) ann':>16} {'B (Short H) ann':>16} {'Better':>8} {'N':>6}")
    print(f"  {'─'*10} {'─'*16} {'─'*16} {'─'*8} {'─'*6}")
    for m in sorted(monthly_a.keys()):
        ra = monthly_a[m]
        rb = monthly_b[m]
        avg_a = sum(ra) / len(ra)
        avg_b = sum(rb) / len(rb)
        ann_a = rate_to_annual(avg_a)
        ann_b = rate_to_annual(avg_b)
        w = "A" if ann_a > ann_b else "B"
        print(f"  {m:<10} {ann_a:+14.2f}% {ann_b:+14.2f}% {w:>8} {len(ra):6d}")

    # ── Weekly Breakdown ──
    winner_spreads = b_spreads if better == "B" else a_spreads
    print(f"\n{'='*W}")
    print(f"  WEEKLY BREAKDOWN (Strategy {better} spread)")
    print(f"{'='*W}")

    weekly = {}
    for i, ts in enumerate(common_hours):
        week = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-W%W")
        weekly.setdefault(week, []).append(winner_spreads[i])

    print(f"\n  {'Week':<12} {'Avg 8h':>12} {'Ann %':>10} {'Positive':>10} {'N':>5}")
    print(f"  {'─'*12} {'─'*12} {'─'*10} {'─'*10} {'─'*5}")
    for week in sorted(weekly.keys()):
        rates = weekly[week]
        avg = sum(rates) / len(rates)
        pos = sum(1 for r in rates if r > 0) / len(rates) * 100
        print(f"  {week:<12} {avg:+.8f} {rate_to_annual(avg):+8.2f}% {pos:8.1f}% {len(rates):5d}")

    print(f"\n{'='*W}")
    print("  Done.")
    print(f"{'='*W}")


if __name__ == "__main__":
    main()
