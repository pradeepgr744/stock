from __future__ import annotations

from html import escape
from typing import Any

import httpx

from ...config import settings


class TelegramError(Exception):
    pass


class TelegramNotifier:
    @property
    def configured(self) -> bool:
        return bool(settings.telegram_bot_token.strip() and settings.telegram_chat_id.strip())

    def send_hint(self, hint: dict[str, Any]) -> None:
        if not self.configured:
            raise TelegramError(
                "Telegram is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in backend/.env."
            )

        required_fields = ("symbol", "strategy", "direction", "entry", "stop_loss", "target")
        missing_fields = [field for field in required_fields if field not in hint]
        if missing_fields:
            raise TelegramError(f"Hint is missing required fields: {', '.join(missing_fields)}.")

        if hint.get("demo"):
            title = "DEMO TRADE ALERT — NOT A LIVE RECOMMENDATION"
        elif hint.get("live_scan"):
            title = "LIVE TRADE CANDIDATE — MANUAL REVIEW ONLY"
        else:
            title = "Trading hint — manual execution only"
        stop_label = (
            "Reference invalidation (not an order)"
            if hint.get("live_scan")
            else "Stop-loss"
        )
        lines = [
            f"<b>{escape(title)}</b>",
            f"Symbol: <b>{escape(str(hint['symbol']))}</b>",
            f"Strategy: {escape(str(hint['strategy']))}",
            f"Direction: {escape(str(hint['direction']))}",
            f"Suggested entry: {escape(str(hint['entry']))}",
            f"{stop_label}: {escape(str(hint['stop_loss']))}",
            f"Illustrative target: {escape(str(hint['target']))}"
            if hint.get("live_scan")
            else f"Target: {escape(str(hint['target']))}",
            f"Quantity: {escape(str(hint.get('quantity', 'Not calculated')))}",
            f"Reason: {escape(str(hint.get('reason', 'Not provided')))}",
        ]
        if hint.get("planned_risk") is not None:
            lines.append(f"Planned stop risk incl. estimated costs: {escape(str(hint['planned_risk']))}")
        if hint.get("estimated_round_trip_cost") is not None:
            lines.append(f"Estimated round-trip costs: {escape(str(hint['estimated_round_trip_cost']))}")
        if hint.get("quality_score") is not None:
            lines.append(f"Setup quality score: {escape(str(hint['quality_score']))}/100 (not a win probability)")
        if hint.get("vwap") is not None:
            lines.append(f"Session VWAP: {escape(str(hint['vwap']))}")
        if hint.get("rvol") is not None:
            lines.append(f"Relative volume: {escape(str(hint['rvol']))}x")
        if hint.get("detected_at"):
            lines.append(f"Detected at: {escape(str(hint['detected_at']))}")
        if hint.get("live_scan"):
            lines.append("Candidate only; no setup is guaranteed. Confirm current price, liquidity, and risk.")
        lines.append("Verify market conditions and risk before placing any manual order.")
        message = "\n".join(
            lines
        )

        try:
            response = httpx.get(
                f"https://api.telegram.org/bot{settings.telegram_bot_token.strip()}/sendMessage",
                params={
                    "chat_id": settings.telegram_chat_id.strip(),
                    "text": message,
                    "parse_mode": "HTML",
                },
                timeout=settings.upstox_request_timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramError("Telegram notification request failed.") from exc

        if not result.get("ok"):
            raise TelegramError("Telegram rejected the notification.")


telegram_notifier = TelegramNotifier()
