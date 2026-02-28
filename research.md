
# Funding Rate Arbitrage Research

---

## Altcoin Funding Arb Scan (18 pairs)

> Generated: 2026-02-28
> Scanned: All 20 coins listed on both HyENA and GRVT
> Method: Actual paired historical data from both exchanges, 38-80 days per coin

### Full Ranking (by net yield at 2x leverage)

| # | Coin | Days | HyENA | GRVT | Spread | Net Yield | Pos% | Direction | GRVT OI $ |
|---|---|---|---|---|---|---|---|---|---|
| 1 | PUMP | 43d | -96.7% | +4.8% | +101.5% | **+112.0%** | 53.1% | Long H + Short G | $594K |
| 2 | FARTCOIN | 44d | +51.5% | +10.8% | +40.7% | **+51.2%** | 55.2% | Short H + Long G | $279K |
| 3 | XMR | 38d | +56.3% | +15.8% | +40.5% | **+51.0%** | 34.5% | Short H + Long G | $442K |
| 4 | ENA | 39d | +40.3% | +8.7% | +31.6% | **+42.1%** | 52.1% | Short H + Long G | $365K |
| 5 | IP | 38d | -31.8% | -4.0% | +27.9% | **+38.4%** | 58.0% | Long H + Short G | $407K |
| 6 | ZEC | 74d | +20.8% | -1.3% | +22.0% | **+32.5%** | 58.5% | Short H + Long G | $676K |
| 7 | SUI | 46d | +20.9% | +2.7% | +18.2% | **+28.7%** | 64.3% | Short H + Long G | $866K |
| 8 | LIT | 65d | +0.0% | +17.6% | +17.6% | **+28.1%** | 99.5% | Long H + Short G | $503K |
| 9 | XPL | 38d | +26.5% | +11.3% | +15.2% | **+25.7%** | 38.9% | Short H + Long G | $435K |
| 10 | XRP | 53d | +10.9% | -3.5% | +14.4% | **+24.9%** | 79.8% | Short H + Long G | $3.8M |
| 11 | DOGE | 49d | +14.8% | +1.1% | +13.7% | **+24.2%** | 71.6% | Short H + Long G | $1.3M |
| 12 | LINK | 38d | +16.3% | +7.2% | +9.1% | **+19.6%** | 64.0% | Short H + Long G | $1.7M |
| 13 | SOL | 80d | +5.9% | -2.6% | +8.5% | **+19.0%** | 72.8% | Short H + Long G | $30M |
| 14 | ADA | 38d | -5.6% | +2.8% | +8.4% | **+18.9%** | 20.7% | Long H + Short G | — |
| 15 | BCH | 38d | +5.1% | -2.4% | +7.5% | **+18.0%** | 70.9% | Short H + Long G | — |
| 16 | HYPE | 80d | +15.8% | +8.7% | +7.1% | **+17.6%** | 42.4% | Short H + Long G | $2.9M |
| 17 | LTC | 38d | +14.4% | +7.9% | +6.6% | **+17.1%** | 59.6% | Short H + Long G | — |
| 18 | BNB | 51d | +0.2% | +5.9% | +5.8% | **+16.3%** | 40.1% | Long H + Short G | — |

Net yield = funding spread + USDe 6% + GRVT 5% - 0.5% fees. All alts get 5x GRVT points.

### Key Finding

HyENA rates are consistently higher than GRVT on almost every alt. **Short HyENA + Long GRVT** wins for most coins. The few exceptions (PUMP, IP, ADA, BNB, LIT) have negative/low HyENA rates — go Long HyENA instead.

### Tiered Recommendations

**Tier 1 — Run now (good yield + liquidity):**
- **SOL** (+19.0%) — deepest OI ($30M), 72.8% positive, most liquid alt. Added to system.
- **HYPE** (+17.6%) — already live, $2.9M OI, proven
- **DOGE** (+24.2%) — $1.3M OI, 71.6% positive, reliable

**Tier 2 — High yield, watch liquidity:**
- **XRP** (+24.9%) — $3.8M OI, 79.8% positive, but thin HyENA book
- **SUI** (+28.7%) — $866K OI, 64.3% positive, thin HyENA book
- **ZEC** (+32.5%) — $676K OI, 58.5% positive

