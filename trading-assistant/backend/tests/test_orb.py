import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.modules.strategy.orb import OpeningRangeBreakout


MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")


def feed_for(timestamp: datetime, open_price: float, high: float, low: float, close: float, volume: int) -> dict:
    return {
        "marketOHLC": {
            "ohlc": [
                {
                    "interval": "I1",
                    "ts": str(int(timestamp.timestamp() * 1000)),
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "vol": str(volume),
                }
            ]
        }
    }


class OpeningRangeBreakoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scanner = OpeningRangeBreakout()
        self.start = datetime(2026, 11, 16, 9, 15, tzinfo=MARKET_TIMEZONE)

    def send_bar(
        self,
        minute: int,
        *,
        open_price: float = 100,
        high: float = 101,
        low: float = 99,
        close: float = 100,
        volume: int = 100,
    ) -> list:
        timestamp = self.start + timedelta(minutes=minute)
        return self.scanner.on_feed(
            "TEST",
            feed_for(timestamp, open_price, high, low, close, volume),
        )

    def build_opening_range(self) -> None:
        for minute in range(15):
            self.send_bar(minute)
        self.send_bar(15, open_price=100, high=102, low=98, close=101)

    def test_sends_candidate_after_completed_breakout_bar_and_only_once(self) -> None:
        self.build_opening_range()
        for minute in range(15, 20):
            self.send_bar(
                minute,
                open_price=102,
                high=104,
                low=101,
                close=103,
                volume=100,
            )

        candidates = self.send_bar(20, open_price=103, high=104, low=102, close=103)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].direction, "BUY")
        self.assertEqual(candidates[0].entry, 103)
        self.assertEqual(candidates[0].stop_loss, 99)
        self.assertEqual(candidates[0].target, 111)
        self.assertEqual(self.send_bar(25), [])

    def test_rejects_breakout_without_volume_confirmation(self) -> None:
        self.build_opening_range()
        for minute in range(15, 20):
            self.send_bar(
                minute,
                open_price=102,
                high=104,
                low=101,
                close=103,
                volume=10,
            )

        self.assertEqual(self.send_bar(20), [])

    def test_rejects_incomplete_opening_range(self) -> None:
        for minute in range(14):
            self.send_bar(minute)
        self.send_bar(15)
        for minute in range(15, 20):
            self.send_bar(minute, open_price=102, high=104, low=101, close=103)

        self.assertEqual(self.send_bar(20), [])

    def test_ignores_non_minute_and_invalid_candles(self) -> None:
        invalid_feed = {
            "marketOHLC": {
                "ohlc": [
                    {"interval": "I5", "ts": "1794810000000", "open": 100},
                    {
                        "interval": "I1",
                        "ts": "1794810000000",
                        "open": 100,
                        "high": 99,
                        "low": 101,
                        "close": 100,
                        "vol": "50",
                    },
                ]
            }
        }
        self.assertEqual(self.scanner.on_feed("TEST", invalid_feed), [])


if __name__ == "__main__":
    unittest.main()
