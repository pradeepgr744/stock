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
from ..news.worker import news_worker
from .orb import OpeningRangeBreakout
from .vwap_sweep import VwapSweepReclaim

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
        self._signal_date = datetime.now(MARKET_TIMEZONE).date()
        self._daily_signal_count = 0
        self._sent_signal_keys: set[tuple[str, str]] = set()
        self._status: dict[str, Any] = {
            "running": False,
            "connection": "stopped",
            "message": "Scanner is stopped.",
            "symbols": [],
            "last_message_at": None,
            "last_alert_at": None,
            "last_error": None,
            "daily_signal_count": 0,
            "daily_signal_limit": 3,
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
                "universe": "NIFTY 200",
                "symbol_count": len(keys_to_symbols),
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
        vwap_sweep_evaluator = VwapSweepReclaim()
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
                        message="Connected; scanning live NIFTY 200 market data for ORB, VWAP and sweep/reclaim setups.",
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
                        await self._process_payload(payload, keys_to_symbols, evaluator, vwap_sweep_evaluator)
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
        vwap_sweep_evaluator: VwapSweepReclaim,
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
            candidates = [
                (candidate, "Opening Range Breakout (ORB), completed 5-minute confirmation", 70, None, None)
                for candidate in evaluator.on_feed(symbol, market_feed)
            ]
            candidates.extend(
                (candidate, candidate.strategy, candidate.quality_score, candidate.vwap, candidate.rvol)
                for candidate in vwap_sweep_evaluator.on_feed(symbol, market_feed)
            )
            for candidate, strategy, quality_score, vwap, rvol in candidates:
                news_status = news_worker.status
                blocked_symbols = set(news_status.get("blocked_symbols") or [])
                if news_status.get("halt_new_entries") or news_status.get("halt_all_symbols") or candidate.symbol in blocked_symbols:
                    self._update_status(
                        message=f"Suppressed {strategy} candidate for {candidate.symbol}: news safety gate is active."
                    )
                    continue
                if not self._reserve_daily_signal(candidate.symbol, candidate.direction):
                    continue
                hint = {
                    "symbol": candidate.symbol,
                    "strategy": strategy,
                    "direction": candidate.direction,
                    "entry": candidate.entry,
                    "stop_loss": candidate.stop_loss,
                    "target": candidate.target,
                    "quality_score": quality_score,
                    "vwap": vwap,
                    "rvol": rvol,
                    "reason": candidate.reason,
                    "live_scan": True,
                    "detected_at": candidate.timestamp.strftime("%Y-%m-%d %H:%M:%S %Z"),
                }
                try:
                    await asyncio.to_thread(telegram_notifier.send_hint, hint)
                except TelegramError as error:
                    logger.error("Telegram alert failed for %s: %s", candidate.symbol, error)
                    self._update_status(
                        last_error=f"Telegram alert failed for {candidate.symbol}: {error}"
                    )
                else:
                    self._update_status(
                        last_alert_at=datetime.now(MARKET_TIMEZONE).isoformat(),
                        last_error=None,
                        message=f"Telegram {strategy} candidate sent for {candidate.symbol}.",
                    )

    def _update_status(self, **updates: Any) -> None:
        with self._lock:
            self._status.update(updates)

    def _reserve_daily_signal(self, symbol: str, direction: str) -> bool:
        today = datetime.now(MARKET_TIMEZONE).date()
        with self._lock:
            if today != self._signal_date:
                self._signal_date = today
                self._daily_signal_count = 0
                self._sent_signal_keys.clear()
            key = (symbol, direction)
            if key in self._sent_signal_keys or self._daily_signal_count >= 3:
                return False
            self._sent_signal_keys.add(key)
            self._daily_signal_count += 1
            self._status["daily_signal_count"] = self._daily_signal_count
            return True


orb_scanner = OrbScanner()