**Tier 3 — Huge yields but risky:**
- **PUMP** (+112%) — HyENA rate is -96.7% (shorts pay insanely). Unsustainable.
- **FARTCOIN** (+51%) — meme coin, $279K OI, could delist
- **XMR/ENA** (+42-51%) — great spreads but volatile (low positive %)

---

## BTC Funding Rate Arbitrage

> Generated: 2026-02-27 14:45 UTC
> Data: 80 days (2025-12-08 — 2026-02-27), 1933 matched hourly observations
> Sources: HyENA `fundingHistory` API (1h) + GRVT `POST /full/v1/funding` API (8h)

### Strategy Overview

Delta-neutral funding rate arbitrage on $BTC perpetual futures across two venues:

| | HyENA | GRVT |
|---|---|---|
| Instrument | `hyna:BTC` | `BTC_USDT_Perp` |
| Funding interval | 1h | 8h |
| Tick size | $1 | $0.1 |
| Min size | 0.00001 BTC | 0.001 BTC |
| Margin mode | Cross | Cross |

### Individual Rate Statistics (8h normalized)

| Metric | HyENA (1h -> 8h) | GRVT (8h) |
|---|---|---|
| Data points | 1,933 | 1,933 |
| Avg 8h rate | +0.00004313 | +0.00005469 |
| Annualized | **+4.72%** | **+5.99%** |
| Max (ann) | +91.49% | +10.95% |
| Min (ann) | -145.02% | -18.83% |
| Positive | 75.1% | 82.3% |

Both rates positive = **longs pay shorts** on both venues. GRVT rate is slightly higher and more stable.

### Strategy Comparison

**Strategy A: Long HyENA + Short GRVT** (CURRENT direction)
- You **pay** the lower HyENA rate, **receive** the higher GRVT rate
- Net funding spread: **+1.27% ann** (you collect)

**Strategy B: Short HyENA + Long GRVT**
- You **receive** the lower HyENA rate, **pay** the higher GRVT rate
- Net funding spread: **-1.27% ann** (you pay)

### Spread Statistics

| Metric | Strategy A (Current) | Strategy B |
|---|---|---|
| Avg spread (8h) | **+0.00001157** | -0.00001157 |
| Annualized | **+1.27%** | -1.27% |
| Max spread (ann) | +155.97% | +83.14% |
| Min spread (ann) | -83.14% | -155.97% |
| Positive periods | 29.5% | 48.1% |

### Monthly Breakdown

| Month | Strategy A (ann) | Strategy B (ann) | Winner |
|---|---|---|---|
| 2025-12 | -5.52% | **+5.52%** | B |
| 2026-01 | -1.74% | **+1.74%** | B |
| 2026-02 | **+10.81%** | -10.81% | **A** |

Strategy A wins only because of February's strong swing. Dec and Jan favored B.

### Projected Yield (Strategy A, at 3x leverage)

| Component | Yield |
|---|---|
| Funding spread (x 3/2) | +1.90% ann |
| USDe staking reward (12% / 2) | +6.0% |
| GRVT equity reward (10% / 2) | +5.0% |
| Est. trading fees | -0.5% |
| **Net yield** | **+12.40% ann** |

### Leverage Sensitivity

| Leverage | Funding (spread x L/2) | Rewards (fixed) | Fees | Net |
|---|---|---|---|---|
| 2x | +1.27% | +11.0% | -0.5% | **+11.77%** |
| 3x | +1.90% | +11.0% | -0.5% | **+12.40%** |

### Recommendation

**Strategy A (Long HyENA + Short GRVT) is correct** — but the edge is narrow (+2.5% ann over B).

The rewards (+11%) dominate the yield regardless of direction. Funding spread contributes only +1.27% to +1.90% depending on leverage. Direction matters much less for BTC than for HYPE.

**Keep current direction. No change needed.**

### Risk Factors

1. **Regime shifts**: Dec and Jan favored opposite direction. Feb swung hard to A. Fixed direction + rewards still wins.
2. **Narrow edge**: Only +1.27% ann funding spread — rewards carry the strategy.
3. **HyENA rate volatility**: Ranges from -145% to +91% ann in extremes, but median is stable.

---

# HYPE Funding Rate Arbitrage Research

> Generated: 2026-02-27 14:41 UTC
> Data: 80 days (2025-12-09 — 2026-02-27), 1921 matched hourly observations
> Sources: HyENA `fundingHistory` API (1h) + GRVT `POST /full/v1/funding` API (4h)

## Strategy Overview

