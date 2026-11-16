import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.modules.backtesting.engine import (
    BacktestConfig, Bar, CostModel, compare_strategies, generate_signals, run_backtest,
)

IST = ZoneInfo("Asia/Kolkata")


def bar(day, minute, o, h, l, c, volume=1000, symbol="TEST"):
    stamp = datetime(2026, 1, day, 9, 15, tzinfo=IST) + timedelta(minutes=minute)
    return Bar(stamp, symbol, o, h, l, c, volume)


def make_orb_session(day, symbol, signal_minute=15):
    values = [bar(day, minute, 1000, 1001, 999, 1000, 1000, symbol) for minute in (0, 5, 10)]
    for minute in range(15, signal_minute, 5):
        values.append(bar(day, minute, 1000, 1001, 999, 1000, 1000, symbol))
    values.append(bar(day, signal_minute, 1001, 1003, 1000, 1002, 2000, symbol))
    values.append(bar(day, signal_minute + 5, 1002, 1010, 1001, 1009, 2500, symbol))
    values.append(bar(day, signal_minute + 10, 1009, 1010, 1008, 1009, 1000, symbol))
    return values


class BacktestingEngineTests(unittest.TestCase):
    def setUp(self):
        self.costs = CostModel(buy_cost_bps=2.0, sell_cost_bps=4.0, slippage_bps_per_side=2.0)
        self.config = BacktestConfig(initial_capital=50000, risk_pct=0.5, max_trades_per_day=3)

    def test_orb_signal_uses_completed_bar(self):
        bars = make_orb_session(2, "TEST")
        signals = generate_signals(bars, "orb")
        self.assertTrue(signals)
        self.assertEqual(signals[0].side, "LONG")
        self.assertEqual(signals[0].timestamp, bars[3].timestamp)

    def test_entry_uses_next_bar_open_and_target_exit(self):
        bars = make_orb_session(2, "TEST")
        report = run_backtest(bars, ["orb"], self.config, self.costs)
        self.assertEqual(report["metrics"]["trades"], 1)
        trade = report["trades"][0]
        self.assertEqual(trade["entry"], 1002)
        self.assertEqual(trade["exit_reason"], "TARGET")
        self.assertGreater(trade["costs"], 0)
        self.assertLessEqual(trade["initial_risk"], 250)

    def test_daily_trade_cap_is_enforced(self):
        bars = []
        for symbol, signal_minute in (("AAA", 15), ("BBB", 25), ("CCC", 35), ("DDD", 45)):
            bars.extend(make_orb_session(2, symbol, signal_minute))
        report = run_backtest(bars, ["orb"], self.config, self.costs)
        self.assertEqual(report["metrics"]["trades"], 3)

    def test_combined_comparison_contains_all_candidates(self):
        bars = make_orb_session(2, "TEST")
        report = compare_strategies(bars, self.config, self.costs)
        self.assertEqual(set(report["results"]), {"orb", "vwap", "sweep", "combined"})
        self.assertEqual(len(report["ranking_by_observed_expectancy_r"]), 4)

    def test_risk_budget_is_configured_at_250_rupees(self):
        self.assertEqual(self.config.initial_capital * self.config.risk_pct / 100, 250)


if __name__ == "__main__":
    unittest.main()
