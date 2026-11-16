"""Deterministic intraday portfolio risk gate.

This module is broker-agnostic. Call it before every entry and feed it fills,
marks, and exits from the execution adapter. It does not submit orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from math import isfinite
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class RiskLimits:
    capital: float = 50_000.0
    risk_per_trade_pct: float = 0.5
    max_open_risk: float = 500.0
    max_daily_loss: float = 750.0
    max_open_positions: int = 2
    max_trades_per_day: int = 3
    entry_start: time = time(9, 30)
    entry_cutoff: time = time(14, 45)
    square_off_time: time = time(15, 15)

    def __post_init__(self) -> None:
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        if not 0 < self.risk_per_trade_pct <= 2:
            raise ValueError("risk_per_trade_pct must be in (0, 2]")
        if min(self.max_open_risk, self.max_daily_loss) <= 0:
            raise ValueError("risk limits must be positive")
        if self.max_open_positions < 1 or self.max_trades_per_day < 1:
            raise ValueError("position and trade limits must be at least one")
        if not self.entry_start < self.entry_cutoff < self.square_off_time:
            raise ValueError("entry_start < entry_cutoff < square_off_time is required")

    @property
    def per_trade_risk(self) -> float:
        return self.capital * self.risk_per_trade_pct / 100.0


@dataclass(frozen=True)
class Position:
    symbol: str
    side: str
    quantity: int
    entry_price: float
    stop_price: float
    planned_risk: float
    opened_at: datetime
    mark_price: float | None = None

    @property
    def unrealized_pnl(self) -> float:
        mark = self.entry_price if self.mark_price is None else self.mark_price
        points = mark - self.entry_price
        if self.side == "SHORT":
            points = -points
        return points * self.quantity


@dataclass(frozen=True)
class EntryDecision:
    allowed: bool
    reason: str
    planned_risk: float = 0.0


@dataclass
class IntradayRiskManager:
    limits: RiskLimits = field(default_factory=RiskLimits)
    session_date: date | None = None
    entries_today: int = 0
    realized_pnl: float = 0.0
    halted: bool = False
    positions: dict[str, Position] = field(default_factory=dict)

    def _ist(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(IST)

    def _prepare_session(self, now: datetime) -> bool:
        local = self._ist(now)
        if self.session_date is None:
            self.session_date = local.date()
            return True
        if local.date() == self.session_date:
            return True
        # A new session must never silently inherit an overnight position.
        if self.positions:
            self.halted = True
            return False
        self.session_date = local.date()
        self.entries_today = 0
        self.realized_pnl = 0.0
        self.halted = False
        return True

    @property
    def open_planned_risk(self) -> float:
        return sum(position.planned_risk for position in self.positions.values())

    @property
    def unrealized_pnl(self) -> float:
        return sum(position.unrealized_pnl for position in self.positions.values())

    @property
    def day_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    def must_flatten(self, now: datetime) -> bool:
        local = self._ist(now)
        if not self._prepare_session(local):
            return True
        return bool(self.positions) and (
            local.time() >= self.limits.square_off_time
            or self.day_pnl <= -self.limits.max_daily_loss
        )

    def evaluate_entry(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_price: float,
        now: datetime,
        estimated_round_trip_cost: float = 0.0,
    ) -> EntryDecision:
        local = self._ist(now)
        if not self._prepare_session(local):
            return EntryDecision(False, "Overnight position detected; new session blocked")
        if self.halted:
            return EntryDecision(False, "Daily risk halt is active")
        if local.time() < self.limits.entry_start:
            return EntryDecision(False, "Before intraday entry window")
        if local.time() > self.limits.entry_cutoff:
            return EntryDecision(False, "Entry cutoff passed")
        if local.time() >= self.limits.square_off_time:
            return EntryDecision(False, "Square-off time reached")
        if self.day_pnl <= -self.limits.max_daily_loss:
            self.halted = True
            return EntryDecision(False, "Daily loss limit reached; flatten open positions")
        if self.entries_today >= self.limits.max_trades_per_day:
            return EntryDecision(False, "Maximum trades for the day reached")
        if len(self.positions) >= self.limits.max_open_positions:
            return EntryDecision(False, "Maximum concurrent positions reached")
        key = symbol.strip().upper()
        if not key or key in self.positions:
            return EntryDecision(False, "Invalid symbol or position already open")
        normalized_side = side.upper()
        if normalized_side not in {"LONG", "SHORT"}:
            return EntryDecision(False, "Side must be LONG or SHORT")
        if quantity < 1 or not all(isfinite(x) for x in (entry_price, stop_price, estimated_round_trip_cost)):
            return EntryDecision(False, "Invalid quantity, price, or estimated cost")
        if entry_price <= 0 or stop_price <= 0 or estimated_round_trip_cost < 0:
            return EntryDecision(False, "Prices must be positive and costs non-negative")
        per_share = entry_price - stop_price if normalized_side == "LONG" else stop_price - entry_price
        if per_share <= 0:
            return EntryDecision(False, "Stop must be below LONG entry or above SHORT entry")
        planned_risk = per_share * quantity + estimated_round_trip_cost
        if planned_risk > self.limits.per_trade_risk:
            return EntryDecision(False, "Per-trade risk budget exceeded", planned_risk)
        if self.open_planned_risk + planned_risk > self.limits.max_open_risk:
            return EntryDecision(False, "Aggregate open-risk limit exceeded", planned_risk)
        return EntryDecision(True, "Risk checks passed", planned_risk)

    def register_entry(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_price: float,
        now: datetime,
        estimated_round_trip_cost: float = 0.0,
    ) -> EntryDecision:
        decision = self.evaluate_entry(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            now=now,
            estimated_round_trip_cost=estimated_round_trip_cost,
        )
        if not decision.allowed:
            return decision
        local = self._ist(now)
        key = symbol.strip().upper()
        self.positions[key] = Position(
            symbol=key,
            side=side.upper(),
            quantity=quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            planned_risk=decision.planned_risk,
            opened_at=local,
        )
        self.entries_today += 1
        return decision

    def update_mark(self, symbol: str, price: float) -> None:
        if not isfinite(price) or price <= 0:
            raise ValueError("mark price must be finite and positive")
        key = symbol.strip().upper()
        position = self.positions.get(key)
        if position is None:
            raise KeyError(f"No open position for {key}")
        self.positions[key] = Position(
            **{**position.__dict__, "mark_price": price}
        )
        if self.day_pnl <= -self.limits.max_daily_loss:
            self.halted = True

    def register_exit(self, symbol: str, *, exit_price: float, now: datetime,
                      additional_cost: float = 0.0) -> float:
        self._ist(now)
        key = symbol.strip().upper()
        position = self.positions.get(key)
        if position is None:
            raise KeyError(f"No open position for {key}")
        if not isfinite(exit_price) or exit_price <= 0 or not isfinite(additional_cost) or additional_cost < 0:
            raise ValueError("exit price must be positive and costs finite and non-negative")
        self.positions.pop(key)
        points = exit_price - position.entry_price
        if position.side == "SHORT":
            points = -points
        pnl = points * position.quantity - additional_cost
        self.realized_pnl += pnl
        if self.day_pnl <= -self.limits.max_daily_loss:
            self.halted = True
        return pnl
