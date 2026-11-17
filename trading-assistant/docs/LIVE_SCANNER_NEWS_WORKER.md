# Live signals, NIFTY 200 scanner and news worker

## Live market scanner

The scanner resolves the current NIFTY 200 constituent CSV from the Nifty Indices site, then intersects it with the NSE equity instrument master downloaded from Upstox. The scanner refuses to start if fewer than 180 constituents resolve. The constituent list is cached in memory for 12 hours; use `GET /api/universe/nifty200?refresh=true` to force a refresh.

The Upstox Market Data Feed V3 websocket is subscribed in full mode for the resolved symbols. It evaluates completed one-minute bars and emits:

- **ORB:** 09:15–09:30 IST opening range; completed five-minute breakout confirmation and volume confirmation.
- **VWAP reclaim:** a completed candle crosses and holds session VWAP with relative-volume and trend/body confirmation.
- **Liquidity sweep/reclaim:** a completed candle sweeps the previous 20 one-minute-bar high/low, closes back through that level, and is on the correct side of session VWAP.

The VWAP and sweep/reclaim evaluator requires at least 1.5x recent one-minute relative volume, uses a simple recent-close trend check and candle-body confirmation, rejects stop distances above 1.2%, and targets an illustrative 2R. Entries are restricted to 09:30–14:45 IST. ORB stop distance is also capped at 1.2%.

These are transparent rule filters, not trained probabilities. The score displayed in Telegram is not a win-rate estimate. No broker orders are submitted.

## Alert guardrails

- The live scanner suppresses all new candidate alerts while the news worker reports unavailable coverage or a high-impact market-wide event.
- Symbol-specific high-impact official events suppress that symbol.
- At most three unique symbol/direction candidate alerts are sent per IST day. This is a candidate-alert cap, not a position counter; it cannot know which alerts were manually traded.
- The scanner cannot enforce the two-open-position limit or the ₹750 daily realized-loss stop without broker positions and manually recorded fills/P&L. Those controls must not be represented as enforced by this alert-only scanner.
- No signal is a guarantee. Slippage, gaps, liquidity and stale or missing feed data can invalidate the displayed reference prices.

## Background news worker

FastAPI starts a daemon worker through its lifespan hook. It polls configured official RSS/Atom feeds every `NEWS_POLL_INTERVAL_SECONDS` (minimum effective interval 60 seconds), assesses fresh articles, and upserts them into the `news_events` table using a URL hash as the idempotency key.

The table is created with SQLAlchemy `create_all` when the worker first runs. The worker preserves source, URL, publication/fetch timestamps, symbols, summary, sentiment label/score, event severity, source trust and assessment reasons. Existing events are not deleted when a feed fails.

Endpoints:

- `GET /api/research/news/worker/status` — worker, coverage and entry-gate status.
- `POST /api/research/news/worker/run-once` — manually trigger an ingestion cycle.
- `GET /api/research/news/events?limit=50` — newest persisted events (1–200).
- `POST /api/research/news/assess` remains separate as the on-demand assessment route.

If feeds are unconfigured, all feeds fail without usable articles, or ingestion/database access fails, the worker fails closed and pauses new signal alerts. A reachable feed with no fresh articles is reported as EMPTY; it is not treated as proof of an adverse event.

## Environment

Set these on the backend deployment:

```env
NEWS_RSS_FEED_URLS=https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml
NEWS_POLL_INTERVAL_SECONDS=300
NEWS_MAX_ARTICLE_AGE_MINUTES=90
NIFTY200_CONSTITUENTS_URL=https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv
```

The backend needs network access to the Nifty Indices CSV, configured RSS feeds, Upstox, Telegram and the configured database. Restart/redeploy the backend after changing environment variables.

## Validation still required before risking capital

1. Run the unit tests and CI for the feature branch.
2. Confirm the deployed Upstox account permits the 200-instrument full-feed subscription and that messages contain one-minute OHLC/volume fields.
3. Confirm the NIFTY 200 CSV and Upstox instrument master resolve the expected constituent count.
4. Run in alert-only/paper mode and record every candidate, rejected setup, manual entry, exit, costs and slippage.
5. Backtest with survivorship-bias-aware historical constituents, realistic brokerage/taxes/slippage, and out-of-sample periods. Compare expectancy, profit factor, max drawdown and sample size—not just win rate.
