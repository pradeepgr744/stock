"""Download current NIFTY 200 constituents and Upstox 5-minute candles to CSV.

Upstox V3 minute intervals up to 15 minutes have a one-month maximum query
window, so the downloader requests one calendar month per instrument.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

import httpx

from app.modules.market_data.upstox import UpstoxError, upstox_client

NIFTY200_CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv"


def month_windows(start: date, end: date):
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        last_day = monthrange(cursor.year, cursor.month)[1]
        window_start = max(start, cursor)
        window_end = min(end, date(cursor.year, cursor.month, last_day))
        yield window_start, window_end
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)


def current_nifty200_symbols() -> list[str]:
    response = httpx.get(
        NIFTY200_CSV_URL,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.niftyindices.com/"},
        timeout=30,
    )
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text))
    if not reader.fieldnames or "Symbol" not in reader.fieldnames:
        raise RuntimeError("Official NIFTY 200 CSV did not contain a Symbol column.")
    symbols = sorted({row["Symbol"].strip().upper() for row in reader if row.get("Symbol", "").strip()})
    if len(symbols) < 180:
        raise RuntimeError(f"Expected approximately 200 NIFTY 200 constituents; received {len(symbols)}. Refusing partial universe.")
    return symbols


def main() -> None:
    parser = argparse.ArgumentParser(description="Export NIFTY 200 5-minute candles from Upstox")
    parser.add_argument("--from-date", required=True, type=date.fromisoformat)
    parser.add_argument("--to-date", required=True, type=date.fromisoformat)
    parser.add_argument("--symbols-file", help="Optional newline-separated symbols; defaults to current official NIFTY 200 members")
    parser.add_argument("--output", default="data/nifty200_5m.csv")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between API calls")
    args = parser.parse_args()

    if args.from_date > args.to_date:
        parser.error("--from-date must be <= --to-date")
    if args.from_date < date(2022, 1, 1):
        parser.error("Upstox V3 minute history is documented from January 2022.")
    if args.delay < 0.25:
        parser.error("--delay must be at least 0.25 seconds to avoid aggressive request bursts.")

    if args.symbols_file:
        symbols = sorted({
            line.strip().upper()
            for line in Path(args.symbols_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        })
    else:
        symbols = current_nifty200_symbols()

    if not symbols:
        parser.error("No symbols found.")
    if not upstox_client.configured:
        parser.error("Set UPSTOX_ACCESS_TOKEN in backend/.env before downloading.")
    print(f"Loading Upstox instrument master for {len(symbols)} symbols...")
    upstox_client.refresh_nse_equities()
    instruments = upstox_client.get_nse_equities_by_symbols(symbols)
    missing = sorted(set(symbols) - set(instruments))
    if missing:
        print(f"WARNING: {len(missing)} symbols were not resolved and will be skipped: {', '.join(missing[:20])}", file=sys.stderr)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    rows_written = 0
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "symbol", "open", "high", "low", "close", "volume"])
        for symbol in sorted(instruments):
            instrument = instruments[symbol]
            for start, end in month_windows(args.from_date, args.to_date):
                attempts = 0
                while True:
                    try:
                        payload = upstox_client.get_historical_candles(
                            instrument.instrument_key, "minutes", 5, end, start
                        )
                        break
                    except UpstoxError as error:
                        attempts += 1
                        if error.status_code == 429 and attempts <= 5:
                            wait = min(2 ** attempts, 30)
                            print(f"Rate limited; waiting {wait}s before retrying {symbol} {start}..{end}")
                            time.sleep(wait)
                            continue
                        raise
                candles = payload.get("data", {}).get("candles", [])
                for candle in candles:
                    if len(candle) < 6:
                        continue
                    key = (symbol, str(candle[0]))
                    if key in seen:
                        continue
                    seen.add(key)
                    writer.writerow([candle[0], symbol, *candle[1:6]])
                    rows_written += 1
                handle.flush()
                time.sleep(args.delay)
            print(f"{symbol}: exported")
    print(f"Exported {rows_written} candles to {output}")
    if missing:
        print("The output is incomplete because some symbols did not resolve. Check the warnings.")


if __name__ == "__main__":
    main()
