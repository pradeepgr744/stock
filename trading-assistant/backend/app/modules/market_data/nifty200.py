from __future__ import annotations

import csv
import io
import time
from threading import Lock

import httpx

from ...config import settings


class Nifty200UniverseError(Exception):
    pass


_cache_lock = Lock()
_cached_symbols: list[str] = []
_cached_at = 0.0
_CACHE_TTL_SECONDS = 12 * 60 * 60


def parse_nifty200_csv(content: str) -> list[str]:
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise Nifty200UniverseError("NIFTY 200 constituent CSV has no header.")
    symbol_column = next(
        (name for name in reader.fieldnames if name.strip().casefold() == "symbol"),
        None,
    )
    if symbol_column is None:
        raise Nifty200UniverseError("NIFTY 200 CSV does not contain a Symbol column.")

    symbols: list[str] = []
    seen: set[str] = set()
    for row in reader:
        raw = (row.get(symbol_column) or "").strip().upper()
        if not raw or raw in seen:
            continue
        if not raw.replace("-", "").replace("&", "").isalnum():
            continue
        seen.add(raw)
        symbols.append(raw)

    if not 180 <= len(symbols) <= 200:
        raise Nifty200UniverseError(
            f"Expected 180–200 NIFTY 200 constituents; received {len(symbols)}."
        )
    return symbols


def load_nifty200_symbols(*, force_refresh: bool = False) -> list[str]:
    global _cached_symbols, _cached_at
    with _cache_lock:
        if (
            not force_refresh
            and _cached_symbols
            and time.monotonic() - _cached_at < _CACHE_TTL_SECONDS
        ):
            return list(_cached_symbols)

    try:
        response = httpx.get(
            settings.nifty200_constituents_url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; TradingAssistant/1.0)",
                "Accept": "text/csv,*/*",
                "Referer": "https://www.niftyindices.com/",
            },
            timeout=15.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        symbols = parse_nifty200_csv(response.text)
    except (httpx.HTTPError, UnicodeError, csv.Error, Nifty200UniverseError) as exc:
        raise Nifty200UniverseError(
            f"Could not load the official NIFTY 200 constituent list: {exc}"
        ) from exc

    with _cache_lock:
        _cached_symbols = symbols
        _cached_at = time.monotonic()
    return list(symbols)
