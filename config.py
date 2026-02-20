"""
Configuration for BTC Funding Rate Arbitrage System.
Loads credentials from environment variables (.env file).
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ExchangeConfig:
    # HyENA / Hyperliquid
    hyena_private_key: str
    hyena_builder_address: str = "0x1924b8561eeF20e70Ede628A296175D358BE80e5"
    hyena_builder_fee: int = 10  # tenths of bps; 10 = 1 bps (0.01%) per trade
    hyena_coin: str = "hyna:BTC"
    hyena_dex: str = "hyna"

    # GRVT
    grvt_api_key: str = ""
    grvt_private_key: str = ""
    grvt_trading_account_id: str = ""
    grvt_instrument: str = "BTC_USDT_Perp"

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
    quantity_precision: int = 3           # GRVT: 0.001 step; HyENA: 0.00001 step

    # Order execution
    aggressive_limit_offset_bps: float = 5.0  # mid +/- 5bps (hyna:BTC book can be thin)
    max_fill_wait_seconds: float = 5.0
    qty_mismatch_threshold: float = 0.001  # BTC

    # External reward estimates (annualized %)
    usde_reward_apr: float = 12.0   # USDe staking reward
    grvt_reward_apr: float = 10.0   # GRVT equity reward

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
    position_change_threshold: float = 0.001  # BTC
    mirror_close_retry_base: int = 30    # seconds, exponential backoff base
    mirror_close_retry_max: int = 300    # seconds, max backoff cap

    # Logging
    log_dir: str = "logs"
    data_dir: str = "data"


def load_config():
    """Load and validate all configuration from environment."""
    hyena_key = os.environ.get("HYENA_PRIVATE_KEY", "")
    if not hyena_key:
        print("WARNING: HYENA_PRIVATE_KEY not set — trading disabled")

    exchange = ExchangeConfig(
        hyena_private_key=hyena_key,
        grvt_api_key=os.environ.get("GRVT_API_KEY", ""),
        grvt_private_key=os.environ.get("GRVT_PRIVATE_KEY", ""),
        grvt_trading_account_id=os.environ.get("GRVT_TRADING_ACCOUNT_ID", ""),
    )
    return exchange, StrategyConfig(), MonitorConfig()
