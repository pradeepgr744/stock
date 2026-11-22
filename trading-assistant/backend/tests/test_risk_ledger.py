import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.modules.risk.ledger import build_risk_manager
from app.modules.risk.models import ManualTrade

IST = ZoneInfo("Asia/Kolkata")


class RiskLedgerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.now = datetime(2026, 11, 17, 10, 30, tzinfo=IST)

    def tearDown(self):
        self.engine.dispose()

    def test_rehydrates_open_positions_and_daily_entry_count(self):
        with self.Session() as session:
            session.add(
                ManualTrade(
                    trade_id="trade-1",
                    trade_date=date(2026, 11, 17),
                    symbol="RELIANCE",
                    side="LONG",
                    quantity=100,
                    entry_price=100,
                    stop_price=99,
                    target_price=102.5,
                    entry_cost=15,
                    mark_price=99.5,
                    exit_cost=0,
                    status="OPEN",
                    opened_at=self.now,
                    note="manual fill",
                )
            )
            session.commit()

            manager = build_risk_manager(session, self.now)

        self.assertEqual(manager.entries_today, 1)
        self.assertEqual(len(manager.positions), 1)
        self.assertEqual(manager.open_planned_risk, 115)
        self.assertEqual(manager.capital_exposure, 10_000)
        self.assertEqual(manager.day_pnl, -65)

        decision = manager.evaluate_entry(
            symbol="TCS",
            side="LONG",
            quantity=100,
            entry_price=100,
            stop_price=99,
            target_price=102.5,
            now=self.now,
            estimated_round_trip_cost=15,
        )
        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
