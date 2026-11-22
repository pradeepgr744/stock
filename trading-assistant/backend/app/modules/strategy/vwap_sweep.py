"""Live, completed-one-minute-bar VWAP and liquidity sweep/reclaim signals.

Quality scores are rule-based setup scores, not win probabilities. This module
only emits manual-review candidates; it never places or manages broker orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from math import isfinite
from zoneinfo import ZoneInfo

from .orb import MinuteBar, _extract_one_minute_bars

MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")
ENTRY_START = time(9, 30)
ENTRY_END = time(14, 45)
LOOKBACK_BARS = 20
MIN_RVOL = 1.5
MIN_QUALITY = 75
MAX_RISK_PCT = 1.2
TARGET_R = 2.5


@dataclass(frozen=True)
class LiveSignal:
    symbol: str
    strategy: str
    direction: str
    entry: float
    stop_loss: float
    target: float
    quality_score: int
    rvol: float
    vwap: float
    reason: str
    timestamp: datetime


@dataclass
class _SymbolState:
    session_date: date | None = None
    pending: MinuteBar | None = None
    bars: list[MinuteBar] = field(default_factory=list)
    emitted: set[str] = field(default_factory=set)


class VwapSweepReclaim:
    """Evaluate VWAP reclaim and 20-minute liquidity sweep/reclaim patterns."""

    def __init__(self) -> None:
        self._states: dict[str, _SymbolState] = {}

    def on_feed(self, symbol: str, market_feed: dict) -> list[LiveSignal]:
        incoming = _extract_one_minute_bars(market_feed)
        if not incoming:
            return []

        state = self._states.setdefault(symbol, _SymbolState())
        signals: list[LiveSignal] = []
        for bar in sorted(incoming, key=lambda item: item.timestamp):
            if state.session_date != bar.timestamp.date():
                state.session_date = bar.timestamp.date()
                state.pending = None
                state.bars.clear()
                state.emitted.clear()

            if state.pending is None:
                state.pending = bar
                continue
            if bar.timestamp == state.pending.timestamp:
                state.pending = bar
                continue
            if bar.timestamp < state.pending.timestamp:
                continue

            completed = state.pending
            state.bars.append(completed)
            state.pending = bar
            signal = self._evaluate(symbol, state, completed)
            if signal is not None:
                signals.append(signal)
        return signals

    def _evaluate(
        self,
        symbol: str,
        state: _SymbolState,
        current: MinuteBar,
    ) -> LiveSignal | None:
        minute = current.timestamp.astimezone(MARKET_TIMEZONE).time().replace(tzinfo=None)
        if minute < ENTRY_START or minute >= ENTRY_END:
            return None
        if len(state.bars) < LOOKBACK_BARS + 1:
            return None

        prior = state.bars[-LOOKBACK_BARS - 1:-1]
        current = state.bars[-1]
        average_volume = sum(bar.volume for bar in prior) / len(prior)
        if average_volume <= 0:
            return None
        rvol = current.volume / average_volume
        if not isfinite(rvol) or rvol < MIN_RVOL:
            return None

        session_volume = sum(bar.volume for bar in state.bars if bar.volume > 0)
        if session_volume <= 0:
            return None
        vwap = sum(
            ((bar.high + bar.low + bar.close) / 3) * bar.volume
            for bar in state.bars
        ) / session_volume
        previous_bars = state.bars[:-1]
        previous_volume = sum(bar.volume for bar in previous_bars)
        previous_vwap = (
            sum(((bar.high + bar.low + bar.close) / 3) * bar.volume for bar in previous_bars)
            / previous_volume
            if previous_volume > 0 else vwap
        )

        sample = state.bars[-15:]
        true_ranges: list[float] = []
        for index, bar in enumerate(sample):
            previous_close = sample[index - 1].close if index else bar.close
            true_ranges.append(max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            ))
        atr = sum(true_ranges) / len(true_ranges)
        if atr <= 0 or not isfinite(atr):
            return None

        prior_low = min(bar.low for bar in prior)
        prior_high = max(bar.high for bar in prior)
        trend_mean = sum(bar.close for bar in prior) / len(prior)
        candle_range = current.high - current.low
        body_ratio = (
            abs(current.close - current.open) / candle_range
            if candle_range > 0 else 0.0
        )
        trend_long = current.close > trend_mean
        trend_short = current.close < trend_mean

        candidates: list[tuple[str, str, float, list[str]]] = []
        if (
            current.low < prior_low - 0.05 * atr
            and current.close > prior_low
            and current.close > vwap
            and current.close > current.open
        ):
            candidates.append((
                "LIQUIDITY_SWEEP_RECLAIM",
                "BUY",
                current.low - 0.1 * atr,
                ["Swept the prior 20-minute low and closed back above it",
                 "Bullish close reclaimed session VWAP"],
            ))
        if (
            current.high > prior_high + 0.05 * atr
            and current.close < prior_high
            and current.close < vwap
            and current.close < current.open
        ):
            candidates.append((
                "LIQUIDITY_SWEEP_RECLAIM",
                "SELL",
                current.high + 0.1 * atr,
                ["Swept the prior 20-minute high and closed back below it",
                 "Bearish close lost session VWAP"],
            ))

        if (
            current.close > vwap
            and previous_bars[-1].close <= previous_vwap
            and current.low <= vwap + 0.1 * atr
            and current.close > current.open
        ):
            candidates.append((
                "VWAP_RECLAIM",
                "BUY",
                min(current.low, vwap) - 0.1 * atr,
                ["Price crossed back above session VWAP",
                 "Retest held near VWAP and closed bullish"],
            ))
        if (
            current.close < vwap
            and previous_bars[-1].close >= previous_vwap
            and current.high >= vwap - 0.1 * atr
            and current.close < current.open
        ):
            candidates.append((
                "VWAP_RECLAIM",
                "SELL",
                max(current.high, vwap) + 0.1 * atr,
                ["Price crossed back below session VWAP",
                 "Retest rejected near VWAP and closed bearish"],
            ))

        for strategy, direction, stop, reasons in candidates:
            key = f"{strategy}:{direction}"
            if key in state.emitted:
                continue
            entry = current.close
            risk = entry - stop if direction == "BUY" else stop - entry
            if risk <= 0 or risk / entry * 100 > MAX_RISK_PCT:
                continue
            target = entry + TARGET_R * risk if direction == "BUY" else entry - TARGET_R * risk
            if target <= 0:
                continue

            trend_aligned = trend_long if direction == "BUY" else trend_short
            score = (
                50
                + (15 if rvol >= MIN_RVOL else 0)
                + (10 if rvol >= 2.0 else 0)
                + (15 if trend_aligned else 0)
                + (10 if body_ratio >= 0.6 else 0)
            )
            if score < MIN_QUALITY:
                continue

            state.emitted.add(key)
            reasons.extend([
                f"Relative volume: {rvol:.2f}x recent 1-minute average",
                f"Session VWAP: {vwap:.2f}",
                f"Rule-based setup score: {score}/100 (not a win probability)",
                f"Illustrative target: {TARGET_R:.1f}R",
            ])
            return LiveSignal(
                symbol=symbol,
                strategy=strategy,
                direction=direction,
                entry=round(entry, 2),
                stop_loss=round(stop, 2),
                target=round(target, 2),
                quality_score=score,
                rvol=round(rvol, 2),
                vwap=round(vwap, 2),
                reason="; ".join(reasons),
                timestamp=current.timestamp,
            )
        return None
