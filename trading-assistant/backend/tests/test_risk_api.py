import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import risk as risk_api

IST = ZoneInfo("Asia/Kolkata")


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 23, 10, 30, tzinfo=tz or IST)


class ManualRiskApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.engine_patch = patch.object(risk_api, "engine", self.engine)
        self.session_patch = patch.object(risk_api, "SessionLocal", self.session_factory)
        self.datetime_patch = patch.object(risk_api, "datetime", FrozenDateTime)
        self.engine_patch.start()
        self.session_patch.start()
        self.datetime_patch.start()

    def tearDown(self):
        self.datetime_patch.stop()
        self.session_patch.stop()
        self.engine_patch.stop()
        self.engine.dispose()

    def test_manual_trade_lifecycle_persists_and_uses_actual_round_trip_cost(self):
        entry = risk_api.ManualTradeEntry(
            symbol="RELIANCE",
            side="LONG",
            quantity=100,
            entry_price=100,
            stop_price=99,
            target_price=102.5,
        )
        created = risk_api.record_manual_entry(entry)
        trade_id = created["trade"]["trade_id"]
        self.assertTrue(created["risk_decision"]["allowed"])
        self.assertEqual(created["risk_decision"]["planned_risk"], 115.0)

        marked = risk_api.update_manual_mark(
            trade_id,
            risk_api.ManualTradeMark(mark_price=101),
        )
        self.assertEqual(marked["trade"]["mark_price"], 101)

        closed = risk_api.close_manual_trade(
            trade_id,
            risk_api.ManualTradeExit(exit_price=102, exit_cost=20),
        )
        self.assertEqual(closed["trade"]["status"], "CLOSED")
        self.assertEqual(closed["trade"]["realized_pnl"], 180.0)
        self.assertEqual(closed["risk"]["realized_pnl"], 180.0)


if __name__ == "__main__":
    unittest.main()
