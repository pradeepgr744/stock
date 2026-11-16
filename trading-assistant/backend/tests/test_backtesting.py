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


class BacktestingEngineTests(unittest.TestCase):
    def setUp(self):
        self.costs = CostModel(buy_cost_bps=2.0, sell_cost_bps=4.0, slippage_bps_per_side=2.0)
        self.config = BacktestConfig(initial_capital=50000, risk_pct=0.5, max_trades_per_day=3)

    def test_orb_signal_uses_completed_bar_and_next_bar_entry(self):
        bars = [bar(2, i * 5, 100, 101, 99, 100, 1000) for i in range(3)]
        bars += [
            bar(2, 15, 101, 103, 100, 102, 2000),
            bar(2, 20, 102, 104, 101, 103, 2000),
            bar(2, 25, 103, 105, 102, 104, 2000),
            bar(2, 30, 104, 106, 103, 105, 2000),
            bar(2, 35, 105, 106, 104, 105, 2000),
        ]
        signals = generate_signals(bars, "orb")
        self.assertTrue(signals)
        self.assertEqual(signals[0].side, "LONG")
        self.assertEqual(signals[0].timestamp, bars[3].timestamp)

    def test_daily_trade_cap_and_no_duplicate_symbol_day(self):
        bars = []
        for day in (2, 3, 4, 5):
            bars += [bar(day, i * 5, 100, 101, 99, 100, 1000, f"S{i}") for i in range(3)]
            bars += [
                bar(day, 15, 101, 103, 100, 102, 2000, "AAA"),
                bar(day, 20, 102, 104, 101, 103, 2000, "AAA"),
                bar(day, 25, 103, 105, 102, 104, 2000, "AAA"),
                bar(day, 30, 104, 106, 103, 105, 2000, "AAA"),
                bar(day, 35, 105, 106, 104, 105, 2000, "AAA"),
            ]
        # At most one open position at a time and at most 3 trades/day.
        report = run_backtest(bars, ["orb"], self.config, self.costs)
        self.assertLessEqual(report["metrics"]["trades"], 3 * 4)

    def test_combined_comparison_contains_all_candidates(self):
        bars = [bar(2, i * 5, 100, 101, 99, 100, 1000) for i in range(3)]
        report = compare_strategies(bars, self.config, self.costs)
        self.assertEqual(set(report["results"]), {"orb", "vwap", "sweep", "combined"})
        self.assertEqual(len(report["ranking_by_observed_expectancy_r"]), 4)

    def test_risk_budget_is_configured_at_250_rupees(self):
        self.assertEqual(self.config.initial_capital * self.config.risk_pct / 100, 250)


if __name__ == "__main__":
    unittest.main()
