#!/usr/bin/env python3
"""
BTC Funding Rate Comparison v3: HyENA vs GRVT vs Backpack
========================================================================
Run: pip install requests && python3 btc_funding_compare_v3.py

CRITICAL FIX from v2:
- v2 used coin="BTC" which queries HYPERLIQUID NATIVE BTC/USDC perp
- v3 uses coin="hyna:BTC" which queries HyENA's BTC-USDe HIP-3 market
- These are SEPARATE markets with INDEPENDENT funding rates, OI, and orderbooks
- HyENA has its own dex prefix "hyna" in the Hyperliquid API

Also new:
- Added perpDexs query to list all HIP-3 DEXs
- Added perpDexStatus for HyENA-specific info (OI caps, funding multiplier)
- Shows both HyENA and Hyperliquid native rates for comparison
- Uses {"type": "meta", "dex": "hyna"} for HyENA universe
"""

import requests
import json
import time
from datetime import datetime, timezone, timedelta

# ============================================================================
# CONSTANTS
# ============================================================================

HYENA_DEX_NAME = "hyna"       # HyENA's dex prefix in Hyperliquid API
HYENA_BTC_COIN = "hyna:BTC"   # HyENA BTC-USDe market identifier
HL_NATIVE_BTC  = "BTC"        # Hyperliquid native BTC/USDC market

HL_API = "https://api.hyperliquid.xyz/info"
GRVT_API = "https://market-data.grvt.io/full/v1"
BP_API = "https://api.backpack.exchange/api/v1"

# ============================================================================
# HELPERS
# ============================================================================

def rate_to_annual(rate_8h_decimal):
    """Convert 8h funding rate (decimal) to annualized percentage"""
    return rate_8h_decimal * 3 * 365 * 100


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def sep(title):
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}")


# ============================================================================
# 1. HYENA (HIP-3 on Hyperliquid) — coin = "hyna:BTC"
# ============================================================================

