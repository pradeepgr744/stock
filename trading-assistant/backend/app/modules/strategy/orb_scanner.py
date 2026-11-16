from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import websockets
from google.protobuf.json_format import MessageToDict
from websockets.exceptions import InvalidStatus
from upstox_client.feeder.proto import MarketDataFeedV3_pb2

from ...config import settings
from ..market_data.upstox import Instrument, upstox_client
from ..notifications.telegram import TelegramError, telegram_notifier
from .orb import OpeningRangeBreakout

logger = logging.getLogger(__name__)
MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")


class OrbScannerError(Exception):
    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class OrbScanner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status: dict[str, Any] = {
            "running": False,
            "connection": "stopped",
            "message": "Scanner is stopped.",
            "symbols": [],
            "last_message_at": None,
            "last_alert_at": None,
            "last_error": None,
        }

    @property
    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def start(self, instruments: list[Instrument]) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return dict(self._status)
            if not upstox_client.configured:
                raise OrbScannerError("Upstox access token is not configured.")
            if not telegram_notifier.configured:
                raise OrbScannerError(
                    "Telegram is not configured; scanner alerts cannot be delivered."
                )
            if not instruments:
                raise OrbScannerError("No instruments were resolved for the scanner.", 409)

            self._stop_event.clear()
            keys_to_symbols = {
                instrument.instrument_key: instrument.trading_symbol
                for instrument in instruments
            }
            self._status = {
                "running": True,
                "connection": "connecting",
                "message": "Connecting to Upstox live market feed.",
                "symbols": list(keys_to_symbols.values()),
                "last_message_at": None,
                "last_alert_at": None,
                "last_error": None,
            }
            self._thread = threading.Thread(
                target=self._thread_main,
                args=(keys_to_symbols,),
                name="orb-market-scanner",
                daemon=True,
            )
            self._thread.start()
            return dict(self._status)

    def stop(self) -> dict[str, Any]:
        self._stop_event.set()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._status["message"] = "Stopping live market feed."
                return dict(self._status)
            self._status.update(
                running=False,
                connection="stopped",
                message="Scanner is stopped.",
            )
            return dict(self._status)

    def _thread_main(self, keys_to_symbols: dict[str, str]) -> None:
        try:
            asyncio.run(self._run(keys_to_symbols))
        except Exception as error:
            logger.exception("ORB scanner stopped unexpectedly")
            self._update_status(
                running=False,
                connection="error",
                message="Scanner stopped unexpectedly.",
                last_error=str(error),
            )
        finally:
            with self._lock:
                self._status["running"] = False
                if self._status["connection"] != "error":
                    self._status.update(
                        connection="stopped",
                        message="Scanner is stopped.",
                    )

    async def _run(self, keys_to_symbols: dict[str, str]) -> None:
        evaluator = OpeningRangeBreakout()
        subscription = {
            "guid": str(uuid4()),
            "method": "sub",
            "data": {
                "mode": "full",
                "instrumentKeys": list(keys_to_symbols),
            },
        }
        delay = 1

        while not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    "wss://api.upstox.com/v3/feed/market-data-feed",
                    additional_headers={
                        "Authorization": f"Bearer {settings.upstox_access_token.strip()}",
                        "Accept": "*/*",
                    },
                    open_timeout=settings.upstox_request_timeout_seconds,
                    ping_interval=20,
                    ping_timeout=20,
                ) as websocket:
                    await websocket.send(json.dumps(subscription).encode("utf-8"))
                    self._update_status(
                        connection="connected",
                        message="Connected; scanning live NIFTY 50 market data.",
                        last_error=None,
                    )
                    delay = 1

                    while not self._stop_event.is_set():
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=1)
                        except TimeoutError:
                            continue
                        if not isinstance(message, bytes):
                            raise OrbScannerError("Upstox sent a non-binary market feed message.")
                        decoded = MarketDataFeedV3_pb2.FeedResponse.FromString(message)
                        payload = MessageToDict(decoded)
                        await self._process_payload(payload, keys_to_symbols, evaluator)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if self._stop_event.is_set():
                    break
                if isinstance(error, InvalidStatus) and error.response.status_code in {401, 403}:
                    self._update_status(
                        connection="error",
                        message="Upstox rejected the stream credentials or access.",
                        last_error="Refresh the Upstox token or check live-feed permissions.",
                    )
                    break
                logger.warning("ORB live feed disconnected: %s", error)
                self._update_status(
                    connection="reconnecting",
                    message=f"Live feed interrupted; reconnecting in {delay} seconds.",
                    last_error=str(error),
                )
                await asyncio.to_thread(self._stop_event.wait, delay)
                delay = min(delay * 2, 30)

    async def _process_payload(
        self,
        payload: dict[str, Any],
        keys_to_symbols: dict[str, str],
        evaluator: OpeningRangeBreakout,
    ) -> None:
        self._update_status(last_message_at=datetime.now(MARKET_TIMEZONE).isoformat())
        feeds = payload.get("feeds", {})
        if not isinstance(feeds, dict):
            return

        for instrument_key, feed in feeds.items():
            if not isinstance(feed, dict):
                continue
            symbol = keys_to_symbols.get(instrument_key)
            full_feed = feed.get("fullFeed")
            market_feed = full_feed.get("marketFF") if isinstance(full_feed, dict) else None
            if symbol is None or not isinstance(market_feed, dict):
                continue
            for candidate in evaluator.on_feed(symbol, market_feed):
                try:
                    await asyncio.to_thread(
                        telegram_notifier.send_hint,
                        {
                            "symbol": candidate.symbol,
                            "strategy": "Opening Range Breakout (ORB), 5-minute confirmation",
                            "direction": candidate.direction,
                            "entry": candidate.entry,
                            "stop_loss": candidate.stop_loss,
                            "target": candidate.target,
                            "reason": candidate.reason,
                            "live_scan": True,
                            "detected_at": candidate.timestamp.strftime("%Y-%m-%d %H:%M:%S %Z"),
                        },
                    )
                except TelegramError as error:
                    logger.error("Telegram alert failed for %s: %s", candidate.symbol, error)
                    self._update_status(
                        last_error=f"Telegram alert failed for {candidate.symbol}: {error}"
                    )
                else:
                    self._update_status(
                        last_alert_at=datetime.now(MARKET_TIMEZONE).isoformat(),
                        last_error=None,
                        message=f"Telegram ORB candidate sent for {candidate.symbol}.",
                    )

    def _update_status(self, **updates: Any) -> None:
        with self._lock:
            self._status.update(updates)


orb_scanner = OrbScanner()
