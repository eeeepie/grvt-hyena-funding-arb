# Product Requirements Document (PRD)

## Cross-Exchange Funding Rate Arbitrage System

**HyENA × GRVT**

| Field | Value |
|-------|-------|
| Version | 3.1 |
| Date | 2026-02-10 |
| Status | **Live (small-amount tested)** |
| Target User | Retail investors (≤100K USDT) |
| Maximum Capital | 100,000 USDT |
| Classification | Internal / Confidential |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Strategy Overview](#2-strategy-overview)
3. [Exchange Specifications](#3-exchange-specifications)
4. [Market Data & API Integration](#4-market-data--api-integration)
5. [Historical Funding Rate Analysis](#5-historical-funding-rate-analysis)
6. [Yield Model](#6-yield-model)
7. [Precise Hedging Execution](#7-precise-hedging-execution)
8. [Risk Management](#8-risk-management)
9. [Mirror Close (Leg Break Protection)](#9-mirror-close-leg-break-protection)
10. [Alert System](#10-alert-system)
11. [Monitoring Dashboard](#11-monitoring-dashboard)
12. [Operational Procedures](#12-operational-procedures)
13. [Implementation Deliverables](#13-implementation-deliverables)
14. [Appendix](#14-appendix)

---

## 1. Executive Summary

This document defines the requirements for an automated cross-exchange funding rate arbitrage system operating across HyENA (HIP-3 on Hyperliquid) and GRVT. The system executes delta-neutral positions to capture funding rate differentials and margin reward programs while maintaining strict risk controls suitable for retail investors. Backpack is documented as a potential diversification option but is not implemented in v3.1.

- **Target Annual Return:** 20–23% (conservative, risk-adjusted)
- **Revenue Composition:** ~22% from margin rewards (stable) + ~1.7% from funding spread (variable)
- **Maximum Capital:** 100,000 USDT
- **Maximum Leverage:** 2x per leg
- **Risk Profile:** Market-neutral; primary risks are platform-level (ADL, depeg, haircut)

---

## 2. Strategy Overview

### 2.1 Core Mechanism

The system opens equal-and-opposite (delta-neutral) BTC perpetual positions across exchanges. One leg goes long on HyENA, the other goes short on GRVT (or Backpack). The net market exposure is zero — profit comes from the funding rate differential between exchanges and the margin reward programs each platform offers.

### 2.2 Revenue Structure

| Component | Source | Annual Estimate | Stability |
|-----------|--------|-----------------|-----------|
| USDe Boosted Reward | HyENA (Long or Short) | +12% | Fixed (promotional, may adjust) |
| Equity Reward | GRVT (≤10K USDT notional) | +10% | Fixed (capped at 100K) |
| Funding Rate Spread | HyENA vs GRVT net | +1.7% | Variable (can be negative) |
| GRVT Token Airdrop | GRVT trading activity & points | Unquantified | 22% of total supply allocated; TGE target Q1 2026; S1 10% + S2 12% |
| HyENA Points | Trading activity | Unquantified | Accumulative (6-month program) |
| Ethena Points | USDe margin usage | Unquantified | Accumulative |
| Based Gold | Based ecosystem | Unquantified | Accumulative |

**Core Insight:** The alpha comes from stacking margin rewards across platforms (12% + 10% = 22%), not from funding rate arbitrage. Funding only needs to stay roughly neutral. Even if funding nets to zero, the strategy still yields ~19% from rewards alone.

### 2.3 Recommended Position Configuration

| Parameter | Primary Config | Diversified Config |
|-----------|---------------|--------------------|
| Long Leg | HyENA 100% | HyENA 100% |
| Short Leg | GRVT 100% | GRVT 50% + Backpack 50% |
| Leverage | 2x both sides | 2x both sides |
| Notional per Leg | ≤$50K | ≤$50K |
| Rebalance Frequency | Weekly or on threshold | Weekly or on threshold |

---

## 3. Exchange Specifications

### 3.1 HyENA (HIP-3 on Hyperliquid)

| Parameter | Value |
|-----------|-------|
| Market | BTC-USDe Perpetual (coin: `hyna:BTC`) |
| Margin Asset | USDe (Ethena synthetic dollar, token ID 235) |
| Settlement | Hourly (1/8 of computed 8h rate) |
| Funding Formula | F = Avg Premium + clamp(IR - Premium, -0.05%, +0.05%) |
| Interest Rate | 0.01% per 8h (11.6% APR, fixed) |
| Funding Cap | 4% per hour |
| OI Cap (BTC) | $75,000,000 |
| Total OI Cap (DEX) | $150,000,000 |
| Max Leverage | Up to 50x (varies by asset) |
| Taker Fee (Tier 0) | 1.11× standard HL rate = 0.0555% (maker rebate: 0.0167%) |
| ADL Protection | None (no vault protection) |
| API Identifier | POST `api.hyperliquid.xyz/info`, coin=`"hyna:BTC"` |
| Deployer | `0x53e655101ea361537124ef814ad4e654b54d0637` |
| Supported Assets | BTC, ETH, HYPE, SOL, LIT, ZEC, XRP, LIGHTER, BNB, DOGE, ADA, BCH, LINK, LTC, PUMP, SUI, XMR, XPL, ENA, FARTCOIN, IP |

> **⚠ CRITICAL:** HyENA (`hyna:BTC`) and Hyperliquid native (`BTC`) are completely separate markets with independent order books, funding rates, and OI. The API coin name `"hyna:BTC"` must be used — NOT `"BTC"`.

### 3.2 GRVT

| Parameter | Value |
|-----------|-------|
| Market | BTC_USDT_Perp |
| Margin Asset | USDT |
| Settlement | Auto-switching: 1h / 4h / 8h (instrument-specific, Binance-aligned) |
| Funding Formula | ClampedPremium = [Avg_Premium + clamp(0.01% − Avg_Premium, +0.05%, −0.05%)] ÷ (8/FundingIntervalHours); FundingRate = min(max(ClampedPremium, Floor), Cap) |
| Interest Rate | 0.01% per 8h (Binance-aligned) |
| Funding Cap/Floor | Per-instrument, Binance-aligned (e.g. ±0.3000% per 8h) |
| Auto-Switch | If avg hourly premium exceeds cap over full interval, switches to 1h regime |
| Risk Mechanism | Socialized loss haircut on withdrawals (no ADL) |
| Equity Reward | 10% APR on margin (≤10K USDT notional) |
| Taker Fee (Level 1) | 0.045% (maker rebate: -0.0001%) |
| Taker Fee (Level 2) | 0.042% (30D vol >$100K or asset >$100K) |
| Withdrawal Fee | 15 USDT (Ethereum), 1.01 USDT (Arbitrum/BSC) |
| Token Airdrop | 22% of total GRVT supply; S1 10% (early adopters) + S2 12%; TGE target Q1 2026 |
| API Base (Market Data) | `https://market-data.grvt.io` |
| API Base (Trading) | `https://edge.grvt.io` (requires API key auth) |
| WebSocket (Market Data) | `wss://market-data.grvt.io/ws/full` or `/ws/lite` |
| WebSocket (Trading) | `wss://trades.grvt.io/ws/full` (requires cookie + X-Grvt-Account-Id) |
| Instrument Spec | `base_decimals: 9`, `tick_size: 0.1`, `min_size: 0.001`, `min_notional: $100` |
| Leverage | Default up to 50x (cross margin). **Must set manually in GRVT frontend UI — no API.** |

### 3.3 Backpack

| Parameter | Value |
|-----------|-------|
| Market | BTC_USDC_PERP |
| Margin Asset | USDC |
| Settlement | Hourly (daily rate / 24) |
| ADL Mechanism | Delta-neutral positions deprioritized in queue |
| API Endpoint | GET `api.backpack.exchange/api/v1/fundingIntervalRates` |

---

## 4. Market Data & API Integration

### 4.1 Data Endpoints

**HyENA (all POST to `https://api.hyperliquid.xyz/info`, no auth):**

| Purpose | Request Body | Notes |
|---------|-------------|-------|
| DEX metadata | `{"type":"meta","dex":"hyna"}` | Universe info, szDecimals |
| Funding history | `{"type":"fundingHistory","coin":"hyna:BTC"}` | Settled rates |
| L2 orderbook | `{"type":"l2Book","coin":"hyna:BTC"}` | Best bid/ask for mid price |
| All HIP-3 DEXs | `{"type":"perpDexs"}` | List all builder DEXs |
| DEX status | `{"type":"perpDexStatus","dex":"hyna"}` | OI caps, limits |
| Predicted funding | `{"type":"predictedFundings","dex":"hyna"}` | May not be supported |
| HL Native (reference) | `{"type":"metaAndAssetCtxs"}` | Does NOT include HIP-3 assets |

**HyENA Trading (POST to `https://api.hyperliquid.xyz/exchange`, requires signature):**

| Purpose | Notes |
|---------|-------|
| Place order | Must include builder code `0x1924b8561eeF20e70Ede628A296175D358BE80e5` with fee=0 for points tracking |
| Modify order | Builder config inherited from original order |

> **⚠ Builder Code Required:** All HyENA API orders must include the builder code to qualify for Ethena Points and HyENA Points. This does NOT charge additional fees — it is purely for tracking.

**GRVT Market Data (POST to `https://market-data.grvt.io/full/v1/...`, no auth):**

| Purpose | Endpoint | Request Body |
|---------|----------|-------------|
| Ticker (full) | `full/v1/ticker` | `{"instrument":"BTC_USDT_Perp"}` |
| Mini ticker | `full/v1/mini` | `{"instrument":"BTC_USDT_Perp"}` |
| Orderbook | `full/v1/book` | `{"instrument":"BTC_USDT_Perp"}` |
| Funding history | `full/v1/funding` | `{"instrument":"BTC_USDT_Perp","limit":500}` |
| Instrument spec | `full/v1/instrument` | `{"instrument":"BTC_USDT_Perp"}` |

**GRVT Market Data WebSocket (recommended over polling):**

| Purpose | Stream | Selector |
|---------|--------|----------|
| Ticker snapshot | `v1.ticker.s` | `BTC_USDT_Perp@500` |
| Ticker delta | `v1.ticker.d` | `BTC_USDT_Perp@500` |
| Mini ticker | `v1.mini.s` | `BTC_USDT_Perp@500` |
| Book snapshot | `v1.book.s` | `BTC_USDT_Perp@500-1-10` |

> **Note:** GRVT recommends WebSocket subscriptions over REST polling for real-time data.

**GRVT Trading (requires API key auth via cookie):**

| Purpose | Endpoint |
|---------|----------|
| Auth | POST `https://edge.grvt.io/auth/api_key/login` |
| Create order | `full/v1/create_order` |
| Cancel order | `full/v1/cancel_order` |
| Positions (WS) | Subscribe `v1.state` stream |

**GRVT Ticker Response Fields (relevant):**

```
funding_rate_8h_curr  // Current 8h funding rate
funding_rate_8h_avg   // Average 8h funding rate
interest_rate         // Interest rate component
mark_price            // Mark price
index_price           // Index price
best_bid_price / best_ask_price
open_interest
```

**Backpack (REST, no auth for public data):**

| Purpose | Endpoint |
|---------|----------|
| Funding rates | `GET api.backpack.exchange/api/v1/fundingIntervalRates?symbol=BTC_USDC_PERP` |
| Ticker | `GET api.backpack.exchange/api/v1/ticker?symbol=BTC_USDC_PERP` |

### 4.2 Key API Notes

- HIP-3 assets do **NOT** appear in standard `metaAndAssetCtxs`. Must use `{"type":"meta","dex":"hyna"}` for universe data.
- HyENA current funding rate: most recent `fundingHistory` entry (settled hourly).
- HyENA mid price: `l2Book` → (best_bid + best_ask) / 2.
- HyENA API orders **must** include builder code `0x1924b8561eeF20e70Ede628A296175D358BE80e5` (fee=0) for points eligibility.
- GRVT ticker returns `funding_rate_8h_curr` as a decimal (0.0003 = 0.03% per 8h).
- GRVT funding formula now uses clamp-based computation aligned with Binance (updated Oct 15, 2025).
- GRVT can auto-switch settlement intervals (1h/4h/8h) when premium exceeds cap. Track `next_funding_time` via ticker WS.
- GRVT strongly recommends WebSocket subscriptions (`v1.ticker.s`, `v1.book.s`) over REST polling.
- GRVT trading API requires API key auth: provision key in UI → POST to `/auth/api_key/login` → use returned cookie + `X-Grvt-Account-Id` header.
- Backpack uses offset-based pagination for `fundingIntervalRates` history.

### 4.3 Polling Frequency

| Data Type | Interval | Rationale |
|-----------|----------|-----------|
| Current funding rates | Every 5 minutes | Detect spread changes and alert conditions |
| L2 book / mid price | Every 1 minute | Monitor spread, detect liquidity issues |
| Funding history (backfill) | Every 1 hour | Update rolling averages and statistics |
| DEX status / OI caps | Every 15 minutes | Monitor OI cap utilization |
| USDe price (peg monitor) | Every 1 minute | Detect depegging events |
| Position status (both legs) | Every 10 seconds | **Mirror close detection — see Section 9** |

---

## 5. Historical Funding Rate Analysis & Backtest

Based on 30-day historical data (2026-01-11 to 2026-02-10), 731 hourly HyENA records and 500 GRVT 8h records, aligned to 90 comparable 8h periods.

### 5.1 Funding Rate Statistics by Phase

| Metric | Phase 1 (Jan 11–31) | Phase 2 (Feb 1–10) | Full Period |
|--------|---------------------|---------------------|-------------|
| 8h periods | 62 | 28 | 90 |
| HyENA avg 8h rate | +0.000088 (+9.6% ann) | -0.000080 (-8.8% ann) | +0.000036 (+3.9% ann) |
| GRVT avg 8h rate | +0.000061 (+6.7% ann) | +0.000047 (+5.1% ann) | +0.000056 (+6.2% ann) |
| Spread (GRVT − HyENA) | -0.000027 (-3.0% ann) | **+0.000127 (+13.9% ann)** | +0.000021 (+2.3% ann) |
| Spread positive % | 33.9% | **78.6%** | 47.8% |

### 5.2 Strategy A Backtest (Long HyENA + Short GRVT)

**Funding P&L breakdown (pure funding, excluding rewards):**

| Period | HyENA Long Funding | GRVT Short Funding | Net Funding |
|--------|--------------------|--------------------|-------------|
| Jan (62 periods) | -9.6% ann (pays) | +6.7% ann (receives) | -2.9% ann |
| Feb (28 periods) | **+8.8% ann (receives)** | +5.1% ann (receives) | **+13.9% ann** |
| Full 30 days | -3.9% ann (pays) | +6.2% ann (receives) | +2.3% ann |

**Key insight: Feb HyENA funding flipped negative** → longs receive funding AND USDe yield, creating a double-collection window.

### 5.3 Total Return Projection (with rewards)

| Component | Annual Estimate | Stability |
|-----------|-----------------|-----------|
| Funding spread (Strategy A) | +2.3% | Variable (can be -3% to +14%) |
| USDe Boosted Reward | +12.0% | Fixed (promotional) |
| GRVT Equity Reward | +10.0% | Fixed (≤10K notional) |
| Fees + slippage | -2.8% | Estimated |
| **Net expected** | **~21.5%** | Conservative |

### 5.4 Directional Reversal Analysis (researched, rejected)

We evaluated auto-reversing direction (Short HyENA + Long GRVT) when spread is negative:

| Flip Threshold | Flips/month | Ann. Return (pure funding) | vs Fixed Strategy A |
|----------------|-------------|---------------------------|---------------------|
| 0% (flip immediately) | 9 | -14.5% | Worse (-16.8%) |
| 10% | 3 | -1.0% | Worse (-3.3%) |
| 20% | 1 | +2.8% | Marginal (+0.5%) |

**Conclusion: Auto-reversal not viable.** Regime switches too frequently (avg 1–2 days), flip cost ~21bps/event (4 trades) accumulates fast, and signal lags the market. USDe + GRVT rewards (+22%) dwarf the funding spread in all scenarios, making direction less important than staying in position.

### 5.5 Key Observations

1. **Rewards dominate returns.** The +22% from USDe + GRVT rewards makes the strategy profitable even when funding is -3% net.
2. **HyENA funding is more volatile** (range 2.9x wider than HL native) due to lower liquidity.
3. **Feb 2026 shows a structural shift** — HyENA funding turned negative, making Strategy A funding-positive for the first time.
4. **Fixed direction is optimal.** Stay Long HyENA + Short GRVT; only manually reverse if spread sustains >-20% ann for 1+ weeks.

> **⚠ Note:** HyENA launched December 9, 2025 (~2 months of data). Funding dynamics may change as liquidity deepens.

---

## 6. Yield Model

### 6.1 Scenario Analysis

Position: HyENA Long + GRVT Short, equal notional (BTC quantity-matched), 2x leverage on both sides.

| Component | Scenario A: Normal (90%) | Scenario B: Negative Window (10%) |
|-----------|--------------------------|-----------------------------------|
| HyENA Long Funding | -9.26% (pays) | +35% (receives, extreme snapshot) |
| GRVT Short Funding | +10.95% (receives) | +10.95% (receives) |
| Funding Net | +1.69% | +46% |
| USDe Reward (12%) | +12.00% | +12.00% |
| GRVT Equity (10%) | +10.00% | +10.00% |
| Rewards Subtotal | +22.00% | +22.00% |
| Fees + Risk Premium | -2.80% | -2.80% |
| **Scenario Annual** | **~20.9%** | **~65%+** |

### 6.2 Weighted Expected Return

| Calculation | Value |
|-------------|-------|
| Weighted = 90% × 20.9% + 10% × 40% | 22.8% annual |
| Conservative Estimate (risk-adjusted) | **20–23% annual** |
| Revenue: Rewards (stable) | ~22% |
| Revenue: Funding (variable) | ~1.7% (can be negative) |

### 6.3 Cost Breakdown

| Cost Item | Estimate | Frequency |
|-----------|----------|-----------|
| HyENA taker fee (1.11× HL Tier 0) | 0.0555% per trade | Per entry/exit/rebalance |
| GRVT taker fee (Level 1) | 0.045% per trade | Per entry/exit/rebalance |
| Execution slippage (both legs) | 0.03–0.10% | Per entry/exit |
| Rebalancing (est. 4x/month) | ~0.40% annual | Monthly |
| Mirror close emergency cost | ~0.10% per event | Rare (est. <2x/year) |
| Risk premium (unquantified risks) | -2.00% annual | Continuous |

---

## 7. Precise Hedging Execution

### 7.1 Core Principle: Anchor on BTC Quantity, Not USD Notional

HyENA (BTC/USDe) and GRVT (BTC/USDT) have different quote currencies, oracles, and liquidity profiles. Their prices will always differ slightly ($30–$100 typically). **This does not affect delta neutrality.**

Delta-neutral requires matching BTC quantity on both legs, not USD value:

```
Correct:
  HyENA long  0.5 BTC (notional ~$48,525 in USDe)
  GRVT  short 0.5 BTC (notional ~$48,510 in USDT)
  → BTC quantity matched → delta neutral ✓
  → USD notional difference ($15) → irrelevant

Wrong:
  HyENA long  $50,000 → 0.5152 BTC
  GRVT  short $50,000 → 0.5154 BTC
  → 0.0002 BTC unhedged → small but unnecessary
```

### 7.2 Price Difference Sources

| Source | Typical Magnitude | Impact on Hedging |
|--------|-------------------|-------------------|
| Quote currency (USDe vs USDT) | 0.05%–0.30% | Does NOT affect delta if BTC qty matched |
| Oracle difference (HL vs GRVT) | $10–$50 | Does NOT affect delta if BTC qty matched |
| Depth/liquidity (HyENA thinner) | 0.01%–0.10% slippage | One-time execution cost |
| Execution time gap | $50–$200 in <30s | One-time execution cost |

### 7.3 Concurrent Execution Specification

All entry, exit, and rebalancing operations use concurrent script execution:

1. Determine target BTC quantity based on allocated capital and 2x leverage.
2. Query both L2 books simultaneously for current mid prices.
3. Submit aggressive limit orders (mid ± 2 bps) on both platforms concurrently via `asyncio`.
4. Both orders submitted within <1 second.
5. After fills, compare actual BTC quantities. If mismatch > 0.001 BTC, submit adjustment order on the side with less.
6. For HyENA: include builder code `0x1924b8561eeF20e70Ede628A296175D358BE80e5` (fee=0) in every order for points tracking.
7. For GRVT: authenticate via API key → cookie flow before submitting; use `full/v1/create_order` endpoint.
8. Expected execution cost: <0.10% one-time (both legs combined).

### 7.4 Contract Precision Alignment

Before opening positions, query both platforms for minimum order size and step size:

- HyENA: `szDecimals` from `{"type":"meta","dex":"hyna"}` response.
- GRVT: `base_decimals: 3`, `min_size: "0.01"`, `tick_size: "0.01"` from `full/v1/instrument` response.
- Use the coarser precision as the common precision for both legs.

Example: If HyENA allows 0.001 BTC steps and GRVT allows 0.01 BTC steps (`min_size`), all orders should be rounded to 0.01 BTC.

### 7.5 USDe/USDT Exchange Rate Risk

This is a **residual risk** that cannot be hedged within the strategy:

- HyENA collateral is USDe; GRVT collateral is USDT.
- If USDe depegs 1% vs USDT, HyENA margin purchasing power drops ~1%.
- BTC/USDe price rises (USDe cheaper → more USDe per BTC), creating a paper gain on HyENA long, but the margin itself is worth less.
- Net effect: ~1% erosion of HyENA-side capital per 1% depeg.

**Mitigation:** Monitor USDe/USDT price continuously. Alert at >0.5% deviation. Exit at >1%. (See Section 10 Alert System.)

### 7.6 Delta Drift & Rebalancing

After entry, BTC quantity on each leg remains constant regardless of price movement. However, margin ratios drift:

```
Example: Open at BTC = $97,000, both legs 0.5 BTC, 2x leverage

BTC rises to $107,000 (+10.3%):
  HyENA long 2x: margin ratio improves, effective leverage → ~1.66x
  GRVT  short 2x: margin ratio deteriorates, effective leverage → ~2.52x
  → BTC quantity unchanged → still delta neutral
  → But GRVT margin is stressed → need to rebalance margin
```

**Rebalancing Rules:**

| Trigger | Action |
|---------|--------|
| Either leg leverage > 3x | Add margin immediately or reduce position |
| Leverage difference > 1x between legs | Transfer profits from winning leg to losing leg |
| Weekly scheduled check | Review and rebalance regardless of triggers |
| Notional drift > 5% (due to partial fills / rounding) | Adjust BTC quantity on mismatched leg |

---

## 8. Risk Management

### 8.1 Risk Classification

| Risk | Severity | Probability | Impact | Mitigation |
|------|----------|-------------|--------|------------|
| Leg Break (ADL on one side) | **Critical** | Low | Naked exposure on surviving leg | **Mirror Close (Section 9)** |
| USDe Depegging | **Critical** | Very Low | Collateral value erosion | Alert >0.5%; exit >1% |
| GRVT Socialized Loss Haircut | High | Low | Withdrawal reduced by haircut % | Monitor insurance fund; diversify to Backpack |
| Funding Rate Reversal | Medium | Medium | Net funding turns negative | Funding rate alert system |
| HyENA OI Cap Hit | Medium | Medium | Cannot increase position | Monitor OI vs cap; stay <5% of cap |
| Reward Program Changes | Medium | Medium | 12% or 10% rates reduced | Track announcements; adjust model |
| Platform Downtime / API | Medium | Low | Cannot rebalance or exit | Multi-platform; manual exit procedure |
| Execution Time Gap | Low | High | Brief directional exposure during entry/exit | Concurrent execution; <1s gap |
| USDe/USDT Rate Divergence | Low | Medium | Margin value mismatch | Monitor; exit at >1% deviation |

### 8.2 Position Sizing Rules

- **Max leverage: 2x.** Both legs must survive >30% single-direction price move without liquidation.
- **Max position: 5% of HyENA BTC OI cap (~$3.75M).** Larger positions distort funding to own disadvantage.
- **Reserve margin: 30% undeployed on each platform.** Available for emergency margin top-up during flash crashes.
- **Single platform cap: 40–50% of total capital.** No more than half of total funds on any one exchange.
- **For 100K USDT accounts:** ~$35K deployed per leg (at 2x = ~$70K notional per leg), ~$15K reserve per platform.

### 8.3 Mirror Close (Primary Protection Mechanism)

The system employs three layers of protection:

| Layer | Purpose | Mechanism |
|-------|---------|-----------|
| Layer 1: Leverage Control | Prevent liquidation | Max 2x; 30% reserve margin; liquidation distance alert |
| Layer 2: Mirror Close | Prevent naked exposure after ADL | Auto-close surviving leg within seconds (see Section 9) |
| Layer 3: Funding Rate Monitoring | Prevent chronic bleed | Alert when net funding < -20% annualized for 6h+ |

**Additional safeguard — Circuit Breaker:**

If BTC price moves >15% in a single 1-hour candle, the system pauses all automated operations (rebalancing, new entries) and issues a Critical alert for manual review.

---

## 9. Mirror Close (Leg Break Protection)

### 9.1 Rationale

This is the single most critical safety mechanism for a retail-facing product. When one leg is forcibly reduced or closed (via ADL on HyENA, or socialized loss on GRVT), the surviving leg becomes a naked directional position. A retail user sleeping through this event could lose 20–30% of capital in hours.

**Mirror close is non-negotiable for production deployment.**

### 9.2 Detection Mechanism

The system polls position status on both platforms every **10 seconds**:

```
For each polling cycle:
  1. Query HyENA position size (hyna:BTC)
  2. Query GRVT position size (BTC_USDT_Perp)
  3. Compare to last known sizes

  If |current_size - last_known_size| > threshold on EITHER leg
    AND the change was NOT initiated by the system:
    → TRIGGER MIRROR CLOSE on the OTHER leg
```

### 9.3 Detection Triggers

| Event | Detection Signal | Response |
|-------|------------------|----------|
| HyENA ADL (full) | HyENA position size drops to 0 | Market-close entire GRVT position |
| HyENA ADL (partial) | HyENA position size decreases by ΔQ | Market-close ΔQ on GRVT |
| GRVT socialized loss | GRVT effective position or margin reduced | Market-close matching amount on HyENA |
| HyENA liquidation | Position size drops to 0, margin zeroed | Market-close entire GRVT position |
| API failure on one side | 3+ consecutive poll failures on one platform | Issue Critical alert; prepare manual mirror close |

### 9.4 Execution Specification

```
MIRROR CLOSE PROCEDURE:

1. DETECT: Position change on Leg A (not initiated by system)
   - Change amount: ΔQ BTC
   - Direction: decrease (ADL, liquidation, or haircut)

2. CALCULATE: Required close on Leg B
   - Close amount = ΔQ BTC (exact match)
   - Order type: MARKET (taker)
   - Priority: IMMEDIATE (skip all queue, rate limit bypass)

3. EXECUTE: Submit market close on Leg B
   - Max slippage tolerance: 0.5% (wider than normal to ensure fill)
   - If partial fill: re-submit remaining at market until fully closed
   - Timeout: 30 seconds per attempt, max 3 attempts

4. VERIFY: Confirm both legs are balanced or fully closed
   - If Leg A fully closed → Leg B must be fully closed
   - If Leg A partially reduced → Leg B reduced by same ΔQ
   - Log all execution details

5. NOTIFY: Critical alert via all channels
   - Include: which leg triggered, amount closed, execution price, slippage
   - Recommend: do not re-enter until manual review complete
```

### 9.5 Edge Cases

| Scenario | Handling |
|----------|----------|
| Both legs ADL'd simultaneously | No action needed — both closed |
| API down on surviving leg | Retry 3x at 5-second intervals; if still down, alert for manual intervention |
| Partial ADL leaves <$1,000 notional | Close remaining on both legs (too small to manage) |
| Mirror close during high volatility | Accept higher slippage (up to 0.5%); better than naked exposure |
| False positive (position decreased due to system's own rebalance) | System must tag all self-initiated trades; ignore tagged changes |

### 9.6 Cost Analysis

| Item | Cost |
|------|------|
| Market taker fee (one side) | ~0.05% |
| Expected slippage in normal conditions | 0.02–0.05% |
| Expected slippage in extreme conditions | 0.10–0.50% |
| Total mirror close cost per event | ~0.10–0.55% |
| Estimated frequency | <2 events per year |
| Annual cost impact | <0.5% — negligible vs 20%+ return |

---

## 10. Alert System

### 10.1 Alert Definitions

| Alert | Condition | Severity | Action |
|-------|-----------|----------|--------|
| **Mirror Close Triggered** | Position reduced on either leg (not self-initiated) | **Emergency** | Auto-execute mirror close; notify all channels |
| **Liquidation Proximity** | Price within 20% of liquidation price on either leg | **Critical** | Add margin immediately |
| **USDe Depeg Exit** | USDe/USD deviation > 1.0% | **Critical** | Close all HyENA positions |
| **USDe Depeg Warning** | USDe/USD deviation > 0.5% | **Critical** | Prepare exit; monitor trend |
| **Circuit Breaker** | BTC price moves >15% in 1 hour | **Critical** | Pause all automation; manual review |
| **Funding Spread Negative** | Net annualized < -20% for 6h+ | **Warning** | Review; consider closing |
| **HyENA Funding Spike** | 8h rate > +0.03% | **Warning** | Monitor closely for reversal |
| **OI Cap Approaching** | Position > 4% of HyENA OI cap | **Warning** | Do not increase size |
| **API Failure** | 3+ consecutive poll failures | **Warning** | Switch to manual monitoring |
| **GRVT Interval Change** | Settlement interval changed | **Info** | Verify settlement times |
| **Extreme Negative Funding** | HyENA 8h rate < -0.05% | **Info** | Dual-collection window; verify |

### 10.2 Notification Channels

| Severity | Channels | Response Time Target |
|----------|----------|---------------------|
| Emergency | Push + SMS + Telegram + auto-action | Immediate (automated) |
| Critical | Push + SMS + Telegram | <5 minutes (human review) |
| Warning | Telegram + email | <1 hour |
| Info | Dashboard log + optional Telegram | Next review cycle |

---

## 11. Monitoring Dashboard

### 11.1 Real-Time Display

1. **Funding Rate Panel:** HyENA (`hyna:BTC`) current 8h rate, GRVT current 8h rate, net spread, annualized estimates, direction indicators, and countdown to next settlement.
2. **Historical Chart:** Rolling 7-day and 30-day funding rate for HyENA, GRVT, and HL native (reference), plus spread overlay.
3. **Position Health:** Margin ratio on each leg, liquidation price, distance to liquidation (%), unrealized P&L per leg and net, BTC quantity verification (match check).
4. **Mirror Close Status:** Green (both legs healthy) / Yellow (API latency >5s) / Red (size mismatch detected). Last 10-second poll timestamp.
5. **Yield Tracker:** Cumulative funding income, cumulative rewards accrued, fees paid, mirror close costs, net return to date, projected annualized return.
6. **Risk Indicators:** USDe peg status, GRVT insurance fund, HyENA OI vs cap, active alerts, circuit breaker status.

### 11.2 Periodic Reports

| Frequency | Contents |
|-----------|----------|
| Daily | Funding income summary, reward accruals, net P&L, alert log, mirror close events (if any) |
| Weekly | Rolling return analysis, funding rate trend, rebalance actions, reward claim status, hedge accuracy (BTC qty match %) |
| Monthly | Full performance attribution, risk event log, strategy parameter review, reward program status, USDe peg deviation history |

---

## 12. Operational Procedures

### 12.1 Position Entry

1. Verify funding rate spread is favorable (HyENA funding in +5% to +15% annualized range, not during extremes).
2. Verify USDe/USDT peg is within 0.3%.
3. Deposit USDe to HyENA and USDT to GRVT (or USDC to Backpack).
4. Query both L2 books for current prices; determine BTC quantity based on allocated capital at 2x leverage.
5. Align quantity to common precision (coarser of two platforms' `szDecimals` / `step_size`).
6. Submit concurrent aggressive limit orders on both platforms (target <1 second gap).
7. Verify fills: confirm BTC quantity match within 0.001 BTC tolerance.
8. Record entry prices, margin amounts, liquidation prices, and BTC quantities for both legs.
9. Enable mirror close monitoring (10-second polling).
10. Configure all alert thresholds.

### 12.2 Rebalancing

| Trigger | Action |
|---------|--------|
| Either leg leverage > 3x | Add margin from reserve or reduce position on both legs proportionally |
| Leverage difference > 1x | Transfer profits from winning leg to losing leg (withdraw + deposit) |
| BTC quantity mismatch > 0.001 BTC | Adjust on mismatched leg to re-align |
| Weekly scheduled check | Review all parameters and rebalance regardless of triggers |
| Margin ratio < 50% on either leg | Immediate margin top-up or proportional reduction |

### 12.3 Position Exit

1. Pause mirror close monitoring (prevent false trigger during intentional close).
2. Submit concurrent market close orders on both platforms (target <1 second gap).
3. Verify both legs fully closed within 60 seconds.
4. Withdraw funds from both platforms.
5. Log final P&L, total funding received, total rewards claimed, total fees paid, and any mirror close events during the position's lifetime.

Exit triggers: reward program discontinuation making strategy unprofitable, sustained negative funding spread exceeding reward income for 48+ hours, USDe depeg >1%, platform security incident, strategic reallocation, or circuit breaker with manual decision to exit.

### 12.4 Reward Claiming

| Reward | Claim Method | Frequency |
|--------|-------------|-----------|
| HyENA USDe Boosted | Rewards dashboard | Weekly |
| GRVT Equity Reward | Auto-accrual | Continuous |
| HyENA Points | Accumulative (6-month program, 24 epochs, 100M pts/week) | End of program |
| Ethena Exchange Points | Passive accrual via USDe usage | Ongoing |
| Based Gold | Passive accrual | Ongoing |

Note: Ensure positions are held >1 hour to qualify for HyENA boosted rewards.

---

## 13. Implementation Deliverables

| Phase | Deliverable | Priority | Status |
|-------|-------------|----------|--------|
| Phase 1 | Monitoring script (`btc_funding_compare_v3.py`) | P0 | ✅ Delivered |
| Phase 1 | Trading system (`main.py` + modules) | P0 | ✅ Delivered & tested |
| Phase 1 | Concurrent entry/exit execution | P0 | ✅ Delivered |
| Phase 1 | Mirror close detection & execution | P0 | ✅ Delivered |
| Phase 1 | 10-second position polling | P0 | ✅ Delivered |
| Phase 1 | Funding rate CSV logging (5min interval) | P0 | ✅ Delivered |
| Phase 1 | Pre-trade leverage guard | P0 | ✅ Delivered |
| Phase 1 | Interactive entry (USD amount + leverage input) | P0 | ✅ Delivered |
| Phase 1 | Historical spread backtest (30d, Section 5) | P1 | ✅ Delivered |
| Phase 1 | USDe peg monitor + circuit breaker | P0 | ✅ Delivered |
| Phase 2 | Telegram bot for alerts | P1 | Pending |
| Phase 2 | Real-time web dashboard | P1 | Pending |
| Phase 2 | Multi-asset analysis (ETH, SOL) | P2 | Pending |
| Phase 2 | Automated rebalancing on drift threshold | P2 | Pending |
| Phase 2 | Integrated P&L tracking and reporting | P2 | Pending |

---

## 14. Appendix

### 14.1 ADL Mechanism Comparison

| Feature | HyENA | GRVT | Backpack |
|---------|-------|------|----------|
| Mechanism | ADL (no vault protection) | Socialized loss haircut | ADL with delta-neutral deprioritization |
| Trigger | Insurance fund depleted | Withdrawal during deficit | Insurance fund depleted |
| Impact on Strategy | Long may be auto-deleveraged | Short withdrawal amount reduced | Short deprioritized in ADL queue |
| Mirror Close Trigger | Yes — immediate | Yes — on detection | Yes — immediate |
| Mitigation | Low leverage; mirror close | Monitor insurance fund; mirror close | Best short-side protection |

### 14.2 Fee Tier Tables

**HyENA (1.11× Hyperliquid Perp Fees):**

| Tier | 14D Volume ($) | Taker (Perp) | Maker (Perp) |
|------|----------------|--------------|--------------|
| 0 | $0 | 0.0555% | 0.0185% |
| 1 | >$5M | 0.0493% | 0.0148% |
| 2 | >$25M | 0.0432% | 0.0099% |
| 3 | >$100M | 0.0370% | 0.0049% |
| 4 | >$500M | 0.0345% | 0.000% |
| 5 | >$2B | 0.0321% | 0.000% |
| 6 | >$7B | 0.0296% | 0.000% |

Notes: Maker rebate tiers and HYPE staking discounts (5%–40%) also apply. Spot-to-spot pairs (e.g. USDe/USDC) have 80% reduced fees.

**GRVT:**

| Level | Maker | Taker | 30D Volume ($) | Total Asset ($) |
|-------|-------|-------|----------------|-----------------|
| 1 | -0.0001% | 0.045% | $0 | n.a. |
| 2 | -0.0004% | 0.042% | >$100K | >$100K |
| 3 | -0.0008% | 0.039% | >$500K | >$200K |
| 4 | -0.001% | 0.037% | >$1M | >$500K |
| 5 | -0.0015% | 0.034% | >$10M | >$1M |

Notes: All levels have negative maker fees (maker receives rebate). Tiers updated daily at 8 AM UTC. Meet ANY of: 30D volume, option volume, or total asset threshold.

### 14.3 Funding Rate Settlement Comparison

| Exchange | Settlement Interval | Rate Format | Annualization |
|----------|--------------------| ------------|---------------|
| HyENA | Hourly (1/8 of 8h rate) | Decimal (0.000100 = 0.01%) | × 3 × 365 = × 1095 |
| GRVT | 8h (auto-switching) | Percentage (0.01 = 0.01%) | × 3 × 365 = × 1095 |
| Backpack | Hourly (daily/24) | Decimal | × 24 × 365 = × 8760 |

### 14.4 Glossary

| Term | Definition |
|------|-----------|
| HIP-3 | Hyperliquid protocol upgrade enabling builder-deployed custom perpetual DEXs |
| hyna:BTC | HyENA's BTC-USDe perpetual contract identifier in the Hyperliquid API |
| Funding Rate | Periodic fee exchanged between long and short holders to anchor perp price to spot |
| Delta-Neutral | Combined position with zero net directional market exposure |
| Leg Break | One side of a hedged position being liquidated, leaving the other side exposed |
| Mirror Close | Automated closure of the surviving leg after detecting a leg break |
| USDe | Ethena's synthetic dollar stablecoin, used as collateral on HyENA |
| ADL | Auto-Deleveraging: forced position reduction when insurance fund is depleted |
| Socialized Loss | GRVT's alternative to ADL where losses are distributed via withdrawal haircuts |
| OI Cap | Maximum open interest allowed on a HyENA market (BTC: $75M, total DEX: $150M) |
| Circuit Breaker | System pause triggered by extreme price moves (>15% in 1 hour) |
| Boosted Rewards | HyENA's promotional 12% APY on eligible USDe margin for qualifying positions |
| Builder Code | HyENA's tracking identifier for API orders; required for points eligibility (fee=0, no additional cost) |
| GRVT TGE | GRVT Token Generation Event, targeted Q1 2026; 22% of supply allocated to community airdrop |

### 14.5 Reference Scripts

| Script | Purpose | Status |
|--------|---------|--------|
| `btc_funding_compare_v3.py` | Real-time funding rate comparison with correct `hyna:BTC` endpoint | Active |
| `btc_funding_compare_v2.py` | **Deprecated:** uses incorrect HL native BTC endpoint | Do not use |

### 14.6 Data Verification Sources

| Data Point | Primary Source | Cross-Reference |
|------------|---------------|-----------------|
| HyENA funding rate | `api.hyperliquid.xyz/info` (hyna:BTC) | `stats.hyena.trade/funding` |
| GRVT funding rate | `market-data.grvt.io/full/v1/ticker` | GRVT trading interface |
| USDe peg | CoinGecko / DEX aggregator | Curve USDe/USDT pool |
| BTC spot reference | Hyperliquid oracle | Binance / Coinbase spot |

---

**Disclaimer:** This document is for informational purposes only. It does not constitute financial advice. Cryptocurrency derivatives trading involves substantial risk of loss. Past funding rate performance does not guarantee future results. Reward programs may be modified or discontinued at any time. Conduct independent due diligence before deploying capital.
