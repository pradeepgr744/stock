# Historical backtesting (NSE cash intraday)

## What is implemented

- CSV loader for 5-minute OHLCV, timestamp-normalized to Asia/Kolkata.
- Three transparent candidate strategies: opening-range breakout, VWAP pullback continuation, and previous-day high/low sweep-reclaim.
- Signals are evaluated only after a completed candle and filled at the next candle open.
- Conservative same-candle ambiguity: if stop and target are both touched, stop wins.
- Risk sizing starts at ₹50,000 equity and 0.5% risk (₹250 initial planned risk), capped by available cash notional.
- Intraday long and short positions are force-closed using the 15:15 IST bar; no overnight positions are permitted.
- The backtest engine is still serialized (one position at a time), with a maximum of three trades per day and one trade per symbol per day. The separate portfolio risk gate defines the intended two-position / ₹500 aggregate-risk limits, but is not yet wired into this simulator or the live scanner.
- Costs include user-supplied buy-side/sell-side statutory and exchange bps, configurable slippage, and capped per-order brokerage.
- Reports include net P&L, win rate, profit factor, expectancy in rupees and R, max drawdown, and total modeled costs.

This is a research harness, not a claim that any tactic is profitable. The strategy rules are deliberately simple baselines and have not been optimized.

## CSV format

Required columns:

`timestamp,symbol,open,high,low,close,volume`

Timestamp should be ISO 8601 with timezone, preferably IST, for example:

`2026-01-05T09:15:00+05:30,RELIANCE,1500,1505,1498,1503,125000`

Use 5-minute candles, not 1-minute candles. Data must include multiple prior sessions for sweep levels and should include the full 09:15-15:25 session. Use point-in-time NIFTY 200 membership to avoid survivorship bias; do not backtest today's constituents across years without membership history. Adjust or explicitly handle splits, bonuses, symbol changes, and delistings. Exclude stale, missing, duplicate, and invalid candles.

## Costs are mandatory

Brokerage and taxes vary by broker, date, turnover, and trade side. The CLI requires buy-side and sell-side statutory/exchange costs in basis points from the current broker tariff/contract note. Slippage defaults to 5 bps per side and brokerage to ₹20 per order capped at 0.03% of order turnover; change these to match actual execution and run sensitivity tests at worse slippage. Do not interpret a report with incomplete charges as net profitability.

Example (replace buy/sell bps with your actual calculated rates):

```powershell
cd trading-assistant/backend
python -m app.modules.backtesting.cli --input data/nifty200_5m.csv --strategy all --capital 50000 --risk-pct 0.5 --max-trades-per-day 3 --slippage-bps 5 --brokerage-per-order 20 --buy-cost-bps 1.0 --sell-cost-bps 4.0 --output reports/compare.json
```

The example cost rates are placeholders only, not a statement of current NSE charges or a broker quote.

## Validation protocol before trusting a result

1. Use point-in-time constituents and corporate-action-aware data.
2. Split chronologically: development, validation, and untouched final test period. Never randomly shuffle time series.
3. Tune only on development data; freeze parameters before validation/test.
4. Include brokerage, exchange transaction charges, SEBI charges, GST, STT, stamp duty, slippage, and spread. Avoid double counting when converting contract-note amounts to bps.
5. Run sensitivity at 1x, 1.5x, and 2x modeled slippage and costs.
6. Report sample size and confidence interval for win rate; group results by year, symbol, sector, time-of-day, and market regime.
7. Compare each strategy with buy-and-hold is not appropriate for intraday; use no-trade and simple benchmark strategies and compare expectancy / drawdown.
8. Paper trade with live timestamps before any real-money use.

## Intraday risk gate

`app.modules.risk.intraday.IntradayRiskManager` is a broker-agnostic pre-trade gate. Its defaults are ₹250 planned risk per trade, ₹500 aggregate open planned risk, two concurrent positions, three entries per day, and a ₹750 daily loss stop based on realized plus marked unrealized P&L. It rejects new entries after 14:45 IST and requests flattening at 15:15 IST or when the daily loss threshold is breached. The caller must invoke `must_flatten`, send actual broker fills/marks/exits to the manager, and execute the resulting square-off through the broker adapter. It does not place orders and does not protect an account unless integrated with live order and position state. A stop gap or execution delay can exceed planned risk.

## Known limitations

- No Upstox bulk historical downloader or NIFTY 200 constituent-history importer is wired in yet. Supply a clean CSV.
- Current sweep baseline uses the immediately preceding session's high/low per symbol.
- VWAP baseline is intraday session VWAP, without a sector/index regime filter or time-adjusted RVOL.
- The initial simulator serializes trades (one open position at a time) to avoid pretending ₹50k can fund unlimited concurrent positions. It does not model broker margin for intraday shorts.
- The max-three daily cap is a ceiling, never a quota. The system can and should produce zero trades.
- The ranking is descriptive on the supplied sample, not out-of-sample evidence or a calibrated probability.
- This backtester does not connect to live alerts or execute orders.
