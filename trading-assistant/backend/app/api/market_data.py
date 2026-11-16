from datetime import date
from typing import Any, Literal, NoReturn

from fastapi import APIRouter, Body, HTTPException, Query

from ..config import settings
from ..modules.market_data.upstox import UpstoxError, upstox_client
from ..modules.notifications.telegram import TelegramError, telegram_notifier
from ..modules.strategy.orb_scanner import OrbScannerError, orb_scanner

router = APIRouter(prefix="/api", tags=["market-data"])


def _raise_upstox_error(error: UpstoxError) -> NoReturn:
    raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@router.get("/integrations/status")
def integration_status() -> dict[str, dict[str, bool]]:
    return {
        "upstox": {"configured": upstox_client.configured},
        "telegram": {"configured": telegram_notifier.configured},
    }


@router.post("/notifications/test")
def send_test_notification(hint: dict[str, Any] | None = Body(default=None)) -> dict[str, str]:
    default_hint = {
        "symbol": "RELIANCE",
        "strategy": "VWAP breakout",
        "direction": "BUY",
        "entry": 2850.0,
        "stop_loss": 2825.0,
        "target": 2905.0,
        "quantity": 4,
        "reason": "Trend remains strong and price is holding above VWAP",
    }
    payload = {**default_hint, **(hint or {}), "demo": True}

    try:
        telegram_notifier.send_hint(payload)
    except TelegramError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return {"status": "sent", "message": "Telegram trade alert sample has been sent."}


@router.get("/scanner/status")
def scanner_status() -> dict[str, Any]:
    return orb_scanner.status


@router.post("/scanner/start")
def start_scanner() -> dict[str, Any]:
    if orb_scanner.status["running"]:
        return orb_scanner.status

    symbols = [
        symbol.strip().upper()
        for symbol in settings.orb_scanner_symbols.split(",")
        if symbol.strip()
    ]
    if not symbols:
        raise HTTPException(status_code=503, detail="ORB_SCANNER_SYMBOLS is empty.")
    if not upstox_client.configured:
        raise HTTPException(status_code=503, detail="Upstox access token is not configured.")
    if not telegram_notifier.configured:
        raise HTTPException(status_code=503, detail="Telegram is not configured.")

    try:
        upstox_client.refresh_nse_equities()
        resolved = upstox_client.get_nse_equities_by_symbols(symbols)
    except UpstoxError as error:
        _raise_upstox_error(error)

    missing = sorted(set(symbols) - set(resolved))
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"Could not resolve configured NSE equity symbols: {', '.join(missing)}.",
        )
    try:
        return orb_scanner.start(list(resolved.values()))
    except OrbScannerError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@router.post("/scanner/stop")
def stop_scanner() -> dict[str, Any]:
    return orb_scanner.stop()


@router.get("/market-data/ltp")
def get_ltp(
    instrument_keys: list[str] = Query(..., alias="instrument_key", min_length=1, max_length=500),
) -> dict:
    try:
        return upstox_client.get_ltp(instrument_keys)
    except UpstoxError as error:
        _raise_upstox_error(error)


@router.get("/market-data/quotes")
def get_quotes(
    instrument_keys: list[str] = Query(..., alias="instrument_key", min_length=1, max_length=500),
) -> dict:
    try:
        return upstox_client.get_quotes(instrument_keys)
    except UpstoxError as error:
        _raise_upstox_error(error)


@router.get("/market-data/market-status")
def get_market_status(exchange: Literal["NSE", "BSE", "MCX"] = "NSE") -> dict:
    try:
        return upstox_client.get_market_status(exchange)
    except UpstoxError as error:
        _raise_upstox_error(error)


@router.get("/market-data/holidays")
def get_holidays(holiday_date: date | None = Query(default=None, alias="date")) -> dict:
    try:
        return upstox_client.get_holidays(holiday_date)
    except UpstoxError as error:
        _raise_upstox_error(error)


@router.get("/market-data/candles/intraday")
def get_intraday_candles(
    instrument_key: str = Query(..., min_length=1),
    unit: Literal["minutes", "hours", "days"] = "minutes",
    interval: int = Query(1, ge=1, le=300),
) -> dict:
    if unit == "hours" and interval > 5 or unit == "days" and interval != 1:
        raise HTTPException(status_code=422, detail="Invalid interval for the selected candle unit.")
    try:
        return upstox_client.get_intraday_candles(instrument_key, unit, interval)
    except UpstoxError as error:
        _raise_upstox_error(error)


@router.get("/market-data/candles/historical")
def get_historical_candles(
    instrument_key: str = Query(..., min_length=1),
    unit: Literal["minutes", "hours", "days", "weeks", "months"] = "minutes",
    interval: int = Query(1, ge=1, le=300),
    to_date: date = Query(...),
    from_date: date = Query(...),
) -> dict:
    if from_date > to_date:
        raise HTTPException(status_code=422, detail="from_date must not be after to_date.")
    if unit == "hours" and interval > 5 or unit in {"days", "weeks", "months"} and interval != 1:
        raise HTTPException(status_code=422, detail="Invalid interval for the selected candle unit.")
    try:
        return upstox_client.get_historical_candles(
            instrument_key, unit, interval, to_date, from_date
        )
    except UpstoxError as error:
        _raise_upstox_error(error)


@router.post("/instruments/refresh")
def refresh_instruments() -> dict[str, int | str]:
    try:
        count = upstox_client.refresh_nse_equities()
        return {"status": "loaded", "nse_equity_count": count}
    except UpstoxError as error:
        _raise_upstox_error(error)


@router.get("/instruments/search")
def search_instruments(
    query: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=100),
) -> list[dict]:
    try:
        instruments = upstox_client.search_nse_equities(query, limit)
    except UpstoxError as error:
        _raise_upstox_error(error)
    return [instrument.__dict__ for instrument in instruments]
