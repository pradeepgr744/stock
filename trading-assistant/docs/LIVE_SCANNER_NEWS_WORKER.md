# Live signals, NIFTY 200 scanner and news worker

## Live market scanner

The scanner resolves the current NIFTY 200 constituent CSV from the Nifty Indices site, then intersects it with the NSE equity instrument master downloaded from Upstox. It will not silently call a partial list the NIFTY 200. The scanner refuses to start unless all 200 constituents resolve. The constituent list is cached in memory for 12 hours; use `GET /api/universe/nifty200?refresh=true` to force a refresh.

The Upstox Market Data Feed V3 websocket is subscribed in full mode for the resolved symbols. A PostgreSQL advisory lock allows only one live scanner instance to publish alerts across Render replicas or Uvicorn workers; the lock is released when the scanner stops. It evaluates completed one-minute bars and emits:

- **ORB:** 09:15–09:30 IST opening range; completed five-minute breakout confirmation and volume confirmation.
- **VWAP reclaim:** a completed candle crosses and holds session VWAP with relative-volume and trend/body confirmation.
- **Liquidity sweep/reclaim:** a completed candle sweeps the previous 20 one-minute-bar high/low, closes back through that level, and is on the correct side of session VWAP.

The VWAP and sweep/reclaim evaluator requires at least 1.5x recent one-minute relative volume, uses a simple recent-close trend check and candle-body confirmation, rejects stop distances above 1.2%, and targets an illustrative 2.5R. Entries are restricted to 09:30–14:45 IST. ORB stop distance is also capped at 1.2%. Alert quantity is sized against the ₹250 per-trade risk budget and ₹50,000 notional capital cap. The scanner applies a configurable-in-code 0.15% round-trip cost estimate to sizing and checks that net reward-to-risk remains at least 2:1; actual broker charges can differ.

These are transparent rule filters, not trained probabilities. The score displayed in Telegram is not a win-rate estimate. No broker orders are submitted.

## Alert guardrails

- The live scanner suppresses all new candidate alerts while the news worker reports unavailable coverage or a high-impact market-wide event.
- Symbol-specific high-impact official events suppress that symbol.
- At most three unique symbol/direction candidate alerts are sent per IST day. This is a candidate-alert cap, not a position counter. Signal checks also reject stale candidates (older than 120 seconds), stop distances above 1.2%, per-trade risk above ₹250, capital exposure above ₹50,000, and net reward-to-risk below 2:1.
- The scanner cannot infer which Telegram alerts were actually traded. The database-backed manual risk journal enforces the two-open-position limit, ₹500 aggregate planned risk, three recorded entries/day, ₹750 daily loss stop, ₹50,000 exposure cap, 15-minute cooldown after a loss, and halt after two consecutive losses when the operator records actual fills, marks and exits. The journal is not broker-connected; missed or inaccurate manual updates make these controls incomplete.
- No signal is a guarantee. Slippage, gaps, liquidity and stale or missing feed data can invalidate the displayed reference prices.

## Background news worker

FastAPI starts a daemon worker through its lifespan hook. It polls configured official RSS/Atom feeds every `NEWS_POLL_INTERVAL_SECONDS` (minimum effective interval 60 seconds), assesses fresh articles, and upserts them into the `news_events` table using a URL hash as the idempotency key.

The `news_events` and `news_worker_state` tables are created with SQLAlchemy `create_all` at worker startup. Worker status is persisted in PostgreSQL, so dashboard replicas and process restarts can read the latest state. A PostgreSQL advisory lock prevents multiple Render/Uvicorn instances from polling and writing concurrently. The worker preserves source, URL, publication/fetch timestamps, symbols, summary, sentiment label/score, event severity, source trust and assessment reasons. Existing events are not deleted when a feed fails.

Endpoints:

- `GET /api/research/news/worker/status` — worker, coverage and entry-gate status.
- `POST /api/research/news/worker/run-once` — manually trigger an ingestion cycle.
- `GET /api/research/news/events?limit=50` — newest persisted events (1–200).
- `POST /api/research/news/assess` remains separate as the on-demand assessment route.

If feeds are unconfigured, any configured feed fails, no fresh usable articles are returned, or ingestion/database access fails, the worker fails closed and pauses new signal alerts. EMPTY means coverage did not provide fresh usable articles; it is not a claim that an adverse market event was detected.

## Environment

Set these on the backend deployment:

```env
NEWS_RSS_FEED_URLS=https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml
RISK_API_KEY=<long-random-secret>
NEWS_POLL_INTERVAL_SECONDS=300
NEWS_MAX_ARTICLE_AGE_MINUTES=90
NIFTY200_CONSTITUENTS_URL=https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv
```

The backend needs network access to the Nifty Indices CSV, configured RSS feeds, Upstox, Telegram and the configured database. Set `RISK_API_KEY` to a long random secret before using `/api/risk/*`; send it in the `X-Risk-API-Key` header. Keep it out of source control and do not expose it in a public frontend bundle. Restart/redeploy the backend after changing environment variables.


## Manual risk journal API

The journal records only trades the operator has actually filled manually. It does not send any broker order.

- `GET /api/risk/status` — open risk, exposure, realized/unrealized P&L, trade count, loss streak and square-off requirement.
- `GET /api/risk/manual-trades` — recent manual fills.
- `POST /api/risk/manual-trades` — record an actual manual entry; requires symbol, side, quantity, entry, stop and target.
- `POST /api/risk/manual-trades/{trade_id}/mark` — update a manually observed mark price.
- `POST /api/risk/manual-trades/{trade_id}/exit` — record an actual exit and costs.

Every route requires `X-Risk-API-Key`. The default estimated round-trip cost is 0.15% of notional unless supplied explicitly; replace it with actual costs on exit. The API reports `square_off_required` at 15:15 IST or when the daily loss threshold is hit. It cannot force-close a position, and the operator remains responsible for manual square-off.

## Validation still required before risking capital

1. Run the unit tests and CI for the feature branch.
2. Confirm the deployed Upstox account permits the 200-instrument full-feed subscription and that messages contain one-minute OHLC/volume fields.
3. Confirm the NIFTY 200 CSV and Upstox instrument master resolve the expected constituent count.
4. Run in alert-only/paper mode and record every candidate, rejected setup, manual entry, exit, costs and slippage.
5. Backtest with survivorship-bias-aware historical constituents, realistic brokerage/taxes/slippage, and out-of-sample periods. Compare expectancy, profit factor, max drawdown and sample size—not just win rate.
