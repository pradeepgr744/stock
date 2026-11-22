from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")
OPENING_RANGE_START = time(9, 15)
OPENING_RANGE_END = time(9, 30)
SCAN_END = time(14, 45)
R_MULTIPLE_TARGET = 2.5
MAX_RISK_PCT = 0.012


@dataclass(frozen=True)
class MinuteBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class OrbCandidate:
    symbol: str
    direction: str
    entry: float
    stop_loss: float
    target: float
    reason: str
    timestamp: datetime


@dataclass
class _SymbolState:
    session_date: date | None = None
    pending: MinuteBar | None = None
    opening_bars: list[MinuteBar] = field(default_factory=list)
    breakout_bars: dict[datetime, list[MinuteBar]] = field(default_factory=dict)
    emitted_directions: set[str] = field(default_factory=set)


class OpeningRangeBreakout:
    """Conservative alert-only ORB evaluator; does not place or manage orders."""

    def __init__(self) -> None:
        self._states: dict[str, _SymbolState] = {}

    def on_feed(self, symbol: str, market_feed: dict) -> list[OrbCandidate]:
        one_minute_bars = _extract_one_minute_bars(market_feed)
        if not one_minute_bars:
            return []

        state = self._states.setdefault(symbol, _SymbolState())
        candidates: list[OrbCandidate] = []
        for bar in sorted(one_minute_bars, key=lambda candle: candle.timestamp):
            if state.session_date != bar.timestamp.date():
                state.session_date = bar.timestamp.date()
                state.pending = None
                state.opening_bars.clear()
                state.breakout_bars.clear()
                state.emitted_directions.clear()

            pending = state.pending
            if pending is None:
                state.pending = bar
                continue
            if bar.timestamp == pending.timestamp:
                state.pending = bar
                continue
            if bar.timestamp < pending.timestamp:
                continue

            candidate = self._complete_bar(symbol, state, pending)
            if candidate is not None:
                candidates.append(candidate)
            state.pending = bar

        return candidates

    def _complete_bar(
        self,
        symbol: str,
        state: _SymbolState,
        bar: MinuteBar,
    ) -> OrbCandidate | None:
        minute = bar.timestamp.time().replace(tzinfo=None)
        if OPENING_RANGE_START <= minute < OPENING_RANGE_END:
            state.opening_bars.append(bar)
            return None

        if minute < OPENING_RANGE_END or minute >= SCAN_END:
            return None
        if len(state.opening_bars) != 15:
            return None

        bucket_minute = minute.minute - minute.minute % 5
        bucket_start = bar.timestamp.replace(minute=bucket_minute, second=0, microsecond=0)
        bucket = state.breakout_bars.setdefault(bucket_start, [])
        if bucket and bar.timestamp - bucket[-1].timestamp != timedelta(minutes=1):
            bucket.clear()
        bucket.append(bar)
        if len(bucket) != 5:
            return None

        highs = [opening.high for opening in state.opening_bars]
        lows = [opening.low for opening in state.opening_bars]
        opening_high = max(highs)
        opening_low = min(lows)
        opening_volume = sum(opening.volume for opening in state.opening_bars) / 3
        breakout_volume = sum(candle.volume for candle in bucket)
        close = bucket[-1].close
        if breakout_volume < opening_volume:
            return None

        direction: str
        if close > opening_high:
            direction = "BUY"
            stop_loss = opening_low
            risk = close - stop_loss
            target = close + R_MULTIPLE_TARGET * risk
        elif close < opening_low:
            direction = "SELL"
            stop_loss = opening_high
            risk = stop_loss - close
            target = close - R_MULTIPLE_TARGET * risk
        else:
            return None

        if risk <= 0 or target <= 0 or risk / close > MAX_RISK_PCT or direction in state.emitted_directions:
            return None

        state.emitted_directions.add(direction)
        reason = (
            f"Completed 5-minute close broke the 9:15–9:30 IST opening range; "
            f"5-minute volume {breakout_volume:g} was at least the opening-range "
            f"5-minute average {opening_volume:g}. Illustrative target is 2.5R."
        )
        return OrbCandidate(
            symbol=symbol,
            direction=direction,
            entry=close,
            stop_loss=stop_loss,
            target=target,
            reason=reason,
            timestamp=bucket[-1].timestamp,
        )


def _extract_one_minute_bars(market_feed: dict) -> list[MinuteBar]:
    try:
        ohlc_entries = market_feed["marketOHLC"]["ohlc"]
    except (KeyError, TypeError):
        return []

    bars: list[MinuteBar] = []
    for entry in ohlc_entries:
        if not isinstance(entry, dict) or entry.get("interval") != "I1":
            continue
        try:
            timestamp = datetime.fromtimestamp(
                int(entry["ts"]) / 1000,
                tz=MARKET_TIMEZONE,
            ).replace(second=0, microsecond=0)
            values = {
                name: float(entry[name])
                for name in ("open", "high", "low", "close")
            }
            volume = int(entry["vol"])
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        if not all(isfinite(value) and value > 0 for value in values.values()):
            continue
        if values["high"] < max(values["open"], values["close"], values["low"]):
            continue
        if values["low"] > min(values["open"], values["close"], values["high"]):
            continue
        if volume < 0:
            continue
        bars.append(MinuteBar(timestamp=timestamp, volume=volume, **values))
    return bars