Delta-neutral funding rate arbitrage on $HYPE perpetual futures across two venues:

| | HyENA | GRVT |
|---|---|---|
| Instrument | `hyna:HYPE` | `HYPE_USDT_Perp` |
| Funding interval | 1h | 4h |
| Tick size | $0.001 | $0.001 |
| Min size | 0.01 HYPE | 1.0 HYPE (integer) |
| Margin mode | Isolated only | Cross |
| Max leverage | 10x | TBD |
| OI | ~$1.59M | ~$2.89M |

## Individual Rate Statistics (8h normalized)

| Metric | HyENA (1h -> 8h) | GRVT (4h -> 8h) |
|---|---|---|
| Data points | 1,921 | 1,921 |
| Avg 8h rate | +0.00014411 | +0.00007953 |
| Annualized | **+15.78%** | **+8.71%** |
| Max (ann) | +340.41% | +14.89% |
| Min (ann) | -664.01% | -47.52% |
| Positive | 82.8% | 92.1% |

Both rates are predominantly positive = **longs pay shorts** on both venues.

## Strategy Comparison

**Strategy A: Long HyENA + Short GRVT** (same direction as BTC arb)
- You **pay** the higher HyENA rate, **receive** the lower GRVT rate
- Net funding spread: **-7.07% ann** (you pay)

**Strategy B: Short HyENA + Long GRVT** (opposite of BTC arb)
- You **receive** the higher HyENA rate, **pay** the lower GRVT rate
- Net funding spread: **+7.07% ann** (you collect)

### Spread Statistics

| Metric | Strategy A | Strategy B |
|---|---|---|
| Avg spread (8h) | -0.00006459 | **+0.00006459** |
| Annualized | -7.07% | **+7.07%** |
| Max spread (ann) | +674.96% | +329.46% |
| Min spread (ann) | -329.46% | -674.96% |
| Positive periods | 22.4% | **42.3%** |

## Monthly Breakdown

| Month | Strategy A (ann) | Strategy B (ann) | Winner |
|---|---|---|---|
| 2025-12 | -8.92% | **+8.92%** | B |
| 2026-01 | -12.56% | **+12.56%** | B |
| 2026-02 | **+0.93%** | -0.93% | A |

Strategy B wins 2 out of 3 months. Feb 2026 is basically flat (-0.93%), not a significant loss.

## Projected Yield (Strategy B, at 2x leverage)

| Component | Yield |
|---|---|
| Funding spread | +7.07% ann |
| USDe staking reward (12% / 2) | +6.0% |
| GRVT equity reward (10% / 2) | +5.0% |
| Est. trading fees | -0.5% |
| **Net yield** | **+17.57% ann + 5x GRVT points** |

### Leverage Sensitivity

| Leverage | Funding (spread x L/2) | Rewards (fixed) | Fees | Net |
|---|---|---|---|---|
| 2x | +7.07% | +11.0% | -0.5% | **+17.57%** |
| 3x | +10.61% | +11.0% | -0.5% | **+21.11%** |

Note: USDe (6%) and GRVT (5%) rewards do NOT scale with leverage — they are margin/equity-based.

## Recommendation

**Strategy B (Short HyENA + Long GRVT)** is the clear winner:
- +14.1% ann better than Strategy A on funding alone
- USDe reward is direction-agnostic (pays on shorts too)
- Opposite direction from BTC arb — each asset optimizes independently

## Risk Factors

1. **HyENA isolated margin**: Cannot share margin with BTC position. Separate liquidation. Keep leverage conservative.
2. **Thin liquidity**: ~$1K at top of HyENA book. Use wider offset (15+ bps), small position sizes.
3. **Funding volatility**: HyENA rates swing wildly (±664% ann extremes). Fixed direction + rewards still wins.
4. **Feb 2026 regime**: Funding spread briefly flipped — but only by -0.93% ann, far offset by 11% rewards.
5. **Low OI**: Keep position < 1% of venue OI to avoid market impact.

## Raw Backtest Output

