# Intraday Decision Engine — Research and Risk Design

## Objective

Optimize **net realized intraday P&L after all costs**, subject to explicit drawdown, daily-loss, liquidity, and execution constraints. Do not optimize raw win rate or headline accuracy. A model that predicts direction correctly but cannot capture enough movement after spread, slippage, brokerage, STT, exchange charges, GST, SEBI charges, stamp duty, and other applicable costs is not useful.

The system is a decision-support tool. Execution remains manual. No position may be carried overnight.

## Decision pipeline

1. **Data integrity gate** — confirm feed freshness, exchange session state, candle completeness, instrument mapping, corporate-action handling, and timestamp alignment. Stale or incomplete data means no new entry.
2. **Market regime** — classify NIFTY 50 / NIFTY 200 context as trend-up, trend-down, range, high-volatility, or event-dislocated. Use completed bars only. Use breadth, advance/decline, index VWAP, opening range, realized volatility, and India VIX as separate inputs; do not let one indicator decide regime.
3. **Sector confirmation** — compare the stock with its point-in-time sector index and peers. Require sector relative strength to confirm directional setups, but allow a clearly defined exception only after backtesting.
4. **Stock microstructure** — time-adjusted RVOL, spread, traded value, depth imbalance, price impact, ATR, gap size, distance from VWAP, opening-range structure, previous-day levels, and circuit proximity. Reject illiquid names even when their chart pattern looks attractive.
5. **Catalyst and news risk** — inspect official exchange/company/regulator disclosures and configured official RSS feeds. Tag issuer-specific versus market-wide events, publication time, source, affected symbols, and event severity. A verified, fresh market-disruption or high-impact event can pause new entries. Third-party or unverified headlines are warnings, not hard directional signals.
6. **Strategy setup** — evaluate ORB, VWAP pullback/rejection, and previous-day sweep/reclaim only when their market regime and sector context are appropriate.
7. **Cost-aware trade plan** — calculate entry, stop, target, quantity, estimated round-trip charges, expected slippage, and minimum net reward/risk. Reject a setup whose expected move is not large enough after costs.
8. **Portfolio risk gate** — ₹250 planned risk per trade, ₹500 aggregate planned open risk, maximum two concurrent positions, three new entries per day, ₹750 daily loss stop, and 15:15 IST mandatory square-off. A risk breach stops new entries; the execution controller must flatten positions as required.
9. **Manual confirmation** — show the evidence, source timestamps, invalidation level, cost estimate, and reasons for rejection. Never silently convert an alert into an order.
10. **Post-trade learning** — persist candidate, accepted/rejected decision, fills, actual contract-note charges, slippage, exit, and outcome. Re-evaluate by strategy, symbol, sector, time bucket, regime, and catalyst class.

## Portfolio risk gate

The broker-agnostic risk manager is in app/modules/risk/intraday.py. Defaults: ₹250 planned risk per trade, ₹500 aggregate open planned risk, maximum two concurrent positions, maximum three entries per day, ₹750 daily loss stop, entries only from 09:30 through 14:45 IST, and mandatory square-off at 15:15 IST. It evaluates realized plus marked unrealized P&L and rejects entries when limits are breached. It does not submit orders; a broker/execution adapter must keep its position state synchronized and execute flatten instructions. The historical backtester currently remains a serialized single-position simulation, so the portfolio limits are not yet reflected in historical combined-strategy results.

## News and sentiment policy

### Source hierarchy

- Highest priority: NSE/BSE corporate announcements, company exchange filings, RBI, SEBI, government and regulator releases.
- Secondary: licensed/reputable financial news feeds with a known publication timestamp and original article URL.
- Social posts and anonymous forwards: unverified context only. Do not treat them as trade signals. Do not scrape private groups or assume a viral post is true.

The first implementation supports configurable RSS/Atom feeds from official domains only. Configure NEWS_RSS_FEED_URLS in backend/.env with comma-separated HTTPS feed URLs copied from the official publisher pages. The endpoint is POST /api/research/news/assess with an optional symbols array. The endpoint fetches configured feeds when called; it is **not yet a continuous background news monitor**.

