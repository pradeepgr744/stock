import unittest
from datetime import datetime

from pydantic import ValidationError

from app.api.research import NewsAssessmentRequest, assess_configured_news
from app.config import settings


class NewsResearchApiTests(unittest.TestCase):
    def test_missing_feed_configuration_fails_closed(self):
        previous = settings.news_rss_feed_urls
        try:
            settings.news_rss_feed_urls = ""
            result = assess_configured_news(NewsAssessmentRequest())
        finally:
            settings.news_rss_feed_urls = previous
        self.assertEqual(result["coverage_status"], "UNAVAILABLE")
        self.assertTrue(result["halt_new_entries"])
        self.assertTrue(result["halt_all_symbols"])

    def test_as_of_requires_timezone(self):
        with self.assertRaises(ValidationError):
            NewsAssessmentRequest(as_of=datetime(2026, 11, 17, 10, 0))


if __name__ == "__main__":
    unittest.main()
