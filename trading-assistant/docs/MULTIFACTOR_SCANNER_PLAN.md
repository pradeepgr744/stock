# Multi-factor NSE scanner: research and integration plan

## Status

The `app.modules.strategy.multi_factor` module is a pure, experimental setup
evaluator. It is **not yet connected** to the Upstox live scanner, the API,
Telegram delivery, or persistent signal storage. The existing ORB scanner
remains the live scanner until those integrations are implemented and tested.

Its integer `quality_score` is a transparent rule score, not a probability,
confidence interval, or measured win rate. Do not display it as a win chance.

## Candidate tactics

- VWAP pullback continuation: trend regime, sector alignment, pullback toward
  session VWAP, then a completed close through the prior candle high/low.
- Previous-day high/low sweep and reclaim: price trades through the prior-day
  extreme and closes back inside with rejection.
- ORB: retain as a baseline comparator, not a presumed edge.
- Later experiments: volatility contraction/expansion and sector-relative
  strength. Add only after the baseline strategies are reproducibly tested.

## Required inputs

The evaluator expects completed 5-minute OHLCV candles for the current session,
at least 20 bars, a separately calculated market regime, sector direction,
liquidity eligibility, and time-adjusted RVOL.

Time-adjusted RVOL must compare a candle's volume with the same time-of-day
bucket across prior sessions. Dividing the current candle volume by the
opening-range average is not an acceptable substitute. Regime and sector data
must use synchronized completed candles. Missing or stale inputs should veto
new alerts.

## Before live integration

1. Build a point-in-time NIFTY 200 universe from official index constituents
   and current NSE instrument identifiers; do not assume a static list remains
   current.
2. Build a historical store for adjusted corporate-action-aware OHLCV, sector
   indices, benchmark index, spreads/quotes where available, and trading
   calendar.
3. Backtest each tactic independently, then test combined ranking. Use
   chronological train/validation/test splits and walk-forward evaluation.
4. Include brokerage, STT, exchange charges, GST, stamp duty, slippage, spread,
   rejected orders, and realistic entry delay. Use conservative fill assumptions.
5. Report trade count, expectancy in R, profit factor, win rate with confidence
   interval, max drawdown, adverse/favourable excursion, turnover, and
   performance by year, regime, sector, and time bucket.
6. Prevent look-ahead and survivorship bias. Use point-in-time index membership
   and only information available at the signal timestamp.
7. Paper trade on live data before considering any real-money use.

## Selection and risk policy

Rank eligible candidates by out-of-sample validated expected value and risk,
not by a hand-tuned score alone. A daily maximum of three alerts is a ceiling,
not a quota: zero alerts is the correct result when no candidate passes.
Enforce one open idea per symbol, a daily portfolio loss limit, a maximum
aggregate risk budget, and a no-new-entry cutoff. Position size must be
calculated from account risk budget divided by stop distance plus estimated
costs; do not hard-code share quantity.

## Reliability requirements

The current in-memory scanner state is lost on Render restarts. Before live
deployment, persist signals, alert idempotency keys, scanner session state,
and outcomes in PostgreSQL. Use a database uniqueness constraint to prevent
duplicate alerts and an outbox/retry mechanism for Telegram delivery. A
UptimeRobot ping only prevents neither process restarts nor WebSocket
disconnects; implement reconnect, heartbeat, stale-feed detection, and
operator-visible degraded status. Do not run multiple scanner workers against
the same account without a distributed lease.

## API / dashboard contract to add later

Each candidate should expose: symbol, timestamp, direction, tactic, setup
score (clearly labelled non-probabilistic), entry reference, invalidation,
target scenarios, estimated costs, regime, sector alignment, RVOL baseline
window, reasons, data freshness, and validation version. Outcomes should be
recorded independently from the signal generation process.
