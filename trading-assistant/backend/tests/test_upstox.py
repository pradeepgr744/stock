import gzip
import json
import unittest
from datetime import date
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.modules.market_data.upstox import Instrument, UpstoxClient, UpstoxError
from app.modules.notifications.telegram import TelegramError, TelegramNotifier


class UpstoxClientTests(unittest.TestCase):
    def test_ltp_uses_bearer_auth_and_repeated_instrument_keys(self) -> None:
        client = UpstoxClient()
        response = Mock(status_code=200, is_error=False)
        response.json.return_value = {"status": "success", "data": {}}

        with (
            patch("app.modules.market_data.upstox.settings.upstox_access_token", "local-test-token"),
            patch("app.modules.market_data.upstox.httpx.request", return_value=response) as request,
        ):
            payload = client.get_ltp(["NSE_EQ|KEY1", "NSE_EQ|KEY2"])

        self.assertEqual(payload["status"], "success")
        self.assertEqual(request.call_args.args[:2], ("GET", "https://api.upstox.com/v2/market-quote/ltp"))
        self.assertEqual(
            request.call_args.kwargs["headers"]["Authorization"],
            "Bearer local-test-token",
        )
        self.assertEqual(
            request.call_args.kwargs["params"],
            [("instrument_key", "NSE_EQ|KEY1"), ("instrument_key", "NSE_EQ|KEY2")],
        )

    def test_ltp_rejects_missing_token(self) -> None:
        client = UpstoxClient()
        with patch("app.modules.market_data.upstox.settings.upstox_access_token", ""):
            with self.assertRaisesRegex(UpstoxError, "not configured") as error:
                client.get_ltp(["NSE_EQ|KEY1"])
        self.assertEqual(error.exception.status_code, 503)

    def test_ltp_reports_expired_or_invalid_token_without_echoing_it(self) -> None:
        client = UpstoxClient()
        response = Mock(status_code=401)

        with (
            patch("app.modules.market_data.upstox.settings.upstox_access_token", "private-token"),
            patch("app.modules.market_data.upstox.httpx.request", return_value=response),
        ):
            with self.assertRaises(UpstoxError) as error:
                client.get_ltp(["NSE_EQ|KEY1"])

        self.assertEqual(error.exception.status_code, 401)
        self.assertNotIn("private-token", str(error.exception))

    def test_intraday_candle_url_encodes_instrument_key(self) -> None:
        client = UpstoxClient()
        response = Mock(status_code=200, is_error=False)
        response.json.return_value = {"status": "success", "data": {"candles": []}}

        with (
            patch("app.modules.market_data.upstox.settings.upstox_access_token", "local-test-token"),
            patch("app.modules.market_data.upstox.httpx.request", return_value=response) as request,
        ):
            client.get_intraday_candles("NSE_EQ|INE000A01001", "minutes", 5)

        self.assertEqual(
            request.call_args.args[1],
            "https://api.upstox.com/v3/historical-candle/intraday/NSE_EQ%7CINE000A01001/minutes/5",
        )

    def test_market_status_and_holidays_use_v2_endpoints(self) -> None:
        client = UpstoxClient()
        response = Mock(status_code=200, is_error=False)
        response.json.return_value = {"status": "success", "data": {}}

        with (
            patch("app.modules.market_data.upstox.settings.upstox_access_token", "local-test-token"),
            patch("app.modules.market_data.upstox.httpx.request", return_value=response) as request,
        ):
            client.get_market_status("NSE")
            client.get_holidays()
            client.get_holidays(date(2026, 11, 13))

        self.assertEqual(
            [call.args[1] for call in request.call_args_list],
            [
                "https://api.upstox.com/v2/market/status/NSE",
                "https://api.upstox.com/v2/market/holidays",
                "https://api.upstox.com/v2/market/holidays/2026-11-13",
            ],
        )

    def test_instrument_refresh_filters_to_nse_cash_equities(self) -> None:
        client = UpstoxClient()
        master = [
            {
                "segment": "NSE_EQ",
                "instrument_type": "EQ",
                "instrument_key": "NSE_EQ|INE000A01001",
                "trading_symbol": "SAMPLE",
                "name": "Sample Limited",
                "tick_size": 0.05,
                "lot_size": 1,
            },
            {
                "segment": "NSE_FO",
                "instrument_type": "CE",
                "instrument_key": "NSE_FO|CONTRACT",
                "trading_symbol": "SAMPLE",
            },
        ]
        response = Mock()
        response.content = gzip.compress(json.dumps(master).encode("utf-8"))
        response.raise_for_status.return_value = None

        with patch("app.modules.market_data.upstox.httpx.get", return_value=response):
            self.assertEqual(client.refresh_nse_equities(), 1)

        found = client.search_nse_equities("sample", 10)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].instrument_key, "NSE_EQ|INE000A01001")
        self.assertEqual(found[0].tick_size, 0.05)


