"""Deterministic intraday portfolio risk gate.

Broker-agnostic controls for manual fills. This module never submits orders.
The manager is in-memory; production position/P&L enforcement requires the
manual trade journal or a broker position adapter to feed fills and marks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
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
    max_capital_exposure: float = 50_000.0
    max_stop_distance_pct: float = 1.2
    min_reward_risk: float = 2.0
    max_consecutive_losses: int = 2
    cooldown_minutes_after_loss: int = 15
    entry_start: time = time(9, 30)
    entry_cutoff: time = time(14, 45)
    square_off_time: time = time(15, 15)

    def __post_init__(self) -> None:
        if self.capital <= 0 or self.max_capital_exposure <= 0:
            raise ValueError("capital and max_capital_exposure must be positive")
        if not 0 < self.risk_per_trade_pct <= 2:
            raise ValueError("risk_per_trade_pct must be in (0, 2]")
        if min(self.max_open_risk, self.max_daily_loss, self.max_stop_distance_pct) <= 0:
            raise ValueError("risk and stop-distance limits must be positive")
        if self.min_reward_risk < 1:
            raise ValueError("min_reward_risk must be at least 1")
        if self.max_consecutive_losses < 1 or self.cooldown_minutes_after_loss < 0:
            raise ValueError("loss-streak and cooldown limits are invalid")
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
    entry_cost: float = 0.0

    @property
    def notional(self) -> float:
        return self.quantity * self.entry_price

    @property
    def unrealized_pnl(self) -> float:
        mark = self.entry_price if self.mark_price is None else self.mark_price
        points = mark - self.entry_price
        if self.side == "SHORT":
            points = -points
        return points * self.quantity - self.entry_cost


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
    consecutive_losses: int = 0
    last_loss_at: datetime | None = None

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
        # Never roll an open position into a new session as if it were flat.
        if self.positions:
            self.halted = True
            return False
        self.session_date = local.date()
        self.entries_today = 0
        self.realized_pnl = 0.0
        self.halted = False
        self.consecutive_losses = 0
        self.last_loss_at = None
        return True

    @property
    def open_planned_risk(self) -> float:
        return sum(position.planned_risk for position in self.positions.values())

    @property
    def capital_exposure(self) -> float:
        return sum(position.notional for position in self.positions.values())

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
        overnight_position = any(
            self._ist(position.opened_at).date() != local.date()
            for position in self.positions.values()
        )
        if self.positions and (
            overnight_position
            or local.time() >= self.limits.square_off_time
            or self.day_pnl <= -self.limits.max_daily_loss
        ):
            if overnight_position or self.day_pnl <= -self.limits.max_daily_loss:
                self.halted = True
            return True
        return False

    def evaluate_entry(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_price: float,
        now: datetime,
        target_price: float | None = None,
        estimated_round_trip_cost: float = 0.0,
    ) -> EntryDecision:
        local = self._ist(now)
        if not self._prepare_session(local):
            return EntryDecision(False, "Overnight position detected; new session blocked")
        if self.consecutive_losses >= self.limits.max_consecutive_losses:
            self.halted = True
            return EntryDecision(False, "Consecutive-loss limit reached; trading halted for the day")
        if self.halted:
            return EntryDecision(False, "Daily risk halt is active")
        if local.time() < self.limits.entry_start:
            return EntryDecision(False, "Before intraday entry window")
        if local.time() >= self.limits.entry_cutoff:
            return EntryDecision(False, "Entry cutoff reached")
        if local.time() >= self.limits.square_off_time:
            return EntryDecision(False, "Square-off time reached")
        if self.day_pnl <= -self.limits.max_daily_loss:
            self.halted = True
            return EntryDecision(False, "Daily loss limit reached; flatten open positions")
        if self.last_loss_at is not None:
            elapsed = local - self._ist(self.last_loss_at)
            cooldown = timedelta(minutes=self.limits.cooldown_minutes_after_loss)
            if elapsed < cooldown:
                remaining = int((cooldown - elapsed).total_seconds() // 60) + 1
                return EntryDecision(False, f"Loss cooldown active; wait approximately {remaining} minute(s)")
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
        numeric_values = [entry_price, stop_price, estimated_round_trip_cost]
        if target_price is not None:
            numeric_values.append(target_price)
        if quantity < 1 or not all(isfinite(value) for value in numeric_values):
            return EntryDecision(False, "Invalid quantity, price, or estimated cost")
        if entry_price <= 0 or stop_price <= 0 or estimated_round_trip_cost < 0:
            return EntryDecision(False, "Prices must be positive and costs non-negative")

        per_share = entry_price - stop_price if normalized_side == "LONG" else stop_price - entry_price
        if per_share <= 0:
            return EntryDecision(False, "Stop must be below LONG entry or above SHORT entry")
        stop_distance_pct = per_share / entry_price * 100.0
        if stop_distance_pct > self.limits.max_stop_distance_pct:
            return EntryDecision(False, "Stop distance exceeds the maximum allowed percentage")

        planned_risk = per_share * quantity + estimated_round_trip_cost
        if planned_risk > self.limits.per_trade_risk:
            return EntryDecision(False, "Per-trade risk budget exceeded", planned_risk)
        if self.open_planned_risk + planned_risk > self.limits.max_open_risk:
            return EntryDecision(False, "Aggregate open-risk limit exceeded", planned_risk)
        if self.capital_exposure + quantity * entry_price > self.limits.max_capital_exposure:
            return EntryDecision(False, "Maximum capital exposure exceeded", planned_risk)

        if target_price is not None:
            reward_per_share = (
                target_price - entry_price
                if normalized_side == "LONG"
                else entry_price - target_price
            )
            if reward_per_share <= 0:
                return EntryDecision(False, "Target is on the wrong side of the entry", planned_risk)
            net_reward = reward_per_share * quantity - estimated_round_trip_cost
            if net_reward <= 0 or net_reward / planned_risk < self.limits.min_reward_risk:
                return EntryDecision(False, "Net reward-to-risk is below the required minimum", planned_risk)
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
        target_price: float | None = None,
        estimated_round_trip_cost: float = 0.0,
    ) -> EntryDecision:
        decision = self.evaluate_entry(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            now=now,
            target_price=target_price,
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
            entry_cost=estimated_round_trip_cost,
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
        self.positions[key] = Position(**{**position.__dict__, "mark_price": price})
        if self.day_pnl <= -self.limits.max_daily_loss:
            self.halted = True

    def register_exit(
        self,
        symbol: str,
        *,
        exit_price: float,
        now: datetime,
        additional_cost: float = 0.0,
    ) -> float:
        local = self._ist(now)
        key = symbol.strip().upper()
        position = self.positions.get(key)
        if position is None:
            raise KeyError(f"No open position for {key}")
        if (
            not isfinite(exit_price)
            or exit_price <= 0
            or not isfinite(additional_cost)
            or additional_cost < 0
        ):
            raise ValueError("exit price must be positive and costs finite and non-negative")
        self.positions.pop(key)
        points = exit_price - position.entry_price
        if position.side == "SHORT":
            points = -points
        pnl = points * position.quantity - position.entry_cost - additional_cost
        self.realized_pnl += pnl
        if pnl < 0:
            self.consecutive_losses += 1
            self.last_loss_at = local
        else:
            self.consecutive_losses = 0
            self.last_loss_at = None
        if self.day_pnl <= -self.limits.max_daily_loss:
            self.halted = True
        if self.consecutive_losses >= self.limits.max_consecutive_losses:
            self.halted = True
        return pnl