def fetch_hyena_current():
    """
    Fetch HyENA BTC-USDe current data via Hyperliquid API.
    
    IMPORTANT: HIP-3 assets do NOT appear in standard metaAndAssetCtxs.
    Must use {"type": "meta", "dex": "hyna"} for HyENA universe,
    and activeAssetData websocket or predict funding for real-time rates.
    
    For current funding, we use the most recent fundingHistory entry
    plus predicted funding if available.
    """
    result = {}
    
    # --- HyENA: get meta for universe info ---
    try:
        resp = requests.post(HL_API, json={"type": "meta", "dex": HYENA_DEX_NAME}, timeout=15)
        hyena_meta = resp.json()
        if isinstance(hyena_meta, dict) and "universe" in hyena_meta:
            result["hyena_universe"] = [a["name"] for a in hyena_meta["universe"]]
            result["collateral_token"] = hyena_meta.get("collateralToken", "unknown")
    except Exception as e:
        result["hyena_meta_error"] = str(e)
    
    # --- HyENA: get latest funding from history (most recent hour) ---
    try:
        start_ms = int((datetime.now(timezone.utc) - timedelta(hours=12)).timestamp() * 1000)
        resp = requests.post(HL_API, json={
            "type": "fundingHistory", "coin": HYENA_BTC_COIN, "startTime": start_ms
        }, timeout=15)
        hist = resp.json()
        if hist and len(hist) > 0:
            latest = hist[-1]  # most recent entry
            rate_1h = safe_float(latest["fundingRate"])
            result["hyena"] = {
                "coin": HYENA_BTC_COIN,
                "funding_1h": rate_1h,
                "funding_8h": rate_1h * 8,
                "annual_pct": rate_to_annual(rate_1h * 8),
                "premium": safe_float(latest.get("premium", 0)),
                "timestamp": datetime.fromtimestamp(latest["time"] / 1000, tz=timezone.utc),
                "data_source": "fundingHistory (latest settled)",
            }
    except Exception as e:
        result["hyena_funding_error"] = str(e)
    
    # --- HyENA: try predictedFundings for forward-looking rate ---
    try:
        resp = requests.post(HL_API, json={
            "type": "predictedFundings", "dex": HYENA_DEX_NAME
        }, timeout=15)
        pf = resp.json()
        # Response format: list of [coin, {venues...}] or similar
        if pf and isinstance(pf, list):
            for entry in pf:
                if isinstance(entry, list) and len(entry) >= 2:
                    coin = entry[0]
                    if coin == "BTC" or coin == HYENA_BTC_COIN:
                        predicted = entry[1]
                        result["hyena_predicted"] = {
                            "coin": coin,
                            "data": predicted,
                        }
                        break
    except Exception as e:
        # predictedFundings may not be supported for HIP-3 dexs
        result["predicted_note"] = f"predictedFundings not available for HIP-3: {e}"
    
    # --- HyENA: get L2 book for mark price ---
    try:
        resp = requests.post(HL_API, json={
            "type": "l2Book", "coin": HYENA_BTC_COIN, "nSigFigs": 5
        }, timeout=15)
        book = resp.json()
        if book and "levels" in book:
            bids = book["levels"][0] if len(book["levels"]) > 0 else []
            asks = book["levels"][1] if len(book["levels"]) > 1 else []
            if bids and asks:
                best_bid = safe_float(bids[0].get("px", 0))
                best_ask = safe_float(asks[0].get("px", 0))
                mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
                result.setdefault("hyena", {})["mark_price"] = mid
                result.setdefault("hyena", {})["best_bid"] = best_bid
                result.setdefault("hyena", {})["best_ask"] = best_ask
                result.setdefault("hyena", {})["spread_bps"] = (best_ask - best_bid) / mid * 10000 if mid else 0
    except Exception as e:
        result["book_error"] = str(e)
    
    # --- HL Native: standard metaAndAssetCtxs ---
    try:
        resp = requests.post(HL_API, json={"type": "metaAndAssetCtxs"}, timeout=15)
        data = resp.json()
        meta, ctxs = data[0], data[1]
        for i, asset in enumerate(meta["universe"]):
            if asset["name"] == HL_NATIVE_BTC:
                ctx = ctxs[i]
                funding_1h = safe_float(ctx.get("funding"))
                result["hl_native"] = {
                    "coin": HL_NATIVE_BTC,
                    "funding_1h": funding_1h,
                    "funding_8h": funding_1h * 8,
                    "annual_pct": rate_to_annual(funding_1h * 8),
                    "mark_price": safe_float(ctx.get("markPx")),
                    "open_interest": safe_float(ctx.get("openInterest")),
                    "volume_24h": safe_float(ctx.get("dayNtlVlm")),
                }
                break
    except Exception as e:
        result["hl_native_error"] = str(e)
    
    return result


def fetch_hyena_history(days=30):
    """
    Fetch HyENA BTC-USDe funding history.
    Uses coin="hyna:BTC" (NOT "BTC" which is Hyperliquid native).
    """
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    resp = requests.post(HL_API, json={
        "type": "fundingHistory", "coin": HYENA_BTC_COIN, "startTime": start_ms
    }, timeout=15)
    
    records = []
    for e in resp.json():
        rate_1h = safe_float(e["fundingRate"])
        records.append({
            "ts": datetime.fromtimestamp(e["time"] / 1000, tz=timezone.utc),
            "rate_1h": rate_1h,
            "rate_8h": rate_1h * 8,
            "premium": safe_float(e.get("premium", 0)),
        })
    return records


def fetch_hl_native_history(days=30):
    """Fetch Hyperliquid native BTC funding history (for comparison)"""
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    resp = requests.post(HL_API, json={
        "type": "fundingHistory", "coin": HL_NATIVE_BTC, "startTime": start_ms
    }, timeout=15)
    
    records = []
    for e in resp.json():
        rate_1h = safe_float(e["fundingRate"])
        records.append({
            "ts": datetime.fromtimestamp(e["time"] / 1000, tz=timezone.utc),
            "rate_1h": rate_1h,
            "rate_8h": rate_1h * 8,
        })
    return records


