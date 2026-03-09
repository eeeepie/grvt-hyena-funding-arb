"""
WebSocket feed for HyENA (Hyperliquid) — position & book updates.

Subscribes to:
  - userEvents: real-time position changes (requires wallet address)
  - l2Book: order book updates for mid-price (used by circuit breaker)

Provides event-driven callbacks instead of REST polling, reducing
mirror-close detection from ~10-20s to sub-second.
"""

import asyncio
import json
import logging
import time
from typing import Callable, Optional

import websockets

logger = logging.getLogger("ws_feed")

# Hyperliquid WebSocket endpoint
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"

# Reconnect parameters
RECONNECT_DELAY_BASE = 1.0    # seconds
RECONNECT_DELAY_MAX = 30.0    # seconds
PING_INTERVAL = 50            # seconds (HL timeout is 60s)


class HyenaWsFeed:
    """WebSocket feed for HyENA position and book data.

    Usage:
        feed = HyenaWsFeed(
            wallet_address="0x...",
            coin="hyna:BTC",
            on_position_change=my_callback,  # called with (coin, size, entry_px)
            on_book_update=my_callback,       # called with (coin, best_bid, best_ask)
        )
        await feed.run()  # blocks, auto-reconnects
    """

    def __init__(
        self,
        wallet_address: str,
        coin: str,
        on_position_change: Optional[Callable] = None,
        on_book_update: Optional[Callable] = None,
    ):
        self.wallet = wallet_address
        self.coin = coin
        self.on_position_change = on_position_change
        self.on_book_update = on_book_update

        self._ws = None
        self._running = False
        self._reconnect_count = 0
        self._last_user_event = 0.0
        self._last_book_event = 0.0

    async def run(self):
        """Connect and listen forever. Auto-reconnects on failure."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                delay = min(
                    RECONNECT_DELAY_BASE * (2 ** self._reconnect_count),
                    RECONNECT_DELAY_MAX,
                )
                self._reconnect_count += 1
                logger.warning(f"WS disconnected: {e}. Reconnecting in {delay:.0f}s (attempt {self._reconnect_count})")
                await asyncio.sleep(delay)

    async def stop(self):
        """Gracefully stop the feed."""
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _connect_and_listen(self):
        """Single connection lifecycle: connect, subscribe, listen."""
        async with websockets.connect(
            HL_WS_URL,
            ping_interval=PING_INTERVAL,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._reconnect_count = 0
            logger.info(f"WS connected to {HL_WS_URL}")

            # Subscribe to channels
            await self._subscribe(ws)

            # Listen for messages
            async for raw_msg in ws:
                try:
                    msg = json.loads(raw_msg)
                    self._dispatch(msg)
                except json.JSONDecodeError:
                    logger.warning(f"WS non-JSON message: {raw_msg[:100]}")
                except Exception as e:
                    logger.error(f"WS message handling error: {e}")

    async def _subscribe(self, ws):
        """Send subscription messages."""
        # 1. User events (position changes, fills, funding, liquidations)
        if self.on_position_change and self.wallet:
            sub = {
                "method": "subscribe",
                "subscription": {
                    "type": "userEvents",
                    "user": self.wallet,
                },
            }
            await ws.send(json.dumps(sub))
            logger.info(f"WS subscribed: userEvents for {self.wallet[:10]}...")

        # 2. L2 book updates
        if self.on_book_update:
            sub = {
                "method": "subscribe",
                "subscription": {
                    "type": "l2Book",
                    "coin": self.coin,
                    "nSigFigs": 5,
                },
            }
            await ws.send(json.dumps(sub))
            logger.info(f"WS subscribed: l2Book for {self.coin}")

    def _dispatch(self, msg: dict):
        """Route incoming WS message to the right handler."""
        channel = msg.get("channel")

        if channel == "subscriptionResponse":
            method = msg.get("data", {}).get("method")
            logger.info(f"WS subscription confirmed: {method}")
            return

        if channel == "userEvents":
            self._handle_user_events(msg.get("data", {}))
        elif channel == "l2Book":
            self._handle_book(msg.get("data", {}))
        elif channel == "pong":
            pass  # heartbeat response
        else:
            # Unknown channel — log once
            if channel:
                logger.debug(f"WS unknown channel: {channel}")

    def _handle_user_events(self, data: dict):
        """Process userEvents — extract position changes for our coin."""
        if not self.on_position_change:
            return

        # userEvents data structure:
        # {"fills": [...], "funding": {...}, "liquidation": {...}, "nonUserCancel": [...]}
        # We care about fills (which change position) and liquidations.
        #
        # But the most reliable approach: each userEvent triggers us to
        # check the embedded position snapshot if available, or we signal
        # that a position-affecting event occurred.

        now = time.time()

        # Check for fills on our coin
        fills = data.get("fills", [])
        for fill in fills:
            if fill.get("coin") == self.coin or fill.get("coin") == self.coin.split(":")[-1]:
                self._last_user_event = now
                # Extract position info if available in fill
                # Hyperliquid fills include: coin, px, sz, side, time, closedPnl, dir
                logger.info(
                    f"WS fill: {fill.get('side')} {fill.get('sz')} {self.coin} "
                    f"@ ${fill.get('px')} (dir={fill.get('dir')})"
                )
                # Signal position change — caller should re-check position
                self.on_position_change(self.coin, fill)
                return

        # Check for liquidation
        liq = data.get("liquidation")
        if liq and liq.get("coin") == self.coin:
            self._last_user_event = now
            logger.warning(f"WS LIQUIDATION on {self.coin}: {liq}")
            self.on_position_change(self.coin, {"liquidation": True})
            return

        # Check for funding (informational, no position change)
        funding = data.get("funding")
        if funding:
            coin = funding.get("coin", "")
            if coin == self.coin or coin == self.coin.split(":")[-1]:
                logger.debug(f"WS funding: {funding}")

    def _handle_book(self, data: dict):
        """Process l2Book — extract best bid/ask."""
        if not self.on_book_update:
            return

        levels = data.get("levels", [])
        if len(levels) < 2:
            return
        bids, asks = levels[0], levels[1]
        if not bids or not asks:
            return

        best_bid = float(bids[0].get("px", 0))
        best_ask = float(asks[0].get("px", 0))
        if best_bid > 0 and best_ask > 0:
            self._last_book_event = time.time()
            self.on_book_update(self.coin, best_bid, best_ask)
