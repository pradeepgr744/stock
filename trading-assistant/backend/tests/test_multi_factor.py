import unittest

from app.modules.strategy.multi_factor import Candle, Regime, evaluate_setups


class MultiFactorValidationTests(unittest.TestCase):
    def candles(self):
        # Stable, valid completed bars. This fixture is for input validation,
        # not a claim that the strategy has a profitable historical edge.
        return [
            Candle(
                timestamp=f"2026-11-16T{9 + i // 12:02d}:{(15 + i * 5) % 60:02d}:00+05:30",
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1000.0,
            )
            for i in range(20)
        ]

    def evaluate(self, candles=None, **overrides):
        args = {
            "symbol": "TEST",
            "candles": self.candles() if candles is None else candles,
            "regime": Regime.RANGE,
            "sector_direction": "NEUTRAL",
            "time_adjusted_rvol": 1.0,
            "liquid": True,
        }
        args.update(overrides)
        return evaluate_setups(**args)

    def test_insufficient_history_is_rejected(self):
        self.assertEqual(self.evaluate(candles=self.candles()[:10]), [])

    def test_invalid_ohlc_is_rejected(self):
        bars = self.candles()
        bars[-1] = Candle("now", 100, 98, 99, 100, 1000)
        self.assertEqual(self.evaluate(candles=bars), [])

    def test_non_finite_rvol_is_rejected(self):
        self.assertEqual(self.evaluate(time_adjusted_rvol=float("nan")), [])

    def test_illiquid_symbol_is_rejected(self):
        self.assertEqual(self.evaluate(liquid=False, time_adjusted_rvol=3.0), [])

    def test_range_market_does_not_invent_trend_trade(self):
        self.assertEqual(self.evaluate(time_adjusted_rvol=3.0), [])


if __name__ == "__main__":
    unittest.main()