def fetch_hyena_dex_info():
    """Fetch HyENA-specific DEX info: OI caps, funding multiplier, etc."""
    info = {}
    
    # List all perp DEXs
    try:
        resp = requests.post(HL_API, json={"type": "perpDexs"}, timeout=15)
        dexs = resp.json()
        info["all_dexs"] = dexs
        # Find HyENA's index and info
        for i, dex in enumerate(dexs):
            if dex and isinstance(dex, dict) and dex.get("name") == HYENA_DEX_NAME:
                info["hyena_dex"] = dex
                info["hyena_dex_index"] = i
                break
    except Exception as e:
        info["perpDexs_error"] = str(e)
    
    # HyENA DEX status
    try:
        resp = requests.post(HL_API, json={"type": "perpDexStatus", "dex": HYENA_DEX_NAME}, timeout=15)
        info["status"] = resp.json()
    except Exception as e:
        info["status_error"] = str(e)
    
    # HyENA DEX limits (OI caps etc)
    try:
        resp = requests.post(HL_API, json={"type": "perpDexLimits", "dex": HYENA_DEX_NAME}, timeout=15)
        info["limits"] = resp.json()
    except Exception as e:
        info["limits_error"] = str(e)
    
    return info


# ============================================================================
# 2. GRVT
# ============================================================================

def fetch_grvt_current():
    """GRVT ticker: funding_rate is percentage (0.01 = 0.01%)"""
    resp = requests.post(f"{GRVT_API}/ticker", json={"instrument": "BTC_USDT_Perp"},
                        timeout=15, headers={"Content-Type": "application/json"})
    if resp.status_code != 200:
        return None
    
    r = resp.json().get("result", {})
    funding_8h_pct = safe_float(r.get("funding_rate_8h_curr", 0))
    funding_8h_decimal = funding_8h_pct / 100  # 0.01% → 0.0001
    
    next_funding_ns = int(r.get("next_funding_time", 0))
    
    return {
        "funding_8h_pct": funding_8h_pct,
        "funding_8h_decimal": funding_8h_decimal,
        "annual_pct": rate_to_annual(funding_8h_decimal),
        "mark_price": safe_float(r.get("mark_price")),
        "index_price": safe_float(r.get("index_price")),
        "open_interest_btc": safe_float(r.get("open_interest")),
        "long_short_ratio": safe_float(r.get("long_short_ratio")),
        "volume_24h_quote": safe_float(r.get("buy_volume_24h_q")) + safe_float(r.get("sell_volume_24h_q")),
        "next_funding_time": datetime.fromtimestamp(next_funding_ns / 1e9, tz=timezone.utc) if next_funding_ns else None,
        "interval": "8h",
        "funding_cap": "±0.3000%",
    }


# ============================================================================
# 3. BACKPACK
# ============================================================================

def fetch_bp_current():
    result = {}
    try:
        resp = requests.get(f"{BP_API}/ticker?symbol=BTC_USDC_PERP", timeout=15)
        if resp.status_code == 200:
            result["ticker"] = resp.json()
    except Exception as e:
        result["ticker_error"] = str(e)
    
    try:
        resp = requests.get(f"{BP_API}/markets", timeout=15)
        if resp.status_code == 200:
            for m in resp.json():
                if m.get("symbol") == "BTC_USDC_PERP":
                    result["market"] = m
                    break
    except Exception as e:
        result["market_error"] = str(e)
    
    return result