NSE describes its RSS feeds as a way to receive corporate information, announcements, board meetings, corporate actions and other updates. Feed delivery may be delayed by intermediaries, so a feed cannot guarantee instant detection.

### Event handling

- Fresh official market-wide disruption / high-impact event: set BLOCKED, stop all new entries and require human review.
- Fresh official issuer-specific suspension/default/ban with a tagged symbol: block that symbol only; do not halt unrelated shares.
- Fresh official issuer-specific material event with a symbol tag: block that symbol until the original filing and price reaction are checked.
- Third-party headline or unverified social claim: CAUTION only. Confirm with an official filing or a second independent reputable source.
- Stale or future-dated item: do not score it as fresh news.
- Conflicting headlines: do not average them into a confident prediction; mark the event ambiguous and require review.

The current keyword sentiment is deliberately a baseline. It is **not** a trained financial-language model, not calibrated, and not a probability of price direction. It does not infer whether an event is already priced in. It should initially be used to block or flag risk, not to add points to a setup.

## Sudden-news response

The live system still needs a continuous feed consumer and an event-driven risk controller. When added, it must:
- poll/stream configured sources with source timestamps and deduplicate by canonical URL/article ID;
- persist every raw headline and parsed assessment for audit;
- alert Telegram and dashboard with source, timestamp, affected symbols, severity, and original link;
- pause new entries when a verified high-impact event is detected;
- re-check open positions against current prices and broker RMS constraints;
- never assume a news alert guarantees a safe exit price or immediate order fill;
- recover from feed outage by displaying NEWS_FEED_STALE and blocking new entries if news coverage is a required gate.

A headline detector cannot prevent all gap moves or exchange halts. Stops are not guaranteed execution prices.

## Model and validation requirements

Do not deploy a neural network or LLM "prediction" until a transparent baseline has been tested. Start with rule-based factors and then compare:
- baseline technical strategy;
- technical + market regime;
- technical + regime + sector;
- technical + regime + sector + news/event filter;
- full model with liquidity, volatility and cost filters.

Use chronological train/validation/test splits, walk-forward evaluation, point-in-time NIFTY 200 membership, and event-time data. Never randomly shuffle time series. Never use article corrections, final-day constituents, revised fundamentals, or future candle values before they were available.

Report net expectancy, profit factor, max drawdown, daily loss breaches, average net win/loss, trade count, turnover, costs as a share of gross P&L, exposure time, missed trades, and results by regime/sector/time/catalyst. Include confidence intervals and a paper-trading period. A single backtest or a high win rate is not evidence of durable edge.

## Current implementation status

- Added deterministic news/event assessment and freshness/deduplication.
- Added official-source domain trust classification, market-wide pause rules, and issuer-specific symbol blocks.
- News coverage reports UNAVAILABLE and fails closed if no official feeds are configured or all configured feeds fail.
- Added an RSS/Atom fetcher restricted to HTTPS feeds on the official NSE/BSE/SEBI/RBI/PIB/MCA allowlist.
- Added POST /api/research/news/assess and NEWS_RSS_FEED_URLS.
- Added unit tests for official vs unverified sources, freshness, duplicate URLs, future timestamps, and basic sentiment.

Not implemented yet:
- No feed URLs are preconfigured; operator must copy them from official publisher pages.
- No continuous background polling, webhook or streaming news service.
- No automatic issuer-to-symbol entity resolution beyond feed category tags and supplied symbols.
- No persistence of raw articles or assessments to Neon yet.
- No integration with the live ORB scanner or intraday portfolio risk manager.
- No news-aware historical backtest; historical timestamped news dataset is required.
- No trained sentiment model, probability calibration, or price prediction.
- No claim of optimized profitability; actual historical tests and paper-trading evidence are still required.

## Official references

- NSE RSS feeds: https://www.nseindia.com/static/rss-feed
- Upstox API overview: https://upstox.com/developer/api-documentation
- Upstox Market Data Feed V3: https://upstox.com/developer/api-documentation/v3/get-market-data-feed/
- Upstox full market quotes: https://upstox.com/developer/api-documentation/get-full-market-quote/
- Upstox historical candle V3: https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/
