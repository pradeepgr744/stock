"""Research-first NSE intraday setup evaluator.

Scores are setup-quality points, NOT calibrated win probabilities. This module is
intentionally pure: feed it completed candles and independently computed
time-of-day RVOL / regime data. It does not place orders or send alerts.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Sequence


class Regime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Candle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Setup:
    symbol: str
    strategy: str
    direction: str
    quality_score: int
    entry: float
    stop_loss: float
    target: float
    risk_reward: float
    rvol: float
    regime: str
    sector_aligned: bool
    reasons: tuple[str, ...]


def _valid(c: Candle) -> bool:
    values = (c.open, c.high, c.low, c.close, c.volume)
    return (
        all(isfinite(v) for v in values)
        and min(c.open, c.high, c.low, c.close) > 0
        and c.volume >= 0
        and c.high >= max(c.open, c.close, c.low)
        and c.low <= min(c.open, c.close, c.high)
    )


def _atr(candles: Sequence[Candle], period: int = 14) -> float:
    sample = candles[-(period + 1):]
    true_ranges = []
    for i, candle in enumerate(sample):
        previous_close = sample[i - 1].close if i else candle.close
        true_ranges.append(max(
            candle.high - candle.low,
            abs(candle.high - previous_close),
            abs(candle.low - previous_close),
        ))
    return sum(true_ranges[-period:]) / min(period, len(true_ranges))


def _session_vwap(candles: Sequence[Candle]) -> float:
    total_volume = sum(c.volume for c in candles)
    if total_volume <= 0:
        return 0.0
    return sum(((c.high + c.low + c.close) / 3) * c.volume for c in candles) / total_volume


def _quality(
    *,
    setup_points: int,
    regime_aligned: bool,
    sector_aligned: bool,
    rvol: float,
    liquid: bool,
    risk_reward: float,
) -> int:
    # Transparent rule score. It is deliberately not called probability.
    return (
        setup_points
        + (20 if regime_aligned else 0)
        + (15 if sector_aligned else 0)
        + (15 if rvol >= 2.0 else 10 if rvol >= 1.5 else 0)
        + (10 if liquid else 0)
        + (5 if risk_reward >= 2.5 else 0)
    )


def evaluate_setups(
    *,
    symbol: str,
    candles: Sequence[Candle],
    regime: Regime,
    sector_direction: str,
    time_adjusted_rvol: float,
    liquid: bool,
    previous_day_high: float | None = None,
    previous_day_low: float | None = None,
    minimum_quality: int = 75,
    minimum_risk_reward: float = 2.0,
    maximum_risk_pct: float = 1.2,
) -> list[Setup]:
    """Evaluate completed 5-minute candles from today's session.

    time_adjusted_rvol must compare the current time bucket with the same
    bucket across prior sessions. A raw candle-volume ratio is not equivalent.
    """
    if len(candles) < 20 or not all(_valid(c) for c in candles):
        return []
    if not isfinite(time_adjusted_rvol) or time_adjusted_rvol < 0:
        return []
    if not liquid:
        return []

    current, previous = candles[-1], candles[-2]
    atr = _atr(candles)
    vwap = _session_vwap(candles)
    if atr <= 0 or vwap <= 0 or current.close <= 0:
        return []

    sector = sector_direction.upper()
    results: list[Setup] = []

    def add(strategy: str, direction: str, stop: float, setup_points: int,
            regime_aligned: bool, reasons: list[str]) -> None:
        entry = current.close
        risk = entry - stop if direction == "LONG" else stop - entry
        if risk <= 0 or risk / entry * 100 > maximum_risk_pct:
            return
        target = entry + minimum_risk_reward * risk if direction == "LONG" else entry - minimum_risk_reward * risk
        if target <= 0:
            return
        rr = abs(target - entry) / risk
        aligned_sector = sector == direction
        score = _quality(
            setup_points=setup_points,
            regime_aligned=regime_aligned,
            sector_aligned=aligned_sector,
            rvol=time_adjusted_rvol,
            liquid=liquid,
            risk_reward=rr,
        )
        if score < minimum_quality or time_adjusted_rvol < 1.5:
            return
        results.append(Setup(
            symbol=symbol,
            strategy=strategy,
            direction=direction,
            quality_score=score,
            entry=round(entry, 2),
            stop_loss=round(stop, 2),
            target=round(target, 2),
            risk_reward=round(rr, 2),
            rvol=round(time_adjusted_rvol, 2),
            regime=regime.value,
            sector_aligned=aligned_sector,
            reasons=tuple(reasons + [
                f"Time-adjusted RVOL: {time_adjusted_rvol:.2f}x",
                f"Session VWAP: {vwap:.2f}",
                f"Risk/reward: {rr:.2f}R",
            ]),
        ))

    if (
        regime == Regime.BULL and sector == "LONG"
        and current.close > vwap and current.close > previous.high
        and previous.low <= vwap + 0.25 * atr
    ):
        stop = min(previous.low, current.low) - 0.1 * atr
        add(
            "VWAP_PULLBACK_CONTINUATION", "LONG", stop, 35, True,
            ["Bullish index regime", "Sector aligned", "VWAP pullback reclaimed with a close above prior high"],
        )

    if (
        regime == Regime.BEAR and sector == "SHORT"
        and current.close < vwap and current.close < previous.low
        and previous.high >= vwap - 0.25 * atr
    ):
        stop = max(previous.high, current.high) + 0.1 * atr
        add(
            "VWAP_REJECTION_CONTINUATION", "SHORT", stop, 35, True,
            ["Bearish index regime", "Sector aligned", "VWAP rejection confirmed below prior low"],
        )

    if previous_day_low and regime != Regime.BEAR:
        if current.low < previous_day_low and current.close > previous_day_low and current.close > current.open:
            stop = min(current.low, previous.low) - 0.1 * atr
            add(
                "PDL_SWEEP_RECLAIM", "LONG", stop, 35,
                regime == Regime.BULL,
                ["Price swept previous-day low and closed back above it", "Bullish rejection candle"],
            )

    if previous_day_high and regime != Regime.BULL:
        if current.high > previous_day_high and current.close < previous_day_high and current.close < current.open:
            stop = max(current.high, previous.high) + 0.1 * atr
            add(
                "PDH_SWEEP_RECLAIM", "SHORT", stop, 35,
                regime == Regime.BEAR,
                ["Price swept previous-day high and closed back below it", "Bearish rejection candle"],
            )

    return sorted(results, key=lambda setup: setup.quality_score, reverse=True)