```
========================================================================
  HYPE FUNDING RATE BACKTEST — HyENA (hyna:HYPE) vs GRVT (HYPE_USDT_Perp)
  2026-02-27 14:41:41 UTC
========================================================================

Fetching data...
  HyENA: 1925 records (1h intervals)
  GRVT:  3244 records (4h intervals, expanded to hourly)
  GRVT current: 0.0011% (+2.41% ann)
  GRVT OI: 103,157 HYPE, mark: $28.172

  Overlapping range: 2025-12-09 — 2026-02-27 (80 days)
  Matched hours: 1921

========================================================================
  INDIVIDUAL RATE STATISTICS (8h normalized)
========================================================================

  HyENA hyna:HYPE (1h -> 8h)  (1921 data points)
    Avg 8h Rate:    +0.00014411  (+15.78% ann)
    Max:            +0.00310873  (+340.41% ann)
    Min:            -0.00606399  (-664.01% ann)
    Median:         +0.00010000
    Positive:       82.8% | Negative: 17.2%

  GRVT HYPE_USDT_Perp (4h -> 8h)  (1921 data points)
    Avg 8h Rate:    +0.00007953  (+8.71% ann)
    Max:            +0.00013600  (+14.89% ann)
    Min:            -0.00043400  (-47.52% ann)
    Median:         +0.00010000
    Positive:       92.1% | Negative: 7.9%

========================================================================
  SPREAD ANALYSIS (actual paired data, 1921 hours)
========================================================================

  Strategy A: Long HyENA + Short GRVT (same as BTC)
    Avg spread (8h):  -0.00006459  (-7.07% ann)
    Max spread:       +0.00616399  (+674.96% ann)
    Min spread:       -0.00300873  (-329.46% ann)
    Positive periods: 22.4%

  Strategy B: Short HyENA + Long GRVT (opposite of BTC)
    Avg spread (8h):  +0.00006459  (+7.07% ann)
    Max spread:       +0.00300873  (+329.46% ann)
    Min spread:       -0.00616399  (-674.96% ann)
    Positive periods: 42.3%

========================================================================
  PROJECTED YIELD (per direction, at 2x leverage)
========================================================================

  Strategy A (Long HyENA + Short GRVT)
    Funding spread:     -7.07% ann (at 2x leverage)
    USDe reward:        +6.0% (on total capital)
    GRVT reward:        +5.0% (on total capital) + 5x points
    Est. fees:          -0.5%
    ────────────────────
    Net yield:          +3.43% ann + 5x GRVT points
    Spread positive:    22.4% of periods

  Strategy B (Short HyENA + Long GRVT)
    Funding spread:     +7.07% ann (at 2x leverage)
    USDe reward:        +6.0% (on total capital)
    GRVT reward:        +5.0% (on total capital) + 5x points
    Est. fees:          -0.5%
    ────────────────────
    Net yield:          +17.57% ann + 5x GRVT points
    Spread positive:    42.3% of periods

========================================================================
  RECOMMENDATION
========================================================================

  Strategy B (Short HyENA + Long GRVT) is better by 14.1% ann
  Reason: Collects the funding spread instead of paying it.
  USDe reward is direction-agnostic, so no penalty for shorting HyENA.

  Note: This is the OPPOSITE direction from BTC arb (Long H + Short G).
  Each asset should optimize independently.

========================================================================
  MONTHLY BREAKDOWN
========================================================================

  Month        A (Long H) ann  B (Short H) ann   Better      N
  ────────── ──────────────── ──────────────── ──────── ──────
  2025-12             -8.92%          +8.92%        B    542
  2026-01            -12.56%         +12.56%        B    744
  2026-02             +0.93%          -0.93%        A    635

========================================================================
  WEEKLY BREAKDOWN (Strategy B spread)
========================================================================

  Week               Avg 8h      Ann %   Positive     N
  ──────────── ──────────── ────────── ────────── ─────
  2025-W49     +0.00016478   +18.04%     45.5%   134
  2025-W50     +0.00000671    +0.73%     31.5%   168
  2025-W51     +0.00004914    +5.38%     47.6%   168
  2025-W52     +0.00017625   +19.30%     56.9%    72
  2026-W00     +0.00013488   +14.77%     52.1%    96
  2026-W01     +0.00011453   +12.54%     54.8%   168
  2026-W02     -0.00000775    -0.85%     20.8%   168
  2026-W03     +0.00021524   +23.57%     72.6%   168
  2026-W04     +0.00012098   +13.25%     53.0%   168
  2026-W05     +0.00018010   +19.72%     48.8%   164
  2026-W06     -0.00006473    -7.09%     28.6%   168
  2026-W07     -0.00004219    -4.62%     22.0%   168
  2026-W08     -0.00017146   -18.77%     22.5%   111

========================================================================
  Done.
========================================================================
```
