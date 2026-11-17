# Trading Assistant Monorepo

This repository is structured for the Indian intraday trading assistant project described in the milestone-based plan.

## Current milestone: Phase 2 — Upstox market data and experimental ORB alerts

- React + TypeScript dashboard shell
- FastAPI backend
- PostgreSQL connection health-check
- Server-side Upstox Analytics/access-token configuration
- LTP and full quote endpoints
- NSE exchange status and market-holiday endpoints
- Upstox V3 historical and intraday candle endpoints
- Compressed instrument-master download and NSE cash-equity search
- Upstox Market Data Feed V3 WebSocket scanner
- Experimental Opening Range Breakout (ORB) candidate alerts to Telegram
- Telegram demo-alert button and scanner start/stop controls
- Dashboard status for Upstox and Telegram configuration

## Project structure

```text
trading-assistant/
├── backend/
│   ├── .env.example
│   ├── requirements.txt
│   └── app/
│       ├── api/
│       ├── modules/
│       │   ├── market_data/
│       │   ├── strategy/
│       │   └── notifications/
│       ├── config.py
│       ├── database.py
│       └── main.py
│   └── tests/
├── database/
│   └── docker-compose.yml
├── docs/
├── frontend/
│   ├── package.json
│   ├── src/
│   ├── vite.config.ts
│   └── ...
├── .gitignore
├── README.md
└── .venv/
```

## Windows PowerShell setup

```powershell
cd D:\Projects\stock\trading-assistant
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

cd frontend
npm install

cd ..\backend
python -m pip install -r requirements.txt
```

## Run PostgreSQL with Docker

```powershell
cd D:\Projects\stock\trading-assistant\database
docker compose up -d
```

For the local native PostgreSQL service installed on this machine, use:

```powershell
Start-Service postgresql-x64-17
```

If PowerShell reports access denied, open it as Administrator. PostgreSQL may already be running.

## Run the services

```powershell
cd D:\Projects\stock\trading-assistant\backend
. ..\.venv\Scripts\Activate.ps1
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```powershell
cd D:\Projects\stock\trading-assistant\frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

## Health checks

- Backend: <http://localhost:8000/health>
- API: <http://localhost:8000/api/health>
- Integration configuration: <http://localhost:8000/api/integrations/status>
- Interactive API docs: <http://localhost:8000/docs>
- Frontend: <http://localhost:5173>

## Configure Upstox and Telegram locally

Edit `backend\.env` locally. Never paste secrets into chat or put them in the frontend.

```dotenv
UPSTOX_ACCESS_TOKEN=your_read_only_upstox_token
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

Restart the backend after editing `.env`. The dashboard reports whether each integration is configured, but never returns token values. API requests with an expired token report a clear 401 error. Upstox rate limits are surfaced as 429; this app does not automatically retry market-data requests.

The configured check only confirms that values are present. It does not confirm that an access token is currently valid or that live-feed access is enabled for the account.

### Send a Telegram sample alert

After bot and chat credentials are configured, use the dashboard button or call the backend endpoint directly to send a sample trade-alert message:

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/notifications/test
```

The sample payload follows the same structure used when a manual execution hint is sent to Telegram, including symbol, strategy, direction, entry, stop-loss, target, and reason.

### Start the live ORB candidate scanner

Open the dashboard and select **Start live scan**. The backend downloads the current Upstox instrument master, resolves the configured NSE equity symbols, and subscribes to Upstox Market Data Feed V3. It remains active until you select **Stop scan** or stop the backend. Scanner connection, latest feed message, and last alert are displayed on the dashboard.

The default starter rules use a 9:15–9:30 IST opening range, build five-minute bars from the stream's one-minute OHLC data, and alert when a completed five-minute close breaks the range with volume at least equal to the average five-minute volume in the opening range. The opposite range boundary is shown as an illustrative invalidation level; the target is an illustrative 2R value. The scanner emits at most one alert per symbol/direction/day.

`ORB_SCANNER_SYMBOLS` in `backend\.env` can override the default NIFTY 50 symbol list. Review it against the current index constituents before each use. Start the scan before the 9:15 IST open and leave the backend running so it captures all 15 opening-range candles. If the access token expires or feed access is rejected, the scanner reports an error and stops; refresh the token, restart the backend, and start scanning again.

These are unvalidated starter rules, not an optimized strategy. There is no backtest, paper-trading track record, position sizing, portfolio-risk limit, or guaranteed return. Alerts are candidates for your manual review only. The app does not place or manage orders, and message prices can be stale by the time you act. Test with paper trading first.

### Try the data API

