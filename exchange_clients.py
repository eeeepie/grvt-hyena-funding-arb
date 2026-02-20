"""
Exchange API wrappers for HyENA (Hyperliquid HIP-3) and GRVT.
"""

import json
import logging
import random
import time
import requests
from datetime import datetime, timezone, timedelta
from config import ExchangeConfig

logger = logging.getLogger("exchange")

ANNUAL_MULTIPLIER = 3 * 365 * 100  # 8h rate → annual %


def safe_float(val, default=0.0):
    """Convert anything to float safely."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def book_from_levels(best_bid: float, best_ask: float) -> dict:
    """Build a standard book dict from bid/ask prices."""
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": (best_ask - best_bid) / mid * 10000 if mid else 0,
    }


# ============================================================================
# HyENA Client
# ============================================================================

class HyenaClient:
    """HyENA (hyna:BTC on Hyperliquid HIP-3). Market data is public; trading needs SDK."""

    def __init__(self, config: ExchangeConfig):
        self.coin = config.hyena_coin        # "hyna:BTC"
        self.dex = config.hyena_dex          # "hyna"
        self.info_url = config.hl_info_api
        self.builder = {"b": config.hyena_builder_address, "f": config.hyena_builder_fee}

        self._exchange = None
        self._account = None
        self._sz_decimals = None

        if config.hyena_private_key:
            self._init_sdk(config)

    def _init_sdk(self, config):
        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info

            self._account = Account.from_key(config.hyena_private_key)
            self._exchange = Exchange(self._account, base_url=config.hl_api, perp_dexs=["hyna"])
            logger.info(f"HyENA SDK ready. Wallet: {self._account.address}")
        except ImportError:
            logger.warning("hyperliquid-python-sdk not installed. pip3 install hyperliquid-python-sdk eth-account")
        except Exception as e:
            logger.error(f"HyENA SDK init failed: {e}")

    # --- Raw API ---

    def _post(self, payload: dict) -> dict:
        resp = requests.post(self.info_url, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # --- Market Data ---

    def get_book(self) -> dict:
        raw = self._post({"type": "l2Book", "coin": self.coin, "nSigFigs": 5})
        return book_from_levels(
            float(raw["levels"][0][0]["px"]),
            float(raw["levels"][1][0]["px"]),
        )

    def get_mid_price(self) -> float:
        return self.get_book()["mid"]

    def get_funding_rate(self) -> dict:
        # Settled rate (fundingHistory)
        start = int((datetime.now(timezone.utc) - timedelta(hours=12)).timestamp() * 1000)
        hist = self._post({"type": "fundingHistory", "coin": self.coin, "startTime": start})
        if not hist:
            r1h = r8h = 0.0
            ts = None
        else:
            r1h = safe_float(hist[-1]["fundingRate"])
            r8h = r1h * 8
            ts = datetime.fromtimestamp(hist[-1]["time"] / 1000, tz=timezone.utc)

        # Predicted rate (assetCtx)
        pred_1h = pred_8h = pred_ann = 0.0
        try:
            meta = self._post({"type": "metaAndAssetCtxs", "dex": self.dex})
            coin_full = f"{self.dex}:{self.coin.split(':')[-1]}"  # "hyna:BTC"
            for i, u in enumerate(meta[0]["universe"]):
                if u["name"] == coin_full or u["name"] == self.coin.split(":")[-1]:
                    pred_1h = safe_float(meta[1][i].get("funding", 0))
                    pred_8h = pred_1h * 8
                    pred_ann = pred_8h * ANNUAL_MULTIPLIER
                    break
        except Exception:
            pass

        return {
            "funding_1h": r1h, "funding_8h": r8h,
            "annual_pct": r8h * ANNUAL_MULTIPLIER,
            "predicted_1h": pred_1h, "predicted_8h": pred_8h, "predicted_ann": pred_ann,
            "timestamp": ts,
        }

    def get_sz_decimals(self) -> int:
        if self._sz_decimals is None:
            meta = self._post({"type": "meta", "dex": self.dex})
            for a in meta.get("universe", []):
                if a["name"] == "BTC":
                    self._sz_decimals = a.get("szDecimals", 5)
                    break
            else:
                self._sz_decimals = 5
        return self._sz_decimals

    # --- Account (needs SDK / private key) ---

    def _get_clearinghouse(self) -> dict:
        """Single clearinghouse call — used by both get_position and get_balance."""
        if not self._account:
            return {}
        return self._post({
            "type": "clearinghouseState",
            "user": self._account.address,
            "dex": self.dex,
        })

    def get_position(self) -> dict:
        if not self._account:
            return {"size": 0.0, "error": "SDK not initialized"}
        try:
            state = self._get_clearinghouse()
            for pos in state.get("assetPositions", []):
                p = pos.get("position", {})
                if p.get("coin", "") in (self.coin, "BTC"):
                    return {
                        "size": safe_float(p.get("szi")),
                        "entry_px": safe_float(p.get("entryPx")),
                        "unrealized_pnl": safe_float(p.get("unrealizedPnl")),
                        "leverage_value": safe_float(p.get("leverage", {}).get("value")),
                        "margin_used": safe_float(p.get("marginUsed")),
                        "liquidation_px": safe_float(p.get("liquidationPx")),
                    }
            return {"size": 0.0}
        except Exception as e:
            logger.error(f"HyENA position query failed: {e}")
            return {"size": 0.0, "error": str(e)}

    def _get_spot_balance(self) -> float:
        """Get USDE balance in spot wallet (counts as HyENA perps margin)."""
        if not self._account:
            return 0.0
        try:
            state = self._post({
                "type": "spotClearinghouseState",
                "user": self._account.address,
            })
            for b in state.get("balances", []):
                if b.get("coin") == "USDE":
                    return safe_float(b.get("total"))
            return 0.0
        except Exception:
            return 0.0

    def get_balance(self) -> dict:
        """Combined balance: perps margin + spot USDE (both usable for HyENA trading)."""
        if not self._account:
            return {"balance": 0.0, "error": "SDK not initialized"}
        try:
            margin = self._get_clearinghouse().get("marginSummary", {})
            perps_value = safe_float(margin.get("accountValue"))
            spot_usde = self._get_spot_balance()
            return {
                "account_value": perps_value + spot_usde,
                "perps_margin": perps_value,
                "spot_usde": spot_usde,
                "total_margin": safe_float(margin.get("totalMarginUsed")),
                "withdrawable": safe_float(margin.get("withdrawable")),
            }
        except Exception as e:
            return {"balance": 0.0, "error": str(e)}

    # --- Trading ---

    def _require_sdk(self):
        if not self._exchange:
            raise RuntimeError("HyENA SDK not initialized — set HYENA_PRIVATE_KEY in .env")

    def place_order(self, is_buy: bool, size: float, price: float, reduce_only=False) -> dict:
        self._require_sdk()
        size = round(size, self.get_sz_decimals())
        price = round(price, 0)  # HyENA BTC tick size = $1
        side = "BUY" if is_buy else "SELL"
        logger.info(f"HyENA {side} {size} BTC @ ${price:,.0f} (IOC, reduce={reduce_only})")

        result = self._exchange.order(
            self.coin, is_buy, size, price,
            {"limit": {"tif": "Ioc"}},
            reduce_only=reduce_only,
            builder=self.builder,
        )
        logger.info(f"HyENA result: {result}")
        # Check for API-level errors (SDK returns {'status':'ok'} even on order rejection)
        if result and result.get("status") == "ok":
            statuses = result.get("response", {}).get("data", {}).get("statuses", [])
            for s in statuses:
                if "error" in s:
                    raise RuntimeError(f"HyENA order rejected: {s['error']}")
        return result

    # --- Fill History ---

    def get_fills(self, start_ms: int) -> list:
        """Get fills since start_ms for hyna:BTC. Returns [{px, sz, fee, side, time, closedPnl, dir}]."""
        try:
            raw = self._post({
                "type": "userFillsByTime",
                "user": self._account.address,
                "startTime": start_ms,
            })
            fills = []
            for f in raw:
                if f.get("coin") == self.coin or f.get("coin") == "BTC":
                    fills.append({
                        "px": safe_float(f.get("px")),
                        "sz": safe_float(f.get("sz")),
                        "fee": safe_float(f.get("fee")),
                        "fee_token": f.get("feeToken", ""),
                        "side": f.get("side", ""),
                        "time": f.get("time", 0),
                        "closed_pnl": safe_float(f.get("closedPnl")),
                        "dir": f.get("dir", ""),
                    })
            return fills
        except Exception as e:
            logger.error(f"HyENA get_fills failed: {e}")
            return []

    def get_accumulated_funding(self, start_ms: int, size: float) -> float:
        """Get accumulated funding payment from start_ms to now.
        Returns total funding (positive = received, negative = paid).
        For a long position, funding = -rate * size * mark_price per period."""
        try:
            hist = self._post({"type": "fundingHistory", "coin": self.coin, "startTime": start_ms})
            total = 0.0
            for h in hist:
                rate = safe_float(h.get("fundingRate"))
                # For longs: pay when rate > 0, receive when rate < 0
                # funding_payment = -rate * abs(size) * mark_price (approximated by mid)
                # But fundingHistory doesn't include mark_price, so use a simpler approach:
                # The actual funding is already settled to the account, we just sum the rates
                # and multiply by position notional
                total += rate
            # total is sum of 1h rates; multiply by position value
            mid = self.get_mid_price()
            # For long: pay positive rates → negative PnL
            funding_pnl = -total * abs(size) * mid
            return funding_pnl
        except Exception as e:
            logger.error(f"HyENA get_accumulated_funding failed: {e}")
            return 0.0

    def market_close(self, size: float) -> dict:
        """Close position with aggressive IOC. size>0 = close long, size<0 = close short."""
        self._require_sdk()
        book = self.get_book()
        if size > 0:
            return self.place_order(False, abs(size), round(book["best_bid"] * 0.995, 0), reduce_only=True)
        else:
            return self.place_order(True, abs(size), round(book["best_ask"] * 1.005, 0), reduce_only=True)


# ============================================================================
# GRVT Client — requires EIP-712 signed orders for trading
# ============================================================================

# GRVT EIP-712 constants
_GRVT_CHAIN_ID = 325  # mainnet
_PRICE_MUL = 1_000_000_000
_SIZE_MUL = 1_000_000_000

_ORDER_TYPES = {
    "Order": [
        {"name": "subAccountID", "type": "uint64"},
        {"name": "isMarket", "type": "bool"},
        {"name": "timeInForce", "type": "uint8"},
        {"name": "postOnly", "type": "bool"},
        {"name": "reduceOnly", "type": "bool"},
        {"name": "legs", "type": "OrderLeg[]"},
        {"name": "nonce", "type": "uint32"},
        {"name": "expiration", "type": "int64"},
    ],
    "OrderLeg": [
        {"name": "assetID", "type": "uint256"},
        {"name": "contractSize", "type": "uint64"},
        {"name": "limitPrice", "type": "uint64"},
        {"name": "isBuyingContract", "type": "bool"},
    ],
}


def _encode_perp_asset(underlying: int, quote: int) -> str:
    """Encode perpetual asset ID. BTC=5, USDT=3."""
    msg = bytearray(3)
    msg[2] = 1  # PERPETUAL
    msg[1] = underlying
    msg[0] = quote
    return f"0x{msg.hex()}"


def _btc_usdt_perp_asset() -> str:
    return _encode_perp_asset(5, 3)  # BTC=5, USDT=3


class GrvtClient:
    """GRVT (BTC_USDT_Perp). Market data is public; trading needs API key + EIP-712 signing."""

    def __init__(self, config: ExchangeConfig):
        self.instrument = config.grvt_instrument
        self.market_url = config.grvt_market_api
        self.auth_url = config.grvt_auth_api
        self.trade_url = config.grvt_trade_api

        self._session = requests.Session()
        self._session.headers["Content-Type"] = "application/json"
        self._sub_account_id = config.grvt_trading_account_id
        self._api_key = config.grvt_api_key
        self._private_key = config.grvt_private_key
        self._signer = None
        self._authenticated = False
        self._cookies = {}

        # Init signer for EIP-712
        if self._private_key:
            try:
                from eth_account import Account
                self._signer = Account.from_key(self._private_key)
                logger.info(f"GRVT signer ready: {self._signer.address}")
            except Exception as e:
                logger.error(f"GRVT signer init failed: {e}")

        if self._api_key:
            self._login()

    def _login(self):
        """Login at edge.grvt.io, use cookies for trades.grvt.io."""
        try:
            login_session = requests.Session()
            resp = login_session.post(
                f"{self.auth_url}/auth/api_key/login",
                json={"api_key": self._api_key},
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code == 200:
                self._cookies = login_session.cookies.get_dict()
                self._session.headers["Content-Type"] = "application/json"
                self._session.headers["X-Grvt-Account-Id"] = self._sub_account_id
                self._authenticated = True
                logger.info(f"GRVT authenticated. Sub-account: {self._sub_account_id}")
            else:
                logger.error(f"GRVT login failed: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"GRVT login error: {e}")

    def _trade_post(self, path: str, payload: dict) -> dict:
        """POST to trades.grvt.io. Auto-injects sub_account_id, retries on 401."""
        payload.setdefault("sub_account_id", self._sub_account_id)
        resp = self._session.post(
            f"{self.trade_url}{path}", json=payload,
            cookies=self._cookies, timeout=15,
        )
        if resp.status_code in (401, 403):
            logger.warning(f"GRVT {resp.status_code}, re-logging in...")
            self._authenticated = False
            self._login()
            resp = self._session.post(
                f"{self.trade_url}{path}", json=payload,
                cookies=self._cookies, timeout=15,
            )
        resp.raise_for_status()
        return resp.json()

    def _trade_post_raw(self, path: str, json_str: str) -> dict:
        """POST raw JSON string (for signed orders). Retries on 401."""
        resp = self._session.post(
            f"{self.trade_url}{path}", data=json_str,
            cookies=self._cookies, timeout=15,
        )
        if resp.status_code in (401, 403):
            logger.warning(f"GRVT {resp.status_code}: {resp.text[:200]}, re-logging in...")
            self._authenticated = False
            self._login()
            resp = self._session.post(
                f"{self.trade_url}{path}", data=json_str,
                cookies=self._cookies, timeout=15,
            )
        resp.raise_for_status()
        return resp.json()

    def _require_auth(self):
        if not self._authenticated:
            self._login()
        if not self._authenticated:
            raise RuntimeError("GRVT not authenticated — set GRVT_API_KEY in .env")

    # --- Market Data (public) ---

    def get_ticker(self) -> dict:
        resp = requests.post(
            f"{self.market_url}/ticker",
            json={"instrument": self.instrument},
            headers={"Content-Type": "application/json"}, timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("result", {})

    def get_mid_price(self) -> float:
        t = self.get_ticker()
        bid, ask = safe_float(t.get("best_bid_price")), safe_float(t.get("best_ask_price"))
        return (bid + ask) / 2 if bid and ask else safe_float(t.get("mark_price"))

    def get_book(self) -> dict:
        resp = requests.post(
            f"{self.market_url}/book",
            json={"instrument": self.instrument, "depth": 10},
            headers={"Content-Type": "application/json"}, timeout=15,
        )
        resp.raise_for_status()
        r = resp.json().get("result", {})
        bids, asks = r.get("bids", []), r.get("asks", [])
        return book_from_levels(
            safe_float(bids[0].get("price")) if bids else 0,
            safe_float(asks[0].get("price")) if asks else 0,
        )

    def get_funding_rate(self) -> dict:
        t = self.get_ticker()
        pct = safe_float(t.get("funding_rate_8h_curr", 0))
        dec = pct / 100  # 0.01% → 0.0001
        return {
            "funding_8h_pct": pct, "funding_8h_decimal": dec,
            "annual_pct": dec * ANNUAL_MULTIPLIER,
            "mark_price": safe_float(t.get("mark_price")),
        }

    # --- Account ---

    def get_position(self) -> dict:
        if not self._authenticated:
            return {"size": 0.0, "error": "Not authenticated"}
        try:
            data = self._trade_post("/full/v1/positions", {})
            positions = data.get("result", data) if isinstance(data, dict) else data
            if isinstance(positions, list):
                for p in positions:
                    if p.get("instrument") == self.instrument:
                        return {
                            "size": safe_float(p.get("size")),
                            "entry_px": safe_float(p.get("entry_price")),
                            "unrealized_pnl": safe_float(p.get("unrealized_pnl")),
                            "margin_used": safe_float(p.get("margin")),
                            "liquidation_px": safe_float(p.get("liquidation_price")),
                            "leverage_value": safe_float(p.get("leverage")),
                        }
            return {"size": 0.0}
        except Exception as e:
            logger.error(f"GRVT position query failed: {e}")
            return {"size": 0.0, "error": str(e)}

    def get_balance(self) -> dict:
        if not self._authenticated:
            return {"balance": 0.0, "error": "Not authenticated"}
        try:
            data = self._trade_post("/full/v1/account_summary", {}).get("result", {})
            return {
                "total_equity": safe_float(data.get("total_equity")),
                "available_balance": safe_float(data.get("available_balance")),
                "initial_margin": safe_float(data.get("initial_margin")),
            }
        except Exception as e:
            return {"balance": 0.0, "error": str(e)}

    # --- Leverage ---

    def get_leverage(self) -> float:
        """Get current initial leverage for BTC_USDT_Perp. Returns 0 on failure."""
        self._require_auth()
        try:
            data = self._trade_post("/full/v1/get_all_initial_leverage", {})
            # Response uses "results" (plural), not "result"
            results = data.get("results", data.get("result", [])) if isinstance(data, dict) else []
            for item in results:
                if item.get("instrument") == self.instrument:
                    return safe_float(item.get("leverage"))
            return 0.0
        except Exception as e:
            logger.error(f"GRVT get_leverage failed: {e}")
            return 0.0

    # set_initial_leverage API deprecated (2026-02) — must use GRVT frontend

    # --- Fill History ---

    def get_fills(self, limit: int = 20) -> list:
        """Get recent fills. Returns [{px, sz, fee, fee_rate, realized_pnl, time, is_buyer}]."""
        self._require_auth()
        try:
            data = self._trade_post("/full/v1/fill_history", {"limit": limit})
            results = data.get("result", data) if isinstance(data, dict) else data
            fills = []
            if isinstance(results, list):
                for f in results:
                    if f.get("instrument") == self.instrument:
                        fills.append({
                            "px": safe_float(f.get("price", f.get("fill_price"))),
                            "sz": safe_float(f.get("size", f.get("fill_size"))),
                            "fee": safe_float(f.get("fee")),
                            "fee_rate": safe_float(f.get("fee_rate")),
                            "realized_pnl": safe_float(f.get("realized_pnl")),
                            "time": f.get("event_time", f.get("time", 0)),
                            "is_buyer": f.get("is_buyer", f.get("is_buying_asset", False)),
                        })
            return fills
        except Exception as e:
            logger.error(f"GRVT get_fills failed: {e}")
            return []

    def get_funding_payments(self, start_time_ns: int = 0, limit: int = 100) -> float:
        """Get accumulated funding payments. Returns total amount (positive = received)."""
        self._require_auth()
        try:
            payload = {
                "sub_account_id": self._sub_account_id,
                "instrument": self.instrument,
                "limit": limit,
            }
            if start_time_ns > 0:
                payload["start_time"] = str(start_time_ns)
            data = self._trade_post("/full/v1/funding_payment_history", payload)
            results = data.get("result", []) if isinstance(data, dict) else []
            total = sum(safe_float(r.get("amount", 0)) for r in results)
            logger.info(f"GRVT funding payments: {len(results)} records, total={total:.4f}")
            return total
        except Exception as e:
            logger.error(f"GRVT get_funding_payments failed: {e}")
            return 0.0

    # --- EIP-712 Order Signing ---

    def _sign_order(self, is_buy: bool, size: float, price: float,
                    reduce_only: bool = False, tif: str = "IOC") -> str:
        """Build and sign a GRVT order via EIP-712, return JSON payload string."""
        from decimal import Decimal
        from eth_account import Account
        from eth_account.messages import encode_typed_data

        if not self._signer:
            raise RuntimeError("GRVT signer not initialized — set GRVT_PRIVATE_KEY in .env")

        # TIF mapping (from official SDK: pysdk.grvt_raw_signing)
        tif_map = {"GTT": 1, "AON": 2, "IOC": 3, "FOK": 4}
        tif_val = tif_map.get(tif, 3)
        tif_name = {"GTT": "GOOD_TILL_TIME", "IOC": "IMMEDIATE_OR_CANCEL",
                     "FOK": "FILL_OR_KILL"}.get(tif, "IMMEDIATE_OR_CANCEL")

        nonce = random.randint(0, 2**32 - 1)
        expiry_ns = time.time_ns() + int(3 * 3600 * 1e9)  # 3 hours

        # Use Decimal for precision (avoids float rounding issues)
        size_int = int(Decimal(str(size)) * Decimal(_SIZE_MUL))
        price_int = int(Decimal(str(price)) * Decimal(_PRICE_MUL))

        # Build EIP-712 message
        message_data = {
            "subAccountID": int(self._sub_account_id),
            "isMarket": False,
            "timeInForce": tif_val,
            "postOnly": False,
            "reduceOnly": reduce_only,
            "legs": [{
                "assetID": _btc_usdt_perp_asset(),
                "contractSize": size_int,
                "limitPrice": price_int,
                "isBuyingContract": is_buy,
            }],
            "nonce": nonce,
            "expiration": expiry_ns,
        }

        domain = {"name": "GRVT Exchange", "version": "0", "chainId": _GRVT_CHAIN_ID}
        signable = encode_typed_data(domain, _ORDER_TYPES, message_data)
        signed = Account.sign_message(signable, self._private_key)

        r_hex = "0x" + signed.r.to_bytes(32, byteorder="big").hex()
        s_hex = "0x" + signed.s.to_bytes(32, byteorder="big").hex()

        payload = {
            "order": {
                "sub_account_id": str(self._sub_account_id),
                "is_market": False,
                "time_in_force": tif_name,
                "post_only": False,
                "reduce_only": reduce_only,
                "legs": [{
                    "instrument": self.instrument,
                    "size": str(size),
                    "limit_price": str(price),
                    "is_buying_asset": is_buy,
                }],
                "signature": {
                    "r": r_hex,
                    "s": s_hex,
                    "v": signed.v,
                    "expiration": str(expiry_ns),
                    "nonce": nonce,
                    "signer": self._signer.address.lower(),
                },
                "metadata": {
                    "client_order_id": str(random.randint(0, 2**32 - 1)),
                },
            }
        }
        return json.dumps(payload)

    # --- Trading ---

    def place_order(self, is_buy: bool, size: float, price: float, reduce_only=False) -> dict:
        self._require_auth()
        size = round(size, 3)
        price = round(price, 1)
        side = "BUY" if is_buy else "SELL"
        logger.info(f"GRVT {side} {size} BTC @ ${price:,.1f} (IOC, reduce={reduce_only})")

        signed_json = self._sign_order(is_buy, size, price, reduce_only=reduce_only)
        result = self._trade_post_raw("/full/v1/create_order", signed_json)
        logger.info(f"GRVT result: {result}")
        return result

    def market_close(self, size: float, is_long: bool, max_retries: int = 3) -> dict:
        """Close position with aggressive IOC. Retries with fresh signature on failure."""
        last_err = None
        for attempt in range(max_retries):
            try:
                mid = self.get_mid_price()
                # 1% offset on retry (0.5% first attempt) for more aggressive fill
                offset = 0.005 if attempt == 0 else 0.01
                if is_long:
                    px = round(mid * (1 - offset), 1)
                    result = self.place_order(False, abs(size), px, reduce_only=True)
                else:
                    px = round(mid * (1 + offset), 1)
                    result = self.place_order(True, abs(size), px, reduce_only=True)
                return result
            except Exception as e:
                last_err = e
                logger.warning(f"GRVT market_close attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
        raise RuntimeError(f"GRVT market_close failed after {max_retries} attempts: {last_err}")
