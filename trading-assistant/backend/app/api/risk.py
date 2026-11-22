from __future__ import annotations

from datetime import datetime
from secrets import compare_digest
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, text
from sqlalchemy.orm import Session

from ..config import settings
from ..database import Base, SessionLocal, engine
from ..modules.risk.intraday import IntradayRiskManager, Position, RiskLimits
from ..modules.risk.models import ManualTrade

def require_risk_api_key(x_risk_api_key: str | None = Header(default=None)) -> None:
    expected = settings.risk_api_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Manual risk journal is locked until RISK_API_KEY is configured.",
        )
    if not x_risk_api_key or not compare_digest(x_risk_api_key, expected):
        raise HTTPException(status_code=401, detail="A valid X-Risk-API-Key header is required.")


router = APIRouter(
    prefix="/api/risk",
    tags=["manual-risk"],
    dependencies=[Depends(require_risk_api_key)],
)
IST = ZoneInfo("Asia/Kolkata")
_RISK_LEDGER_LOCK_KEY = 724_202_612
_ESTIMATED_ROUND_TRIP_COST_RATE = 0.0015


class ManualTradeEntry(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["LONG", "SHORT"]
    quantity: int = Field(ge=1)
    entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    estimated_round_trip_cost: float | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=1000)


class ManualTradeMark(BaseModel):
    mark_price: float = Field(gt=0)


class ManualTradeExit(BaseModel):
    exit_price: float = Field(gt=0)
    exit_cost: float = Field(default=0, ge=0)


def _ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


def _manager_from_session(session: Session, now: datetime) -> IntradayRiskManager:
    local = now.astimezone(IST)
    manager = IntradayRiskManager(limits=RiskLimits())
    manager.session_date = local.date()

    today_rows = (
        session.query(ManualTrade)
        .filter(ManualTrade.trade_date == local.date())
        .all()
    )
    manager.entries_today = len(today_rows)
    manager.realized_pnl = sum(
        row.realized_pnl or 0.0
        for row in today_rows
        if row.status == "CLOSED"
    )

    open_rows = (
        session.query(ManualTrade)
        .filter(ManualTrade.status == "OPEN")
        .all()
    )
    for row in open_rows:
        if row.trade_date != local.date():
            manager.halted = True
        opened_at = row.opened_at
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=IST)
        planned_risk = abs(row.entry_price - row.stop_price) * row.quantity + row.entry_cost
        manager.positions[row.symbol] = Position(
            symbol=row.symbol,
            side=row.side,
            quantity=row.quantity,
            entry_price=row.entry_price,
            stop_price=row.stop_price,
            planned_risk=planned_risk,
            opened_at=opened_at.astimezone(IST),
            mark_price=row.mark_price,
            entry_cost=row.entry_cost,
        )

    closed_today = sorted(
        [row for row in today_rows if row.status == "CLOSED" and row.realized_pnl is not None],
        key=lambda row: row.closed_at or row.opened_at,
        reverse=True,
    )
    for row in closed_today:
        if row.realized_pnl is not None and row.realized_pnl < 0:
            manager.consecutive_losses += 1
            if manager.last_loss_at is None:
                closed_at = row.closed_at or row.opened_at
                manager.last_loss_at = (
                    closed_at.replace(tzinfo=IST)
                    if closed_at.tzinfo is None
                    else closed_at.astimezone(IST)
                )
        else:
            break

    if manager.day_pnl <= -manager.limits.max_daily_loss:
        manager.halted = True
    if manager.consecutive_losses >= manager.limits.max_consecutive_losses:
        manager.halted = True
    return manager


def _trade_payload(row: ManualTrade) -> dict:
    return {
        "trade_id": row.trade_id,
        "trade_date": row.trade_date.isoformat(),
        "symbol": row.symbol,
        "side": row.side,
        "quantity": row.quantity,
        "entry_price": row.entry_price,
        "stop_price": row.stop_price,
        "target_price": row.target_price,
        "entry_cost": row.entry_cost,
        "mark_price": row.mark_price,
        "exit_price": row.exit_price,
        "exit_cost": row.exit_cost,
        "realized_pnl": row.realized_pnl,
        "status": row.status,
        "opened_at": row.opened_at.isoformat(),
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "note": row.note,
    }