class MarketDataRouteTests(unittest.TestCase):
    def test_market_data_route_reports_missing_upstox_token(self) -> None:
        client = TestClient(app)
        with patch("app.modules.market_data.upstox.settings.upstox_access_token", ""):
            response = client.get(
                "/api/market-data/ltp",
                params={"instrument_key": "NSE_EQ|INE000A01001"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.json()["detail"])

    def test_historical_route_rejects_reversed_date_range(self) -> None:
        client = TestClient(app)
        response = client.get(
            "/api/market-data/candles/historical",
            params={
                "instrument_key": "NSE_EQ|INE000A01001",
                "to_date": "2026-01-01",
                "from_date": "2026-01-02",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_scanner_status_route_is_available(self) -> None:
        response = TestClient(app).get("/api/scanner/status")
        self.assertEqual(response.status_code, 200)
        self.assertIn("connection", response.json())
        self.assertIn("last_message_at", response.json())

    def test_scanner_start_resolves_nifty200_symbols_and_starts_feed(self) -> None:
        client = TestClient(app)
        symbols = [f"TEST{i}" for i in range(180)]
        instruments = {
            symbol: Instrument(f"NSE_EQ|{symbol}", symbol, f"{symbol} Ltd", 0.05, 1)
            for symbol in symbols
        }

        with (
            patch("app.modules.strategy.orb_scanner.settings.upstox_access_token", "token"),
            patch("app.modules.strategy.orb_scanner.settings.telegram_bot_token", "bot-token"),
            patch("app.modules.strategy.orb_scanner.settings.telegram_chat_id", "chat-id"),
            patch("app.api.market_data.load_nifty200_symbols", return_value=symbols),
            patch("app.api.market_data.upstox_client.refresh_nse_equities", return_value=2000),
            patch(
                "app.api.market_data.upstox_client.get_nse_equities_by_symbols",
                return_value=instruments,
            ),
            patch(
                "app.api.market_data.orb_scanner.start",
                return_value={"running": True, "connection": "connecting"},
            ) as start,
        ):
            response = client.post("/api/scanner/start")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["running"])
        self.assertEqual(response.json()["universe"], "NIFTY 200")
        self.assertEqual(response.json()["universe_count"], 180)
        start.assert_called_once_with(list(instruments.values()))


class TelegramNotifierTests(unittest.TestCase):
    def test_hint_message_escapes_html_and_never_uses_order_api(self) -> None:
        notifier = TelegramNotifier()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True}
        hint = {
            "symbol": "<SCRIPT>",
            "strategy": "VWAP & EMA",
            "direction": "BUY",
            "entry": 100.0,
            "stop_loss": 98.0,
            "target": 104.0,
            "quantity": 2,
            "reason": "Rule condition met",
        }

        with (
            patch("app.modules.notifications.telegram.settings.telegram_bot_token", "bot-token"),
            patch("app.modules.notifications.telegram.settings.telegram_chat_id", "chat-id"),
            patch("app.modules.notifications.telegram.httpx.get", return_value=response) as get,
        ):
            notifier.send_hint(hint)

        request = get.call_args
        self.assertIn("sendMessage", request.args[0])
        self.assertNotIn("sendOrder", request.args[0])
        self.assertIn("&lt;SCRIPT&gt;", request.kwargs["params"]["text"])
        self.assertEqual(request.kwargs["params"]["parse_mode"], "HTML")
        self.assertIn("manual execution only", request.kwargs["params"]["text"])

    def test_live_scan_message_is_labeled_as_candidate(self) -> None:
        notifier = TelegramNotifier()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True}

        with (
            patch("app.modules.notifications.telegram.settings.telegram_bot_token", "bot-token"),
            patch("app.modules.notifications.telegram.settings.telegram_chat_id", "chat-id"),
            patch("app.modules.notifications.telegram.httpx.get", return_value=response) as get,
        ):
            notifier.send_hint(
                {
                    "symbol": "TEST",
                    "strategy": "ORB",
                    "direction": "BUY",
                    "entry": 103,
                    "stop_loss": 99,
                    "target": 111,
                    "live_scan": True,
                }
            )

        self.assertIn("LIVE ORB CANDIDATE", get.call_args.kwargs["params"]["text"])
        self.assertIn("no setup is guaranteed", get.call_args.kwargs["params"]["text"])
        self.assertIn("Reference invalidation (not an order)", get.call_args.kwargs["params"]["text"])

    def test_hint_delivery_requires_local_telegram_configuration(self) -> None:
        notifier = TelegramNotifier()
        with (
            patch("app.modules.notifications.telegram.settings.telegram_bot_token", ""),
            patch("app.modules.notifications.telegram.settings.telegram_chat_id", ""),
        ):
            with self.assertRaisesRegex(TelegramError, "Telegram is not configured"):
                notifier.send_hint(
                    {
                        "symbol": "TEST",
                        "strategy": "test",
                        "direction": "BUY",
                        "entry": 100,
                        "stop_loss": 98,
                        "target": 104,
                    }
                )

    def test_test_notification_route_sends_sample_trade_alert(self) -> None:
        client = TestClient(app)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True}

        with (
            patch("app.modules.notifications.telegram.settings.telegram_bot_token", "bot-token"),
            patch("app.modules.notifications.telegram.settings.telegram_chat_id", "chat-id"),
            patch("app.modules.notifications.telegram.httpx.get", return_value=response) as get,
        ):
            api_response = client.post("/api/notifications/test")

        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(api_response.json()["status"], "sent")
        self.assertIn("RELIANCE", get.call_args.kwargs["params"]["text"])
        self.assertIn("DEMO TRADE ALERT", get.call_args.kwargs["params"]["text"])


if __name__ == "__main__":
    unittest.main()
