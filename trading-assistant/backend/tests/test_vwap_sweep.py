import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.modules.strategy.vwap_sweep import VwapSweepReclaim

IST = ZoneInfo("Asia/Kolkata")


def feed(bars):
    return {
        "marketOHLC": {
            "ohlc": [
                {
                    "interval": "I1",
                    "ts": str(int(ts.timestamp() * 1000)),
                    "open": op,
                    "high": hi,
                    "low": lo,
                    "close": close,
                    "vol": str(volume),
                }
                for ts, op, hi, lo, close, volume in bars
            ]
        }
    }


class VwapSweepTests(unittest.TestCase):
    def test_detects_completed_bar_sweep_and_reclaim_above_vwap(self):
        scanner = VwapSweepReclaim()
        start = datetime(2026, 11, 16, 9, 30, tzinfo=IST)
        bars = []
        for index in range(25):
            ts = start + timedelta(minutes=index)
            bars.append((ts, 100.0, 100.25, 99.75, 100.0, 100))
        # A high-volume candle sweeps the prior 20-minute low, reclaims it,
        # closes above VWAP, and has a strong bullish body.
        ts = start + timedelta(minutes=25)
        bars.append((ts, 99.8, 100.45, 99.70, 100.3, 250))
        # This next bar closes the sweep candle; it is not itself a signal bar.
        ts = start + timedelta(minutes=26)
        bars.append((ts, 100.3, 100.4, 100.1, 100.2, 100))

        signals = scanner.on_feed("TEST", feed(bars))
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].strategy, "LIQUIDITY_SWEEP_RECLAIM")
        self.assertEqual(signals[0].direction, "BUY")
        self.assertGreaterEqual(signals[0].quality_score, 75)
        self.assertGreater(signals[0].target, signals[0].entry)
        self.assertLess(signals[0].stop_loss, signals[0].entry)

    def test_does_not_emit_without_relative_volume(self):
        scanner = VwapSweepReclaim()
        start = datetime(2026, 11, 16, 9, 30, tzinfo=IST)
        bars = []
        for index in range(25):
            ts = start + timedelta(minutes=index)
            bars.append((ts, 100.0, 100.25, 99.75, 100.0, 100))
        ts = start + timedelta(minutes=25)
        bars.append((ts, 99.8, 100.45, 99.70, 100.3, 100))
        ts = start + timedelta(minutes=26)
        bars.append((ts, 100.3, 100.4, 100.1, 100.2, 100))
        self.assertEqual(scanner.on_feed("TEST", feed(bars)), [])

    def test_does_not_emit_after_entry_cutoff(self):
        scanner = VwapSweepReclaim()
        start = datetime(2026, 11, 16, 14, 20, tzinfo=IST)
        bars = []
        for index in range(25):
            ts = start + timedelta(minutes=index)
            bars.append((ts, 100.0, 100.25, 99.75, 100.0, 100))
        ts = start + timedelta(minutes=25)
        bars.append((ts, 99.8, 100.45, 99.70, 100.3, 250))
        ts = start + timedelta(minutes=26)
        bars.append((ts, 100.3, 100.4, 100.1, 100.2, 100))
        self.assertEqual(scanner.on_feed("TEST", feed(bars)), [])


if __name__ == "__main__":
    unittest.main()
