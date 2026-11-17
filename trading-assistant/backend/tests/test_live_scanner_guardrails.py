import unittest
from unittest.mock import patch

from app.config import settings
from app.modules.news.worker import NewsWorker
from app.modules.strategy.orb_scanner import OrbScanner


class NewsWorkerGuardTests(unittest.TestCase):
    def test_unconfigured_feeds_fail_closed(self):
        worker = NewsWorker()
        with patch.object(settings, "news_rss_feed_urls", ""):
            result = worker.run_once()
        self.assertEqual(result["state"], "UNCONFIGURED")
        self.assertTrue(result["halt_new_entries"])
        self.assertTrue(result["halt_all_symbols"])


class LiveScannerGuardTests(unittest.TestCase):
    def test_quantity_uses_per_trade_risk_and_capital_limit(self):
        scanner = OrbScanner()
        quantity, planned_risk = scanner._size_candidate(100.0, 99.0)
        self.assertEqual(quantity, 250)
        self.assertEqual(planned_risk, 250.0)

    def test_daily_signal_cap_and_symbol_direction_deduplication(self):
        scanner = OrbScanner()
        self.assertTrue(scanner._reserve_daily_signal("AAA", "BUY"))
        self.assertFalse(scanner._reserve_daily_signal("AAA", "BUY"))
        self.assertTrue(scanner._reserve_daily_signal("BBB", "SELL"))
        self.assertTrue(scanner._reserve_daily_signal("CCC", "BUY"))
        self.assertFalse(scanner._reserve_daily_signal("DDD", "BUY"))


if __name__ == "__main__":
    unittest.main()
