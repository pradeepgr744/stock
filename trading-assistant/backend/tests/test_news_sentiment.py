import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.modules.news.sentiment import NewsArticle, NewsState, assess_news

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 17, 10, 0, tzinfo=IST)


class NewsRiskTests(unittest.TestCase):
    def article(self, title, url="https://www.nseindia.com/example", symbols=("RELIANCE",), age=5):
        return NewsArticle(
            title=title, source="Test feed", url=url,
            published_at=NOW - timedelta(minutes=age), symbols=symbols,
        )

    def test_official_fresh_market_disruption_blocks_new_entries(self):
        result = assess_news(
            [self.article("NSE exchange outage announced")],
            as_of=NOW, relevant_symbols=["RELIANCE"],
        )
        self.assertEqual(result.state, NewsState.BLOCKED)
        self.assertTrue(result.halt_new_entries)
        self.assertTrue(result.halt_all_symbols)

    def test_issuer_default_blocks_only_tagged_symbol(self):
        result = assess_news(
            [self.article("Company default reported", symbols=("RELIANCE",))],
            as_of=NOW, relevant_symbols=["RELIANCE", "TCS"],
        )
        self.assertEqual(result.state, NewsState.BLOCKED)
        self.assertFalse(result.halt_all_symbols)
        self.assertEqual(result.blocked_symbols, ("RELIANCE",))

    def test_unverified_high_impact_claim_does_not_hard_block(self):
        result = assess_news(
            [self.article("RBI policy emergency announcement", "https://randomblog.example/news")],
            as_of=NOW, relevant_symbols=["RELIANCE"],
        )
        self.assertEqual(result.state, NewsState.CAUTION)
        self.assertFalse(result.halt_new_entries)

    def test_old_headlines_are_ignored(self):
        result = assess_news([self.article("Profit rises", age=120)], as_of=NOW)
        self.assertEqual(result.articles, ())
        self.assertEqual(result.sentiment_score, 0)

    def test_duplicate_urls_are_deduplicated(self):
        article = self.article("Profit rises")
        result = assess_news([article, article], as_of=NOW)
        self.assertEqual(len(result.articles), 1)

    def test_future_timestamp_is_not_scored(self):
        future = NewsArticle(
            title="Profit rises", source="NSE",
            url="https://www.nseindia.com/a",
            published_at=NOW + timedelta(minutes=20),
        )
        result = assess_news([future], as_of=NOW)
        self.assertEqual(result.articles, ())
        self.assertTrue(any("Future-dated" in reason for reason in result.reasons))

    def test_official_company_event_sentiment_is_directional_but_not_probability(self):
        result = assess_news(
            [self.article("Reliance beats estimates and profit rises")],
            as_of=NOW, relevant_symbols=["RELIANCE"],
        )
        self.assertEqual(result.articles[0].sentiment_label, "POSITIVE")
        self.assertGreater(result.sentiment_score, 0)

    def test_naive_datetime_is_rejected(self):
        with self.assertRaises(ValueError):
            assess_news([], as_of=datetime(2026, 11, 17, 10, 0))


if __name__ == "__main__":
    unittest.main()