def fetch_bp_history(limit=1000):
    all_records = []
    offset = 0
    batch_size = 100
    
    while len(all_records) < limit:
        try:
            params = {"symbol": "BTC_USDC_PERP", "limit": batch_size, "offset": offset}
            resp = requests.get(f"{BP_API}/fundingIntervalRates", params=params, timeout=15)
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not batch or not isinstance(batch, list) or len(batch) == 0:
                break
            all_records.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size
        except Exception as e:
            print(f"  BP history error: {e}")
            break
    
    records = []
    for entry in all_records:
        rate_1h = safe_float(entry.get("fundingRate", 0))
        ts_str = entry.get("intervalEndTimestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except:
            ts = None
        records.append({"ts": ts, "rate_1h": rate_1h, "rate_8h": rate_1h * 8})
    return records


# ============================================================================
# ANALYSIS
# ============================================================================

def analyze_rates(records, label, rate_key="rate_8h"):
    if not records:
        return None
    rates = [r[rate_key] for r in records if r.get(rate_key) is not None]
    if not rates:
        return None
    avg = sum(rates) / len(rates)
    positive = sum(1 for r in rates if r > 0)
    return {
        "label": label,
        "count": len(rates),
        "avg_8h": avg,
        "annual_pct": rate_to_annual(avg),
        "max_8h": max(rates),
        "min_8h": min(rates),
        "median_8h": sorted(rates)[len(rates) // 2],
        "positive_pct": positive / len(rates) * 100,
        "negative_pct": (len(rates) - positive) / len(rates) * 100,
    }


def compute_spreads(hyena_8h, grvt_8h, bp_8h):
    spreads = {}
    if hyena_8h is not None and grvt_8h is not None:
        diff = grvt_8h - hyena_8h
        spreads["GRVT - HyENA"] = {
            "spread_8h": diff, "annual_pct": rate_to_annual(diff),
            "strategy": "Long HyENA + Short GRVT" if diff > 0 else "Short HyENA + Long GRVT",
        }
    if hyena_8h is not None and bp_8h is not None:
        diff = bp_8h - hyena_8h
        spreads["BP - HyENA"] = {
            "spread_8h": diff, "annual_pct": rate_to_annual(diff),
            "strategy": "Long HyENA + Short BP" if diff > 0 else "Short HyENA + Long BP",
        }
    if grvt_8h is not None and bp_8h is not None:
        diff = grvt_8h - bp_8h
        spreads["GRVT - BP"] = {
            "spread_8h": diff, "annual_pct": rate_to_annual(diff),
            "strategy": "Long BP + Short GRVT" if diff > 0 else "Short BP + Long GRVT",
        }
    return spreads


# ============================================================================
# MAIN
# ============================================================================

def main():
    now = datetime.now(timezone.utc)
    print("=" * 72)
    print("  BTC FUNDING RATE COMPARISON v3 — HyENA (hyna:BTC) vs GRVT vs Backpack")
    print(f"  {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 72)
    print()
    print("  ⚠️  v3 修正: 使用 coin='hyna:BTC' 查询 HyENA 的 BTC-USDe 市场")
    print("     (v2 错误使用 coin='BTC' 查询的是 Hyperliquid 原生 BTC/USDC 市场)")
    print("     两者是独立的市场，有不同的 funding rate、OI 和订单簿!")

    # ── HyENA DEX Info ─────────────────────────────────────────────────────
    sep("HYENA DEX INFO (HIP-3)")
    
    dex_info = None
    try:
        dex_info = fetch_hyena_dex_info()
        
        if dex_info.get("hyena_dex"):
            d = dex_info["hyena_dex"]
            print(f"\n  DEX Name:          {d.get('name')}")
            print(f"  Full Name:         {d.get('fullName', 'N/A')}")
            print(f"  Deployer:          {d.get('deployer', 'N/A')}")
            
            # Funding multiplier per asset
            fm = d.get("assetToFundingMultiplier", [])
            if fm:
                print(f"  Funding Multipliers:")
                for coin, mult in fm:
                    print(f"    {coin}: {mult}x")
            
            # OI caps
            oi_caps = d.get("assetToStreamingOiCap", [])
            if oi_caps:
                print(f"  OI Caps:")
                for coin, cap in oi_caps:
                    print(f"    {coin}: {cap}")
        else:
            print("  HyENA DEX not found in perpDexs response")
            # Show all dexs for debugging
            if dex_info.get("all_dexs"):
                print(f"  Available DEXs: {json.dumps([d.get('name') if d else None for d in dex_info['all_dexs']][:20])}")
        
        if dex_info.get("limits"):
            print(f"\n  DEX Limits: {json.dumps(dex_info['limits'])[:300]}")
    except Exception as e:
        print(f"  DEX info error: {e}")

    # ── Current Rates ──────────────────────────────────────────────────────
    sep("CURRENT FUNDING RATES (normalized to 8h)")
    
    hyena_data = None
    grvt = None
    
    # HyENA + HL native
    try:
        hl_data = fetch_hyena_current()
        
        if hl_data.get("hyena"):
            hyena_data = hl_data["hyena"]
            h = hyena_data
            print(f"\n  ★ HyENA BTC-USDe (coin: {h['coin']}):")
            print(f"    Rate (8h):    {h['funding_8h']:+.6f}  ({h['annual_pct']:+.2f}% ann.)")
            print(f"    Direction:    {'Long pays Short' if h['funding_8h'] > 0 else 'Short pays Long'}")
            if h.get('mark_price') and h['mark_price'] > 0:
                print(f"    Mid Price:    ${h['mark_price']:,.2f}")
            if h.get('best_bid') and h.get('best_ask'):
                print(f"    Bid/Ask:      ${h['best_bid']:,.1f} / ${h['best_ask']:,.1f}  (spread: {h.get('spread_bps', 0):.1f} bps)")
            if h.get('premium'):
                print(f"    Premium:      {h['premium']:+.6f}")
            print(f"    Data source:  {h.get('data_source', 'N/A')}")
            if h.get('timestamp'):
                print(f"    Last settled: {h['timestamp'].strftime('%Y-%m-%d %H:%M UTC')}")
        else:
            print("\n  ⚠️  HyENA (hyna:BTC) funding data unavailable")
            if hl_data.get("hyena_funding_error"):
                print(f"      Error: {hl_data['hyena_funding_error']}")
        
        # HyENA predicted funding
        if hl_data.get("hyena_predicted"):
            pred = hl_data["hyena_predicted"]
            print(f"\n    Predicted funding: {json.dumps(pred['data'])[:300]}")
        elif hl_data.get("predicted_note"):
            print(f"\n    Note: {hl_data['predicted_note']}")
        
        # HyENA universe info
        if hl_data.get("hyena_universe"):
            print(f"\n    HyENA markets: {', '.join(hl_data['hyena_universe'][:15])}")
            print(f"    Collateral: {hl_data.get('collateral_token', 'N/A')}")
        
        # HL native for reference
        if hl_data.get("hl_native"):
            n = hl_data["hl_native"]
            print(f"\n  [Reference] Hyperliquid Native BTC/USDC (coin: {n['coin']}):")
            print(f"    Rate (8h):    {n['funding_8h']:+.6f}  ({n['annual_pct']:+.2f}% ann.)")
            print(f"    Direction:    {'Long pays Short' if n['funding_8h'] > 0 else 'Short pays Long'}")
            print(f"    Mark:         ${n['mark_price']:,.2f}")
            print(f"    OI (BTC):     {n['open_interest']:,.2f}")
            print(f"    24h Vol:      ${n['volume_24h']:,.0f}")
            
            if hyena_data:
                diff = hyena_data['funding_8h'] - n['funding_8h']
                print(f"\n    *** HyENA vs HL Native差异: {diff:+.6f}/8h ({rate_to_annual(diff):+.2f}% ann.) ***")
    except Exception as e:
        print(f"  HyENA/HL error: {e}")
        import traceback; traceback.print_exc()
    
    # GRVT
    try:
        grvt = fetch_grvt_current()
        if grvt:
            print(f"\n  GRVT BTC_USDT_Perp:")
            print(f"    Rate (8h):    {grvt['funding_8h_decimal']:+.6f}  ({grvt['annual_pct']:+.2f}% ann.)  [displayed as {grvt['funding_8h_pct']}%]")
            print(f"    Direction:    {'Long pays Short' if grvt['funding_8h_decimal'] > 0 else 'Short pays Long'}")
            print(f"    Mark:         ${grvt['mark_price']:,.2f}")
            print(f"    Index:        ${grvt['index_price']:,.2f}")
            print(f"    OI (BTC):     {grvt['open_interest_btc']:,.3f}")
            print(f"    L/S Ratio:    {grvt['long_short_ratio']:.3f}")
            print(f"    24h Vol:      ${grvt['volume_24h_quote']:,.0f}")
            print(f"    Interval:     {grvt['interval']}  |  Cap/Floor: {grvt['funding_cap']}")
            if grvt['next_funding_time']:
                delta = grvt['next_funding_time'] - now
                mins = delta.total_seconds() / 60
                print(f"    Next Settle:  {grvt['next_funding_time'].strftime('%H:%M:%S UTC')} (in {mins:.0f} min)")
    except Exception as e:
        print(f"  GRVT error: {e}")
    
    # Backpack
    bp_data = None
    try:
        bp_data = fetch_bp_current()
        if bp_data:
            print(f"\n  Backpack BTC_USDC_PERP:")
            if bp_data.get("ticker"):
                t = bp_data["ticker"]
                print(f"    Last Price:   ${safe_float(t.get('lastPrice')):,.1f}")
                print(f"    24h Vol:      ${safe_float(t.get('quoteVolume')):,.0f}")
                print(f"    Trades 24h:   {t.get('trades', 'N/A')}")
            if bp_data.get("market"):
                m = bp_data["market"]
                interval_ms = safe_float(m.get("fundingInterval", 0))
                print(f"    Interval:     {interval_ms/1000/3600:.0f}h ({interval_ms/1000:.0f}s)")
    except Exception as e:
        print(f"  BP error: {e}")

    # ── Historical Analysis ────────────────────────────────────────────────
    sep("HISTORICAL ANALYSIS")
    
    now_ts = datetime.now(timezone.utc)
    
    # HyENA history (hyna:BTC)
    hyena_30d_stats = None
    try:
        hyena_hist = fetch_hyena_history(days=30)
        if hyena_hist:
            hyena_7d = [r for r in hyena_hist if r["ts"] >= now_ts - timedelta(days=7)]
            hyena_7d_stats = analyze_rates(hyena_7d, "HyENA (hyna:BTC) 7d")
            hyena_30d_stats = analyze_rates(hyena_hist, "HyENA (hyna:BTC) 30d")
            
            for stats in [hyena_7d_stats, hyena_30d_stats]:
                if stats:
                    print(f"\n  {stats['label']}  ({stats['count']} data points)")
                    print(f"    Avg 8h Rate:    {stats['avg_8h']:+.6f}  ({stats['annual_pct']:+.2f}% ann.)")
                    print(f"    Max / Min:      {stats['max_8h']:+.6f} / {stats['min_8h']:+.6f}")
                    print(f"    Median:         {stats['median_8h']:+.6f}")
                    print(f"    Positive:       {stats['positive_pct']:.1f}% | Negative: {stats['negative_pct']:.1f}%")
        else:
            print("\n  HyENA (hyna:BTC) 历史数据为空 — HyENA 可能上线不久 (Dec 9, 2025)")
    except Exception as e:
        print(f"  HyENA history error: {e}")
    
    # HL native history for comparison
    try:
        hl_hist = fetch_hl_native_history(days=30)
        if hl_hist:
            hl_30d_stats = analyze_rates(hl_hist, "[Ref] HL Native BTC 30d")
            if hl_30d_stats:
                print(f"\n  {hl_30d_stats['label']}  ({hl_30d_stats['count']} data points)")
                print(f"    Avg 8h Rate:    {hl_30d_stats['avg_8h']:+.6f}  ({hl_30d_stats['annual_pct']:+.2f}% ann.)")
                print(f"    Max / Min:      {hl_30d_stats['max_8h']:+.6f} / {hl_30d_stats['min_8h']:+.6f}")
                print(f"    Positive:       {hl_30d_stats['positive_pct']:.1f}% | Negative: {hl_30d_stats['negative_pct']:.1f}%")
    except Exception as e:
        print(f"  HL native history error: {e}")
    
    # Backpack history
    try:
        bp_hist = fetch_bp_history(limit=1000)
        if bp_hist:
            bp_7d = [r for r in bp_hist if r["ts"] and r["ts"] >= now_ts - timedelta(days=7)]
            bp_7d_stats = analyze_rates(bp_7d, "Backpack 7d") if bp_7d else None
            bp_all_stats = analyze_rates(bp_hist, f"Backpack ALL ({len(bp_hist)} records)")
            
            for stats in [bp_7d_stats, bp_all_stats]:
                if stats:
                    print(f"\n  {stats['label']}  ({stats['count']} data points)")
                    print(f"    Avg 8h Rate:    {stats['avg_8h']:+.6f}  ({stats['annual_pct']:+.2f}% ann.)")
                    print(f"    Max / Min:      {stats['max_8h']:+.6f} / {stats['min_8h']:+.6f}")
                    print(f"    Positive:       {stats['positive_pct']:.1f}% | Negative: {stats['negative_pct']:.1f}%")
    except Exception as e:
        print(f"  BP history error: {e}")
    
    # ── Spreads ────────────────────────────────────────────────────────────
    sep("FUNDING RATE SPREADS (current snapshot)")
    
    hyena_8h = hyena_data["funding_8h"] if hyena_data else None
    grvt_8h = grvt["funding_8h_decimal"] if grvt else None
    bp_8h = None
    try:
        bp_latest = fetch_bp_history(limit=5)
        if bp_latest:
            bp_8h = bp_latest[0]["rate_8h"]
            print(f"  (Backpack latest: {bp_latest[0]['ts']} rate_1h={bp_latest[0]['rate_1h']:.8f})")
    except:
        pass
    
    spreads = compute_spreads(hyena_8h, grvt_8h, bp_8h)
    for pair, info in spreads.items():
        print(f"\n  {pair}:")
        print(f"    Spread (8h):    {info['spread_8h']:+.6f}  ({info['annual_pct']:+.2f}% ann.)")
        print(f"    Pure arb →      {info['strategy']}")
    
    if not spreads:
        print("\n  无法计算 spread (HyENA 数据缺失)")
    
    # ── Combined Yield ─────────────────────────────────────────────────────
    sep("ESTIMATED COMBINED YIELD (conservative)")
    
    if hyena_data and grvt:
        hyena_r = hyena_data["funding_8h"]
        grvt_r = grvt["funding_8h_decimal"]
        
        # When funding is negative, long receives; when positive, short receives
        hyena_long_funding = -rate_to_annual(hyena_r)  # flip: neg→long receives (+)
        grvt_short_funding = rate_to_annual(grvt_r)    # pos→short receives (+)
        
        usde_reward = 12.0
        grvt_equity = 10.0
        est_fees = 0.4
        
        total_funding = hyena_long_funding + grvt_short_funding
        total_rewards = usde_reward + grvt_equity
        gross = total_funding + total_rewards
        net = gross - est_fees
        
        print(f"""
  组合: HyENA Long + GRVT Short (equal notional, 2x leverage)
  
    [HyENA hyna:BTC]
    Funding (8h):               {hyena_r:+.6f}
    Long receives/pays:         {hyena_long_funding:+.2f}% ann.
    
    [GRVT BTC_USDT_Perp]  
    Funding (8h):               {grvt_r:+.6f}
    Short receives/pays:        {grvt_short_funding:+.2f}% ann.
    
    ────────────────────────────
    Funding 小计:               {total_funding:+.2f}% ann.
    
    USDe Boosted Reward:        +{usde_reward:.1f}% ann.
    GRVT Equity Reward:         +{grvt_equity:.1f}% ann.
    ────────────────────────────
    Rewards 小计:               +{total_rewards:.1f}% ann.
    
    毛收益:                     {gross:+.2f}% ann.
    预估手续费:                 -{est_fees:.1f}% ann.
    ════════════════════════════
    净收益估算:                 {net:+.2f}% ann.
    
    ⚠️ 以上基于 HyENA (hyna:BTC) 当前快照，而非 HL 原生市场。
    ⚠️ HyENA 流动性较低，实际 funding 波动可能更大。
    ⚠️ 实际收益还需扣除: USDe 脱锚风险、腿断裂损失、GRVT haircut。
        """)
    else:
        print("\n  数据不完整，无法计算组合收益")
        if not hyena_data:
            print("  - HyENA (hyna:BTC) 数据缺失")
        if not grvt:
            print("  - GRVT 数据缺失")
    
    # ── Data Source Verification ───────────────────────────────────────────
    sep("DATA SOURCE VERIFICATION")
    print(f"""
  HyENA:
    API Endpoint:  POST {HL_API}
    Coin Name:     "{HYENA_BTC_COIN}" (HIP-3 prefix format)
    Query:         {{"type": "fundingHistory", "coin": "{HYENA_BTC_COIN}", ...}}
    验证方法:      与 stats.hyena.trade/funding 对比
    
  HL Native (仅作参考):
    Coin Name:     "{HL_NATIVE_BTC}"
    ⚠️ 这是 Hyperliquid 原生市场，NOT HyENA！
    
  GRVT:
    API Endpoint:  POST {GRVT_API}/ticker
    Instrument:    "BTC_USDT_Perp"
    
  Backpack:
    API Endpoint:  GET {BP_API}/fundingIntervalRates
    Symbol:        "BTC_USDC_PERP"
    """)
    
    print("=" * 72)
    print("  Done. Re-run periodically to track rate evolution.")
    print("  Compare HyENA rates with stats.hyena.trade/funding to verify.")
    print("=" * 72)


if __name__ == "__main__":
    main()
