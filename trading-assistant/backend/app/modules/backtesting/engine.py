"""Deterministic, next-bar-fill backtesting for NSE cash intraday strategies.

CSV input is point-in-time historical 5-minute OHLCV. No look-ahead:
signals are formed after a completed bar and enter at the next bar open.
When stop and target are both touched in one candle, stop is assumed first.
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ENTRY_START = time(9, 30)
ENTRY_CUTOFF = time(14, 45)
EXIT_TIME = time(15, 25)


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class CostModel:
    # Supply buy/sell charges from the user's current broker contract note.
    buy_cost_bps: float
    sell_cost_bps: float
    slippage_bps_per_side: float = 5.0
    brokerage_per_order: float = 20.0
    brokerage_cap_pct: float = 0.03


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 50_000.0
    risk_pct: float = 0.5
    max_trades_per_day: int = 3
    target_r: float = 2.0
    max_risk_pct_of_price: float = 1.2
    max_entry_notional_pct: float = 100.0


@dataclass(frozen=True)
class Signal:
    timestamp: datetime
    symbol: str
    strategy: str
    side: str
    stop: float
    reason: str


@dataclass
class Trade:
    date: str
    symbol: str
    strategy: str
    side: str
    signal_time: str
    entry_time: str
    exit_time: str
    entry: float
    exit: float
    stop: float
    target: float
    quantity: int
    gross_pnl: float
    costs: float
    net_pnl: float
    initial_risk: float
    r_multiple: float
    exit_reason: str


def load_bars(path: str | Path) -> list[Bar]:
    required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
    bars: list[Bar] = []
    with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError("CSV missing required columns: " + ", ".join(sorted(missing)))
        for line, row in enumerate(reader, start=2):
            try:
                stamp = datetime.fromisoformat(row["timestamp"])
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=IST)
                else:
                    stamp = stamp.astimezone(IST)
                bar = Bar(
                    timestamp=stamp,
                    symbol=row["symbol"].strip().upper(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid CSV row {line}: {exc}") from exc
            values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
            if not bar.symbol or not all(math.isfinite(v) for v in values):
                raise ValueError(f"Invalid symbol or non-finite value on CSV row {line}")
            if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
                raise ValueError(f"Non-positive price or negative volume on CSV row {line}")
            if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
                raise ValueError(f"Invalid OHLC relationship on CSV row {line}")
            bars.append(bar)
    bars.sort(key=lambda item: (item.symbol, item.timestamp))
    return bars


def _atr(history: list[Bar], period: int = 14) -> float:
    sample = history[-(period + 1):]
    ranges = []
    for index, bar in enumerate(sample):
        prev_close = sample[index - 1].close if index else bar.close
        ranges.append(max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close)))
    return sum(ranges[-period:]) / max(1, min(period, len(ranges)))


def _vwap(session: list[Bar]) -> float:
    volume = sum(bar.volume for bar in session)
    if volume <= 0:
        return 0.0
    return sum(((bar.high + bar.low + bar.close) / 3.0) * bar.volume for bar in session) / volume


def generate_signals(bars: list[Bar], strategy: str) -> list[Signal]:
    if strategy not in {"orb", "vwap", "sweep"}:
        raise ValueError("strategy must be one of: orb, vwap, sweep")
    grouped: dict[tuple[str, date], list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[(bar.symbol, bar.timestamp.date())].append(bar)

    previous_day_levels: dict[str, tuple[float, float]] = {}
    signals: list[Signal] = []

    for (symbol, session_date), session in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0])):
        session.sort(key=lambda item: item.timestamp)
        if not session:
            continue
        prior_levels = previous_day_levels.get(symbol)
        opening = [b for b in session if time(9, 15) <= b.timestamp.time() < time(9, 30)]
        opening_high = max((b.high for b in opening), default=0.0)
        opening_low = min((b.low for b in opening), default=0.0)
        opening_avg_volume = sum(b.volume for b in opening) / len(opening) if opening else 0.0
        seen = False
        for index in range(1, len(session) - 1):
            current = session[index]
            previous = session[index - 1]
            if not (ENTRY_START <= current.timestamp.time() <= ENTRY_CUTOFF):
                continue
            history = session[: index + 1]
            atr = _atr(history)
            if atr <= 0:
                continue
            side = ""
            stop = 0.0
            reason = ""

            if strategy == "orb" and opening and current.timestamp.time() >= time(9, 30):
                if current.close > opening_high and current.volume >= opening_avg_volume:
                    side = "LONG"
                    stop = min(opening_low, current.low) - 0.1 * atr
                    reason = "Completed 5-minute close above 09:15-09:30 opening range with volume confirmation"
                elif current.close < opening_low and current.volume >= opening_avg_volume:
                    side = "SHORT"
                    stop = max(opening_high, current.high) + 0.1 * atr
                    reason = "Completed 5-minute close below 09:15-09:30 opening range with volume confirmation"

            elif strategy == "vwap":
                current_vwap = _vwap(history)
                previous_vwap = _vwap(session[:index])
                if current.close > current_vwap and previous.low <= previous_vwap * 1.0025 and current.close > previous.high:
                    side = "LONG"
                    stop = min(previous.low, current.low) - 0.1 * atr
                    reason = "VWAP pullback and bullish close above previous candle high"
                elif current.close < current_vwap and previous.high >= previous_vwap * 0.9975 and current.close < previous.low:
                    side = "SHORT"
                    stop = max(previous.high, current.high) + 0.1 * atr
                    reason = "VWAP rejection and bearish close below previous candle low"

            elif strategy == "sweep" and prior_levels:
                prior_high, prior_low = prior_levels
                if current.low < prior_low and current.close > prior_low and current.close > current.open:
                    side = "LONG"
                    stop = min(current.low, previous.low) - 0.1 * atr
                    reason = "Previous-day low swept and reclaimed with bullish close"
                elif current.high > prior_high and current.close < prior_high and current.close < current.open:
                    side = "SHORT"
                    stop = max(current.high, previous.high) + 0.1 * atr
                    reason = "Previous-day high swept and reclaimed with bearish close"

            if side and not seen and stop > 0:
                signals.append(Signal(current.timestamp, symbol, strategy.upper(), side, stop, reason))
                seen = True

        # Levels become available only to the next session.
        previous_day_levels[symbol] = (max(b.high for b in session), min(b.low for b in session))

    return sorted(signals, key=lambda item: (item.timestamp, item.symbol, item.strategy))


def _order_cost(price: float, quantity: int, variable_bps: float, costs: CostModel) -> float:
    turnover = price * quantity
    brokerage = min(costs.brokerage_per_order, turnover * costs.brokerage_cap_pct / 100.0)
    return turnover * (variable_bps + costs.slippage_bps_per_side) / 10_000.0 + brokerage


def _simulate_trade(
    signal: Signal,
    session: list[Bar],
    signal_index: int,
    equity: float,
    config: BacktestConfig,
    costs: CostModel,
) -> Trade | None:
    if signal_index + 1 >= len(session):
        return None
    eligible_session = [bar for bar in session if bar.timestamp.time() <= EXIT_TIME]
    if signal_index + 1 >= len(eligible_session):
        return None
    entry_bar = eligible_session[signal_index + 1]
    if (entry_bar.timestamp - signal.timestamp).total_seconds() > 5 * 60:
        return None
    entry = entry_bar.open
    risk_per_share = entry - signal.stop if signal.side == "LONG" else signal.stop - entry
    if risk_per_share <= 0 or risk_per_share / entry * 100 > config.max_risk_pct_of_price:
        return None

    round_trip_bps = costs.buy_cost_bps + costs.sell_cost_bps + 2 * costs.slippage_bps_per_side
    risk_budget = equity * config.risk_pct / 100.0
    risk_after_fixed_fees = max(0.0, risk_budget - 2 * costs.brokerage_per_order)
    qty_by_risk = math.floor(risk_after_fixed_fees / (risk_per_share + entry * round_trip_bps / 10_000.0))
    qty_by_cash = math.floor(equity * config.max_entry_notional_pct / 100.0 / entry)
    quantity = min(qty_by_risk, qty_by_cash)
    if quantity < 1:
        return None

    target = entry + config.target_r * risk_per_share if signal.side == "LONG" else entry - config.target_r * risk_per_share
    exit_price = eligible_session[-1].close
    exit_bar = eligible_session[-1]
    exit_reason = "EOD"
    for bar in eligible_session[signal_index + 1:]:
        if signal.side == "LONG":
            stop_hit, target_hit = bar.low <= signal.stop, bar.high >= target
            if stop_hit:
                exit_price = min(bar.open, signal.stop) if bar.open < signal.stop else signal.stop
                exit_bar, exit_reason = bar, "STOP"
                break
            if target_hit:
                exit_price = max(bar.open, target) if bar.open > target else target
                exit_bar, exit_reason = bar, "TARGET"
                break
        else:
            stop_hit, target_hit = bar.high >= signal.stop, bar.low <= target
            if stop_hit:
                exit_price = max(bar.open, signal.stop) if bar.open > signal.stop else signal.stop
                exit_bar, exit_reason = bar, "STOP"
                break
            if target_hit:
                exit_price = min(bar.open, target) if bar.open < target else target
                exit_bar, exit_reason = bar, "TARGET"
                break

    if signal.side == "LONG":
        gross = (exit_price - entry) * quantity
        buy_cost = _order_cost(entry, quantity, costs.buy_cost_bps, costs)
        sell_cost = _order_cost(exit_price, quantity, costs.sell_cost_bps, costs)
    else:
        gross = (entry - exit_price) * quantity
        sell_cost = _order_cost(entry, quantity, costs.sell_cost_bps, costs)
        buy_cost = _order_cost(exit_price, quantity, costs.buy_cost_bps, costs)
    total_cost = buy_cost + sell_cost
    net = gross - total_cost
    initial_risk = risk_per_share * quantity
    return Trade(
        date=signal.timestamp.date().isoformat(),
        symbol=signal.symbol,
        strategy=signal.strategy,
        side=signal.side,
        signal_time=signal.timestamp.isoformat(),
        entry_time=entry_bar.timestamp.isoformat(),
        exit_time=exit_bar.timestamp.isoformat(),
        entry=round(entry, 4),
        exit=round(exit_price, 4),
        stop=round(signal.stop, 4),
        target=round(target, 4),
        quantity=quantity,
        gross_pnl=round(gross, 2),
        costs=round(total_cost, 2),
        net_pnl=round(net, 2),
        initial_risk=round(initial_risk, 2),
        r_multiple=round(net / initial_risk, 4) if initial_risk else 0.0,
        exit_reason=exit_reason,
    )


def run_backtest(
    bars: list[Bar],
    strategies: Iterable[str],
    config: BacktestConfig,
    costs: CostModel,
) -> dict:
    if not bars:
        raise ValueError("No historical bars supplied")
    grouped: dict[tuple[str, date], list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[(bar.symbol, bar.timestamp.date())].append(bar)
    for values in grouped.values():
        values.sort(key=lambda item: item.timestamp)

    strategy_list = list(strategies)
    if not strategy_list:
        raise ValueError("At least one strategy is required")
    signals_by_strategy = {name: generate_signals(bars, name) for name in strategy_list}
    all_signals = sorted(
        (signal for items in signals_by_strategy.values() for signal in items),
        key=lambda item: (item.timestamp, item.symbol, item.strategy),
    )

    # One open position at a time is deliberately conservative for this
    # initial single-account, manual-cash simulation.
    equity = config.initial_capital
    peak_equity = equity
    max_drawdown = 0.0
    daily_count: dict[date, int] = defaultdict(int)
    last_exit: datetime | None = None
    trades: list[Trade] = []
    consumed_symbol_day: set[tuple[str, date]] = set()

    for signal in all_signals:
        day = signal.timestamp.date()
        if daily_count[day] >= config.max_trades_per_day:
            continue
        if (signal.symbol, day) in consumed_symbol_day:
            continue
        if last_exit is not None and signal.timestamp <= last_exit:
            continue
        session = grouped.get((signal.symbol, day), [])
        try:
            signal_index = next(i for i, bar in enumerate(session) if bar.timestamp == signal.timestamp)
        except StopIteration:
            continue
        trade = _simulate_trade(signal, session, signal_index, equity, config, costs)
        if trade is None:
            continue
        trades.append(trade)
        equity += trade.net_pnl
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity * 100.0)
        daily_count[day] += 1
        consumed_symbol_day.add((signal.symbol, day))
        last_exit = datetime.fromisoformat(trade.exit_time)

    wins = [trade for trade in trades if trade.net_pnl > 0]
    losses = [trade for trade in trades if trade.net_pnl < 0]
    gross_profit = sum(trade.net_pnl for trade in wins)
    gross_loss = abs(sum(trade.net_pnl for trade in losses))
    total_net = sum(trade.net_pnl for trade in trades)
    r_values = [trade.r_multiple for trade in trades]
    metrics = {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 2) if trades else 0.0,
        "net_pnl": round(total_net, 2),
        "ending_equity": round(config.initial_capital + total_net, 2),
        "return_pct": round(total_net / config.initial_capital * 100.0, 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else (None if gross_profit == 0 else "inf"),
        "expectancy_per_trade": round(total_net / len(trades), 2) if trades else 0.0,
        "expectancy_r": round(sum(r_values) / len(r_values), 4) if r_values else 0.0,
        "max_drawdown_pct": round(max_drawdown, 2),
        "total_costs": round(sum(trade.costs for trade in trades), 2),
        "daily_trade_cap": config.max_trades_per_day,
        "risk_pct_per_trade": config.risk_pct,
    }
    return {
        "config": asdict(config),
        "cost_model": asdict(costs),
        "metrics": metrics,
        "trades": [asdict(trade) for trade in trades],
    }


def compare_strategies(bars: list[Bar], config: BacktestConfig, costs: CostModel) -> dict:
    results = {
        strategy: run_backtest(bars, [strategy], config, costs)
        for strategy in ("orb", "vwap", "sweep")
    }
    results["combined"] = run_backtest(bars, ("orb", "vwap", "sweep"), config, costs)
    ranking = sorted(
        results,
        key=lambda name: (
            results[name]["metrics"]["expectancy_r"],
            results[name]["metrics"]["profit_factor"] if isinstance(results[name]["metrics"]["profit_factor"], (int, float)) else 0,
        ),
        reverse=True,
    )
    return {"ranking_by_observed_expectancy_r": ranking, "results": results}


def write_report(report: dict, output: str | Path) -> None:
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
