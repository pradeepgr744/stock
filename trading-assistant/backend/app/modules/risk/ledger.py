from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .intraday import IntradayRiskManager, Position, RiskLimits
from .models import ManualTrade

IST = ZoneInfo("Asia/Kolkata")


def build_risk_manager(session: Session, now: datetime) -> IntradayRiskManager:
    """Rehydrate the risk engine from operator-recorded fills in PostgreSQL."""
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
        [
            row for row in today_rows
            if row.status == "CLOSED" and row.realized_pnl is not None
        ],
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
