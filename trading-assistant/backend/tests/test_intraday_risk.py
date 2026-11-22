import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.modules.risk.intraday import IntradayRiskManager, RiskLimits

IST = ZoneInfo("Asia/Kolkata")


def at(hour, minute, day=17):
    return datetime(2026, 11, day, hour, minute, tzinfo=IST)


class IntradayRiskManagerTests(unittest.TestCase):
    def setUp(self):
        self.risk = IntradayRiskManager()

    def open_position(self, symbol="AAA", now=None):
        return self.risk.register_entry(
            symbol=symbol, side="LONG", quantity=250,
            entry_price=100, stop_price=99,
            now=now or at(10, 0),
        )

    def test_default_limits_match_intraday_plan(self):
        limits = RiskLimits()
        self.assertEqual(limits.per_trade_risk, 250)
        self.assertEqual(limits.max_open_risk, 500)
        self.assertEqual(limits.max_daily_loss, 750)
        self.assertEqual(limits.max_open_positions, 2)
        self.assertEqual(limits.max_trades_per_day, 3)
        self.assertEqual(limits.square_off_time.isoformat(), "15:15:00")

    def test_allows_two_positions_but_blocks_third_concurrent(self):
        self.assertTrue(self.open_position("AAA").allowed)
        self.assertTrue(self.open_position("BBB").allowed)
        result = self.open_position("CCC")
        self.assertFalse(result.allowed)
        self.assertIn("concurrent", result.reason)

    def test_aggregate_risk_cap_is_enforced(self):
        limits = RiskLimits(max_open_risk=300)
        manager = IntradayRiskManager(limits)
        self.assertTrue(manager.register_entry(
            symbol="AAA", side="LONG", quantity=200, entry_price=100,
            stop_price=99, now=at(10, 0),
        ).allowed)
        result = manager.evaluate_entry(
            symbol="BBB", side="LONG", quantity=110, entry_price=100,
            stop_price=99, now=at(10, 5),
        )
        self.assertFalse(result.allowed)
        self.assertIn("Aggregate", result.reason)

    def test_entry_cutoff_is_enforced(self):
        result = self.open_position(now=at(14, 46))
        self.assertFalse(result.allowed)

    def test_square_off_deadline_requires_flattening(self):
        self.open_position()
        self.assertTrue(self.risk.must_flatten(at(15, 15)))

    def test_daily_loss_uses_realized_plus_unrealized(self):
        self.open_position()
        self.risk.update_mark("AAA", 96.9)
        self.assertLessEqual(self.risk.day_pnl, -750)
        self.assertTrue(self.risk.must_flatten(at(11, 0)))
        self.assertFalse(self.open_position("BBB", at(11, 5)).allowed)

    def test_trade_count_resets_on_next_flat_session(self):
        for symbol in ("AAA", "BBB", "CCC"):
            self.assertTrue(self.open_position(symbol).allowed)
            self.risk.register_exit(symbol, exit_price=100, now=at(10, 30))
        self.assertFalse(self.open_position("DDD").allowed)
        self.assertTrue(self.open_position("EEE", at(10, 0, day=18)).allowed)


    def test_rejects_stop_wider_than_maximum_percentage(self):
        result = self.risk.evaluate_entry(
            symbol="AAA", side="LONG", quantity=10, entry_price=100,
            stop_price=98.7, now=at(10, 0),
        )
        self.assertFalse(result.allowed)
        self.assertIn("Stop distance", result.reason)

    def test_rejects_reward_risk_below_two_to_one_after_costs(self):
        result = self.risk.evaluate_entry(
            symbol="AAA", side="LONG", quantity=100, entry_price=100,
            stop_price=99, target_price=101, now=at(10, 0),
            estimated_round_trip_cost=10,
        )
        self.assertFalse(result.allowed)
        self.assertIn("reward-to-risk", result.reason)

    def test_enforces_capital_exposure(self):
        limits = RiskLimits(max_capital_exposure=30_000)
        manager = IntradayRiskManager(limits)
        self.assertTrue(manager.register_entry(
            symbol="AAA", side="LONG", quantity=200, entry_price=100,
            stop_price=99, now=at(10, 0),
        ).allowed)
        result = manager.evaluate_entry(
            symbol="BBB", side="LONG", quantity=110, entry_price=100,
            stop_price=99, now=at(10, 5),
        )
        self.assertFalse(result.allowed)
        self.assertIn("capital exposure", result.reason)

    def test_loss_starts_cooldown_and_two_losses_halt(self):
        self.assertTrue(self.risk.register_entry(
            symbol="AAA", side="LONG", quantity=100, entry_price=100,
            stop_price=99, target_price=102, now=at(10, 0),
        ).allowed)
        self.risk.register_exit("AAA", exit_price=99, now=at(10, 5))
        cooldown = self.risk.evaluate_entry(
            symbol="BBB", side="LONG", quantity=100, entry_price=100,
            stop_price=99, target_price=102, now=at(10, 10),
        )
        self.assertFalse(cooldown.allowed)
        self.assertIn("cooldown", cooldown.reason)

        self.assertTrue(self.risk.register_entry(
            symbol="BBB", side="LONG", quantity=100, entry_price=100,
            stop_price=99, target_price=102, now=at(10, 21),
        ).allowed)
        self.risk.register_exit("BBB", exit_price=99, now=at(10, 25))
        halted = self.risk.evaluate_entry(
            symbol="CCC", side="LONG", quantity=100, entry_price=100,
            stop_price=99, target_price=102, now=at(10, 45),
        )
        self.assertFalse(halted.allowed)
        self.assertIn("Consecutive-loss", halted.reason)

    def test_timezone_naive_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            self.open_position(now=datetime(2026, 11, 17, 10, 0))


if __name__ == "__main__":
    unittest.main()
