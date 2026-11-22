import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

from app.config import settings
from app.modules.news.worker import NewsWorker
from app.modules.strategy.orb_scanner import OrbScanner, news_gate_blocks_candidate


class NewsWorkerGuardTests(unittest.TestCase):
    def test_unconfigured_feeds_fail_closed(self):
        worker = NewsWorker()
        with (
            patch.object(settings, "news_rss_feed_urls", ""),
            patch("app.modules.news.worker.Base.metadata.create_all"),
            patch("app.modules.news.worker.engine.connect") as connect,
            patch("app.modules.news.worker.engine.dialect.name", "sqlite"),
            patch.object(worker, "_persist_status"),
        ):
            connect.return_value = MagicMock()
            result = worker.run_once()
        self.assertEqual(result["state"], "UNCONFIGURED")
        self.assertTrue(result["halt_new_entries"])
        self.assertTrue(result["halt_all_symbols"])


    def test_empty_feed_result_fails_closed_instead_of_allowing_signals(self):
        worker = NewsWorker()
        session = MagicMock()
        session.__enter__.return_value = session
        with (
            patch.object(settings, "news_rss_feed_urls", "https://example.com/feed.xml"),
            patch("app.modules.news.worker.Base.metadata.create_all"),
            patch("app.modules.news.worker.engine.connect") as connect,
            patch("app.modules.news.worker.engine.dialect.name", "sqlite"),
            patch("app.modules.news.worker.fetch_rss_articles", return_value=([], [])),
            patch("app.modules.news.worker.SessionLocal", return_value=session),
            patch.object(worker, "_persist_status"),
        ):
            connect.return_value = MagicMock()
            result = worker.run_once()

        self.assertEqual(result["state"], "EMPTY")
        self.assertTrue(result["halt_new_entries"])
        self.assertTrue(result["halt_all_symbols"])
        self.assertEqual(result["last_article_count"], 0)


class LiveScannerGuardTests(unittest.TestCase):
    def test_quantity_uses_per_trade_risk_and_capital_limit(self):
        scanner = OrbScanner()
        quantity, planned_risk, estimated_cost = scanner._size_candidate(100.0, 99.0)
        self.assertEqual(quantity, 217)
        self.assertEqual(planned_risk, 249.55)
        self.assertEqual(estimated_cost, 32.55)


    def test_issuer_news_block_is_limited_to_affected_symbol(self):
        now = datetime(2026, 11, 23, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        status = {
            "state": "AVAILABLE",
            "last_heartbeat_at": now.isoformat(),
            "halt_new_entries": True,
            "halt_all_symbols": False,
            "blocked_symbols": ["ABC"],
        }
        self.assertTrue(news_gate_blocks_candidate(status, "ABC", now))
        self.assertFalse(news_gate_blocks_candidate(status, "RELIANCE", now))

    def test_stale_news_heartbeat_blocks_every_candidate(self):
        now = datetime(2026, 11, 23, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        status = {
            "state": "AVAILABLE",
            "last_heartbeat_at": (now - timedelta(minutes=20)).isoformat(),
            "halt_new_entries": False,
            "halt_all_symbols": False,
            "blocked_symbols": [],
        }
        self.assertTrue(news_gate_blocks_candidate(status, "RELIANCE", now))

    def test_daily_signal_cap_and_symbol_direction_deduplication(self):
        scanner = OrbScanner()
        self.assertTrue(scanner._reserve_daily_signal("AAA", "BUY"))
        self.assertFalse(scanner._reserve_daily_signal("AAA", "BUY"))
        self.assertTrue(scanner._reserve_daily_signal("BBB", "SELL"))
        self.assertTrue(scanner._reserve_daily_signal("CCC", "BUY"))
        self.assertFalse(scanner._reserve_daily_signal("DDD", "BUY"))


if __name__ == "__main__":
    unittest.main()