Use an Upstox `instrument_key`, not a trading symbol. Refresh the instrument master once per day before searching:

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/instruments/refresh
Invoke-RestMethod 'http://localhost:8000/api/instruments/search?query=RELIANCE'
```

Then copy a returned `instrument_key` into the quote and candle requests:

```powershell
$key = 'NSE_EQ|INE002A01018'
Invoke-RestMethod "http://localhost:8000/api/market-data/ltp?instrument_key=$([uri]::EscapeDataString($key))"
Invoke-RestMethod "http://localhost:8000/api/market-data/candles/intraday?instrument_key=$([uri]::EscapeDataString($key))&unit=minutes&interval=1"
Invoke-RestMethod 'http://localhost:8000/api/market-data/market-status?exchange=NSE'
Invoke-RestMethod 'http://localhost:8000/api/market-data/holidays'
```

Historical example:

```powershell
Invoke-RestMethod "http://localhost:8000/api/market-data/candles/historical?instrument_key=$([uri]::EscapeDataString($key))&unit=minutes&interval=5&from_date=2026-11-10&to_date=2026-11-10"
```

### Run backend tests

```powershell
cd D:\Projects\stock\trading-assistant\backend
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

### Notifications and trading scope

The ORB evaluator and live-feed scanner are experimental. They use market-feed one-minute candles aggregated into five-minute bars, and send candidate alerts only; they do not prove profitability, calculate confidence percentages, size positions, or place orders. Do not treat the sample Telegram alert as a signal.

The scanner is manually started from the dashboard or `POST /api/scanner/start`; inspect it using `GET /api/scanner/status` and stop with `POST /api/scanner/stop`. It needs a valid Upstox token, live-feed permission, Telegram configuration, a current symbol list, and an active backend process. No guarantee is made that every market event will be received or that a detected setup will be profitable.

## Official Upstox references

- [Authentication](https://upstox.com/developer/api-documentation/authentication/)
- [Instrument master](https://upstox.com/developer/api-documentation/instruments/)
- [Historical Candle Data V3](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/)
- [Intraday Candle Data V3](https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/)
- [Exchange Status](https://upstox.com/developer/api-documentation/get-market-status/)
- [Market holidays](https://upstox.com/developer/api-documentation/get-market-holidays/)
- [Market Data Feed V3](https://upstox.com/developer/api-documentation/v3/get-market-data-feed/)


## Historical backtesting (new research module)

The backend includes an offline comparison engine for ORB, VWAP pullback, and previous-day liquidity sweep/reclaim. It uses completed 5-minute candles, next-bar-open entries, a stop-first assumption when stop and target are both touched, a ₹50,000 starting balance, 0.5% planned risk, and a maximum of three trades per day. It does not send alerts or place orders.

### Download a current NIFTY 200 sample

The downloader reads the current official NIFTY 200 constituent CSV from [Nifty Indices](https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv), maps symbols through the Upstox instrument master, and downloads 5-minute candles:

```powershell
cd D:\Projects\stock\trading-assistant\backend
python scripts\download_nifty200_history.py --from-date 2026-01-01 --to-date 2026-02-28 --output data\nifty200_5m.csv
```

Upstox V3 limits 1–15 minute historical requests to one month per request. The downloader chunks requests by calendar month and uses sequential requests with a delay. See the [Upstox V3 historical candle documentation](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/). Historical minute data is documented from January 2022. A large NIFTY 200 multi-year download can take a long time and may be subject to API limits.

Important: the official constituent CSV is today's/current universe. Do not use today's members to claim unbiased results for old periods. For credible multi-year backtests, supply point-in-time historical index membership and include removed/delisted stocks. See [NSE's NIFTY 200 index page](https://www.nseindia.in/static/products-services/indices-nifty200-index).

### Run strategy comparison

```powershell
python -m app.modules.backtesting.cli --input data\nifty200_5m.csv --strategy all --capital 50000 --risk-pct 0.5 --max-trades-per-day 3 --slippage-bps 5 --brokerage-per-order 20 --buy-cost-bps 1.0 --sell-cost-bps 4.0 --output reports\compare.json
```

The buy/sell bps in this example are placeholders. Replace them with a calculation from your actual Upstox contract note and applicable statutory charges. Full instructions and limitations are in [docs/BACKTESTING.md](docs/BACKTESTING.md). Run tests with:

```powershell
python -m unittest discover -s tests -v
```


## Intraday decision engine and news-risk research

See [INTRADAY_DECISION_ENGINE.md](docs/INTRADAY_DECISION_ENGINE.md) for the factor pipeline, event handling policy, validation rules, and current implementation limits.

The backend exposes a research-only news assessment endpoint:

~~~powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/research/news/assess -ContentType 'application/json' -Body '{"symbols":["RELIANCE","TCS"]}'
~~~

Set NEWS_RSS_FEED_URLS in backend/.env to comma-separated HTTPS RSS/Atom feed URLs copied from official NSE, BSE, RBI, SEBI, PIB or MCA publisher pages. No feed URLs are bundled by default. If feeds are unconfigured or all fail, the endpoint reports news coverage unavailable and fails closed. The endpoint fetches feeds when called; continuous background monitoring and live scanner integration are not implemented yet.

The news sentiment is a transparent keyword baseline, not a prediction model or calibrated probability. Official market-wide disruption events can block all new entries; verified issuer-specific events can block tagged symbols. The current news endpoint is not yet connected to the live scanner or historical backtester. Do not use it as a standalone buy/sell signal.
