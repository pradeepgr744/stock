from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from ...config import settings


class UpstoxError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Instrument:
    instrument_key: str
    trading_symbol: str
    name: str
    tick_size: float | None
    lot_size: int | None


class UpstoxClient:
    def __init__(self) -> None:
        self._instrument_cache: tuple[date, list[Instrument]] | None = None

    @property
    def configured(self) -> bool:
        return bool(settings.upstox_access_token.strip())

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.configured:
            raise UpstoxError(
                "Upstox access token is not configured. Set UPSTOX_ACCESS_TOKEN in backend/.env.",
                status_code=503,
            )

        url = f"{settings.upstox_api_base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            response = httpx.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {settings.upstox_access_token.strip()}",
                    "Accept": "application/json",
                },
                timeout=settings.upstox_request_timeout_seconds,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise UpstoxError("Upstox request timed out.", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise UpstoxError("Could not reach Upstox API.", status_code=502) from exc

        if response.status_code == 401:
            raise UpstoxError(
                "Upstox rejected the token. Check its validity or expiry.",
                status_code=401,
            )
        if response.status_code == 429:
            raise UpstoxError(
                "Upstox rate limit reached. Wait before retrying.",
                status_code=429,
            )
        if response.is_error:
            raise UpstoxError(
                f"Upstox API request failed with HTTP {response.status_code}.",
                status_code=502 if response.status_code >= 500 else response.status_code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstoxError("Upstox returned an invalid JSON response.") from exc

        if not isinstance(payload, dict):
            raise UpstoxError("Upstox returned an unexpected JSON response.")
        if payload.get("status") == "error":
            raise UpstoxError("Upstox reported an API error.")
        return payload

    def get_ltp(self, instrument_keys: list[str]) -> dict[str, Any]:
        return self._request(
            "GET",
            "/v2/market-quote/ltp",
            params=[("instrument_key", key) for key in instrument_keys],
        )

    def get_quotes(self, instrument_keys: list[str]) -> dict[str, Any]:
        return self._request(
            "GET",
            "/v2/market-quote/quotes",
            params=[("instrument_key", key) for key in instrument_keys],
        )

    def get_market_status(self, exchange: str) -> dict[str, Any]:
        return self._request("GET", f"/v2/market/status/{quote(exchange, safe='')}")

    def get_holidays(self, holiday_date: date | None = None) -> dict[str, Any]:
        path = "/v2/market/holidays"
        if holiday_date is not None:
            path = f"{path}/{holiday_date.isoformat()}"
        return self._request("GET", path)

    def get_intraday_candles(
        self,
        instrument_key: str,
        unit: str,
        interval: int,
    ) -> dict[str, Any]:
        path = (
            f"/v3/historical-candle/intraday/{quote(instrument_key, safe='')}"
            f"/{unit}/{interval}"
        )
        return self._request("GET", path)

    def get_historical_candles(
        self,
        instrument_key: str,
        unit: str,
        interval: int,
        to_date: date,
        from_date: date,
    ) -> dict[str, Any]:
        path = (
            f"/v3/historical-candle/{quote(instrument_key, safe='')}"
            f"/{unit}/{interval}/{to_date.isoformat()}/{from_date.isoformat()}"
        )
        return self._request("GET", path)

    def refresh_nse_equities(self) -> int:
        try:
            response = httpx.get(
                settings.upstox_instruments_url,
                timeout=settings.upstox_request_timeout_seconds,
            )
            response.raise_for_status()
            raw_instruments = json.loads(gzip.decompress(response.content))
        except httpx.TimeoutException as exc:
            raise UpstoxError("Instrument master download timed out.", 504) from exc
        except (httpx.HTTPError, OSError, gzip.BadGzipFile, json.JSONDecodeError) as exc:
            raise UpstoxError("Could not download or parse the Upstox instrument master.") from exc

        instruments: list[Instrument] = []
        if not isinstance(raw_instruments, list):
            raise UpstoxError("Instrument master response is not a JSON list.")

        for item in raw_instruments:
            if not isinstance(item, dict):
                continue
            if item.get("segment") != "NSE_EQ" or item.get("instrument_type") != "EQ":
                continue
            instrument_key = item.get("instrument_key")
            trading_symbol = item.get("trading_symbol")
            if not isinstance(instrument_key, str) or not isinstance(trading_symbol, str):
                continue
            instruments.append(
                Instrument(
                    instrument_key=instrument_key,
                    trading_symbol=trading_symbol,
                    name=str(item.get("name") or trading_symbol),
                    tick_size=_optional_float(item.get("tick_size")),
                    lot_size=_optional_int(item.get("lot_size")),
                )
            )

        if not instruments:
            raise UpstoxError("Instrument master contained no NSE equity instruments.")
        self._instrument_cache = (date.today(), instruments)
        return len(instruments)

    def search_nse_equities(self, query: str, limit: int) -> list[Instrument]:
        if self._instrument_cache is None or self._instrument_cache[0] != date.today():
            raise UpstoxError(
                "NSE instrument master is not loaded. Refresh it first.",
                status_code=409,
            )
        normalized_query = query.casefold()
        matches = [
            instrument
            for instrument in self._instrument_cache[1]
            if normalized_query in instrument.trading_symbol.casefold()
            or normalized_query in instrument.name.casefold()
        ]
        return matches[:limit]

    def get_nse_equities_by_symbols(self, symbols: list[str]) -> dict[str, Instrument]:
        if self._instrument_cache is None or self._instrument_cache[0] != date.today():
            raise UpstoxError(
                "NSE instrument master is not loaded. Refresh it first.",
                status_code=409,
            )
        requested = {symbol.strip().casefold() for symbol in symbols}
        return {
            instrument.trading_symbol: instrument
            for instrument in self._instrument_cache[1]
            if instrument.trading_symbol.casefold() in requested
        }


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


upstox_client = UpstoxClient()
