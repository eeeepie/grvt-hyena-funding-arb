"""
Configuration for Funding Rate Arbitrage System.
Loads credentials from environment variables (.env file).
Supports multiple assets (BTC, HYPE) via AssetConfig.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


# ============================================================================
# Asset Configuration — per-asset market parameters
# ============================================================================

@dataclass(frozen=True)
class AssetConfig:
    """Per-asset parameters for both exchanges."""
    name: str                        # "BTC" or "HYPE"

    # HyENA
    hyena_coin: str                  # "hyna:BTC" or "hyna:HYPE"
    hyena_sz_decimals: int           # 5 for BTC, 2 for HYPE
    hyena_tick_decimals: int         # 0 for BTC ($1), 3 for HYPE ($0.001)

    # GRVT
    grvt_instrument: str             # "BTC_USDT_Perp" or "HYPE_USDT_Perp"
    grvt_tick_decimals: int          # 1 for BTC ($0.1), 3 for HYPE ($0.001)
    grvt_min_size: float             # 0.001 for BTC, 1.0 for HYPE
    grvt_size_decimals: int          # 3 for BTC, 0 for HYPE (integer)
    grvt_min_notional: float         # 100 for BTC, 5 for HYPE
    grvt_underlying_id: int          # 5 for BTC, 41 for HYPE
    grvt_quote_id: int = 3           # USDT = 3

    # Funding intervals (for normalization)
    grvt_funding_hours: int = 8      # 8 for BTC, 4 for HYPE
    hyena_funding_hours: int = 1     # always 1h

    # Direction: which side to take on each exchange
    # True = HyENA buys (long), False = HyENA sells (short)
    hyena_is_buy: bool = True

    # Position threshold for "flat" detection
    flat_threshold: float = 0.001    # BTC: 0.001, HYPE: 0.5

    # Sizing: quantity precision used for GRVT (bottleneck)
    quantity_precision: int = 3      # 3 for BTC, 0 for HYPE

    # Offset for thin books
    aggressive_limit_offset_bps: float = 5.0  # 5 for BTC, 10-20 for HYPE

    # Qty mismatch threshold
    qty_mismatch_threshold: float = 0.001


BTC_CONFIG = AssetConfig(
    name="BTC",
    hyena_coin="hyna:BTC",
    hyena_sz_decimals=5,
    hyena_tick_decimals=0,       # $1 tick
    grvt_instrument="BTC_USDT_Perp",
    grvt_tick_decimals=1,        # $0.1 tick
    grvt_min_size=0.001,
    grvt_size_decimals=3,
    grvt_min_notional=100.0,
    grvt_underlying_id=5,
    grvt_funding_hours=8,
    hyena_is_buy=True,           # Long HyENA + Short GRVT
    flat_threshold=0.001,
    quantity_precision=3,
    aggressive_limit_offset_bps=5.0,
    qty_mismatch_threshold=0.001,
)

HYPE_CONFIG = AssetConfig(
    name="HYPE",
    hyena_coin="hyna:HYPE",
    hyena_sz_decimals=2,
    hyena_tick_decimals=3,       # $0.001 tick
    grvt_instrument="HYPE_USDT_Perp",
    grvt_tick_decimals=3,        # $0.001 tick
    grvt_min_size=1.0,
    grvt_size_decimals=0,        # integer
    grvt_min_notional=5.0,
    grvt_underlying_id=41,
    grvt_funding_hours=4,        # 4h, NOT 8h
    hyena_is_buy=False,          # Short HyENA + Long GRVT (collects spread)
    flat_threshold=0.5,          # 0.5 HYPE (~$12)
    quantity_precision=0,        # integer qty (GRVT min_size=1)
    aggressive_limit_offset_bps=15.0,  # wider for thin HYPE book
    qty_mismatch_threshold=0.5,
)

SOL_CONFIG = AssetConfig(
    name="SOL",
    hyena_coin="hyna:SOL",
    hyena_sz_decimals=2,
    hyena_tick_decimals=2,       # $0.01 tick
    grvt_instrument="SOL_USDT_Perp",
    grvt_tick_decimals=2,        # $0.01 tick
    grvt_min_size=0.1,
    grvt_size_decimals=1,
    grvt_min_notional=5.0,
    grvt_underlying_id=6,
    grvt_funding_hours=8,
    hyena_is_buy=False,          # Short HyENA + Long GRVT (collects spread)
    flat_threshold=0.05,         # 0.05 SOL (~$4)
    quantity_precision=1,        # 1 decimal (GRVT min_size=0.1)
    aggressive_limit_offset_bps=10.0,
    qty_mismatch_threshold=0.05,
)

ASSET_CONFIGS = {"BTC": BTC_CONFIG, "HYPE": HYPE_CONFIG, "SOL": SOL_CONFIG}


# ============================================================================
# Exchange & Strategy Configuration
# ============================================================================

@dataclass(frozen=True)
class ExchangeConfig:
    # HyENA / Hyperliquid
    hyena_private_key: str
    hyena_builder_address: str = "0x1924b8561eeF20e70Ede628A296175D358BE80e5"
    hyena_builder_fee: int = 10  # tenths of bps; 10 = 1 bps (0.01%) per trade
    hyena_dex: str = "hyna"

    # GRVT
    grvt_api_key: str = ""
    grvt_private_key: str = ""
    grvt_trading_account_id: str = ""

    # API endpoints
    hl_api: str = "https://api.hyperliquid.xyz"
    hl_exchange_api: str = "https://api.hyperliquid.xyz/exchange"
    hl_info_api: str = "https://api.hyperliquid.xyz/info"
    grvt_market_api: str = "https://market-data.grvt.io/full/v1"
    grvt_auth_api: str = "https://edge.grvt.io"
    grvt_trade_api: str = "https://trades.grvt.io"


@dataclass(frozen=True)
class StrategyConfig:
    # Position sizing
    usd_per_leg: float = 100.0           # default USD per leg (overridable via input)

    # External reward estimates (annualized %)
    usde_reward_apr: float = 12.0   # USDe staking reward
    grvt_reward_apr: float = 11.0   # GRVT equity reward (5% base + 6% referral)

    # Rebalancing triggers (PRD Section 8.2)
    max_leverage: float = 3.0
    leverage_diff_trigger: float = 1.0
    margin_ratio_min: float = 0.50


@dataclass(frozen=True)
class MonitorConfig:
    # Polling intervals (seconds)
    position_poll_interval: int = 10
    funding_rate_interval: int = 300  # 5 min
    usde_peg_interval: int = 60  # 1 min
    book_price_interval: int = 60  # 1 min
    oi_status_interval: int = 900  # 15 min

    # Alert thresholds (PRD Section 10)
    usde_depeg_warning: float = 0.005  # 0.5%
    usde_depeg_exit: float = 0.01  # 1.0%
    circuit_breaker_pct: float = 0.15  # 15% in 1h
    funding_negative_threshold_annual: float = -20.0  # -20% annualized
    funding_negative_duration_hours: int = 6
    api_failure_threshold: int = 3  # consecutive failures

    # Mirror close (PRD Section 9)
    mirror_close_max_slippage: float = 0.005  # 0.5%
    mirror_close_timeout_seconds: int = 30
    mirror_close_max_retries: int = 3
    position_change_threshold: float = 0.001  # overridden by AssetConfig.flat_threshold
    mirror_close_retry_base: int = 30    # seconds, exponential backoff base
    mirror_close_retry_max: int = 300    # seconds, max backoff cap

    # Logging
    log_dir: str = "logs"
    data_dir: str = "data"


def load_config(asset_name: str = "BTC"):
    """Load and validate all configuration from environment.
    Returns (exchange_cfg, strategy_cfg, monitor_cfg, asset_cfg)."""
    hyena_key = os.environ.get("HYENA_PRIVATE_KEY", "")
    if not hyena_key:
        print("WARNING: HYENA_PRIVATE_KEY not set — trading disabled")

    asset_cfg = ASSET_CONFIGS.get(asset_name.upper())
    if not asset_cfg:
        raise ValueError(f"Unknown asset '{asset_name}'. Available: {list(ASSET_CONFIGS.keys())}")

    exchange = ExchangeConfig(
        hyena_private_key=hyena_key,
        grvt_api_key=os.environ.get("GRVT_API_KEY", ""),
        grvt_private_key=os.environ.get("GRVT_PRIVATE_KEY", ""),
        grvt_trading_account_id=os.environ.get("GRVT_TRADING_ACCOUNT_ID", ""),
    )
    mon_cfg = MonitorConfig(
        position_change_threshold=asset_cfg.flat_threshold,
    )
    # Default leverage: 2x for alts (volatile), 3x for BTC
    default_lev = 3.0 if asset_cfg.name == "BTC" else 2.0
    strategy = StrategyConfig(max_leverage=default_lev)
    return exchange, strategy, mon_cfg, asset_cfg