@router.get("/status")
def risk_status() -> dict:
    _ensure_schema()
    now = datetime.now(IST)
    with SessionLocal() as session:
        manager = _manager_from_session(session, now)
        must_flatten = manager.must_flatten(now)
        cooldown_active = False
        if manager.last_loss_at is not None:
            elapsed = now - manager.last_loss_at
            cooldown_active = elapsed.total_seconds() < manager.limits.cooldown_minutes_after_loss * 60
        return {
            "as_of": now.isoformat(),
            "capital": manager.limits.capital,
            "per_trade_risk": manager.limits.per_trade_risk,
            "max_open_risk": manager.limits.max_open_risk,
            "open_planned_risk": round(manager.open_planned_risk, 2),
            "max_open_positions": manager.limits.max_open_positions,
            "open_positions": len(manager.positions),
            "max_trades_per_day": manager.limits.max_trades_per_day,
            "entries_today": manager.entries_today,
            "max_daily_loss": manager.limits.max_daily_loss,
            "realized_pnl": round(manager.realized_pnl, 2),
            "unrealized_pnl": round(manager.unrealized_pnl, 2),
            "day_pnl": round(manager.day_pnl, 2),
            "capital_exposure": round(manager.capital_exposure, 2),
            "max_capital_exposure": manager.limits.max_capital_exposure,
            "consecutive_losses": manager.consecutive_losses,
            "cooldown_active": cooldown_active,
            "halted": manager.halted,
            "square_off_time": manager.limits.square_off_time.isoformat(),
            "square_off_required": must_flatten,
            "manual_execution_only": True,
            "limitations": [
                "Only positions and fills recorded in this manual journal are included.",
                "This API never places, modifies or cancels broker orders.",
                "Planned risk is not a guaranteed maximum loss; gaps, slippage and execution failures can exceed it.",
            ],
        }


@router.get("/manual-trades")
def list_manual_trades(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    _ensure_schema()
    with SessionLocal() as session:
        rows = (
            session.query(ManualTrade)
            .order_by(desc(ManualTrade.opened_at))
            .limit(limit)
            .all()
        )
        return [_trade_payload(row) for row in rows]


@router.post("/manual-trades", status_code=201)
def record_manual_entry(request: ManualTradeEntry) -> dict:
    _ensure_schema()
    now = datetime.now(IST)
    with SessionLocal() as session:
        try:
            if engine.dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": _RISK_LEDGER_LOCK_KEY},
                )
            manager = _manager_from_session(session, now)
            estimated_cost = (
                request.estimated_round_trip_cost
                if request.estimated_round_trip_cost is not None
                else round(request.quantity * request.entry_price * _ESTIMATED_ROUND_TRIP_COST_RATE, 2)
            )
            decision = manager.evaluate_entry(
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                entry_price=request.entry_price,
                stop_price=request.stop_price,
                target_price=request.target_price,
                now=now,
                estimated_round_trip_cost=estimated_cost,
            )
            if not decision.allowed:
                raise HTTPException(status_code=409, detail=decision.reason)

            row = ManualTrade(
                trade_id=str(uuid4()),
                trade_date=now.date(),
                symbol=request.symbol.strip().upper(),
                side=request.side,
                quantity=request.quantity,
                entry_price=request.entry_price,
                stop_price=request.stop_price,
                target_price=request.target_price,
                entry_cost=estimated_cost,
                status="OPEN",
                opened_at=now,
                note=request.note,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return {
                "trade": _trade_payload(row),
                "risk_decision": {
                    "allowed": True,
                    "reason": decision.reason,
                    "planned_risk": round(decision.planned_risk, 2),
                    "estimated_round_trip_cost": estimated_cost,
                },
                "manual_execution_only": True,
            }
        except HTTPException:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=503,
                detail=f"Could not save manual trade: {type(exc).__name__}",
            ) from exc


@router.post("/manual-trades/{trade_id}/mark")
def update_manual_mark(trade_id: str, request: ManualTradeMark) -> dict:
    _ensure_schema()
    with SessionLocal() as session:
        row = session.get(ManualTrade, trade_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Manual trade was not found.")
        if row.status != "OPEN":
            raise HTTPException(status_code=409, detail="Only open trades can be marked.")
        row.mark_price = request.mark_price
        session.commit()
        return {"trade": _trade_payload(row), "risk": risk_status()}


@router.post("/manual-trades/{trade_id}/exit")
def close_manual_trade(trade_id: str, request: ManualTradeExit) -> dict:
    _ensure_schema()
    now = datetime.now(IST)
    with SessionLocal() as session:
        try:
            if engine.dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": _RISK_LEDGER_LOCK_KEY},
                )
            row = session.get(ManualTrade, trade_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Manual trade was not found.")
            if row.status != "OPEN":
                raise HTTPException(status_code=409, detail="Manual trade is already closed.")
            points = request.exit_price - row.entry_price
            if row.side == "SHORT":
                points = -points
            # exit_cost is the actual round-trip cost when supplied; otherwise
            # retain the conservative estimate recorded at entry.
            total_cost = request.exit_cost if request.exit_cost > 0 else row.entry_cost
            realized_pnl = points * row.quantity - total_cost
            row.exit_price = request.exit_price
            row.exit_cost = total_cost
            row.realized_pnl = round(realized_pnl, 2)
            row.status = "CLOSED"
            row.closed_at = now
            session.commit()
            session.refresh(row)
            return {"trade": _trade_payload(row), "risk": risk_status()}
        except HTTPException:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=503,
                detail=f"Could not close manual trade: {type(exc).__name__}",
            ) from exc
