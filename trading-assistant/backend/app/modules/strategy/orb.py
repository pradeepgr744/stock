from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")
OPENING_RANGE_START = time(9, 15)
OPENING_RANGE_END = time(9, 30)
SCAN_END = time(15, 0)
ENTRY_CUTOFF = time(14, 45)
R_MULTIPLE_TARGET = 2.0


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
    """Research-only ORB evaluator. It emits candidates, never orders."""

    def __init__(self) -> None:
        self._states: dict[str, _SymbolState] = {}

    def on_feed(self, symbol: str, market_feed: dict) -> list[OrbCandidate]:
        bars = _extract_one_minute_bars(market_feed)
        if not bars:
            return []

        state = self._states.setdefault(symbol, _SymbolState())
        candidates: list[OrbCandidate] = []
        for bar in sorted(bars, key=lambda candle: candle.timestamp):
            if state.session_date != bar.timestamp.date():
                state.session_date = bar.timestamp.date()
                state.pending = None
                state.opening_bars.clear()
                state.breakout_bars.clear()
                state.emitted_directions.clear()

            if state.pending is None:
                state.pending = bar
                continue
            if bar.timestamp <= state.pending.timestamp:
                state.pending = bar
                continue

            candidate = self._complete_bar(symbol, state, state.pending)
            if candidate:
                candidates.append(candidate)
            state.pending = bar

        return candidates

    def _complete_bar(self, symbol: str, state: _SymbolState, bar: MinuteBar) -> OrbCandidate | None:
        minute = bar.timestamp.time().replace(tzinfo=None)
        if OPENING_RANGE_START <= minute < OPENING_RANGE_END:
            state.opening_bars.append(bar)
            return None
        if minute < OPENING_RANGE_END or minute >= SCAN_END or minute > ENTRY_CUTOFF:
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

        opening_high = max(c.high for c in state.opening_bars)
        opening_low = min(c.low for c in state.opening_bars)
        opening_volume = sum(c.volume for c in state.opening_bars) / 3
        breakout_volume = sum(c.volume for c in bucket)
        if breakout_volume < opening_volume:
            return None

        close = bucket[-1].close
        if close > opening_high:
            direction = "BUY"
            stop_loss = opening_low
            risk = close - stop_loss
        elif close < opening_low:
            direction = "SELL"
            stop_loss = opening_high
            risk = stop_loss - close
        else:
            return None

        if risk / close * 100 > 1.2:
            return None
        if risk <= 0 or direction in state.emitted_directions:
            return None

        # Require the breakout close to remain beyond the level: no wick-only signals.
        body = abs(close - bucket[-1].open)
        range_size = opening_high - opening_low
        if range_size <= 0 or body / range_size < 0.25:
            return None

        target = close + R_MULTIPLE_TARGET * risk if direction == "BUY" else close - R_MULTIPLE_TARGET * risk
        if target <= 0:
            return None

        state.emitted_directions.add(direction)
        reason = (
            f"Completed 5-minute close beyond the 9:15–9:30 IST range; "
            f"breakout volume {breakout_volume:g} met opening-range average "
            f"{opening_volume:g}; risk is {risk / close * 100:.2f}% of entry. "
            "Illustrative 2R target; no win probability inferred."
        )
        return OrbCandidate(symbol, direction, close, stop_loss, target, reason, bucket[-1].timestamp)


def _extract_one_minute_bars(market_feed: dict) -> list[MinuteBar]:
    try:
        entries = market_feed["marketOHLC"]["ohlc"]
    except (KeyError, TypeError):
        return []

    bars: list[MinuteBar] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("interval") != "I1":
            continue
        try:
            timestamp = datetime.fromtimestamp(int(entry["ts"]) / 1000, tz=MARKET_TIMEZONE).replace(second=0, microsecond=0)
            values = {name: float(entry[name]) for name in ("open", "high", "low", "close")}
            volume = int(entry["vol"])
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        if not all(isfinite(value) and value > 0 for value in values.values()) or volume < 0:
            continue
        if values["high"] < max(values["open"], values["close"]):
            continue
        if values["low"] > min(values["open"], values["close"]):
            continue
        bars.append(MinuteBar(timestamp=timestamp, volume=volume, **values))
    return bars
