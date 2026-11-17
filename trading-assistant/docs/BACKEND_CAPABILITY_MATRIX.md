# Backend Capability Matrix and Operational Status

This document records what exists in the current branch. It is not a roadmap presented as completed functionality.

## Implemented

| Capability | Current behavior | Caveat |
|---|---|---|
| FastAPI service health | `GET /health`, `GET /api/health` | DB status is a connection check |
| Database integration | PostgreSQL connection health | No trade/news ledger wired into the new modules |
| Upstox config status | Reports whether access token is present | Presence does not prove token validity |
| Upstox REST market data | LTP, full quotes, market status, holidays, intraday and historical candles | Requests require valid token and instrument keys |
| Instrument master | Refresh and NSE equity symbol search | Current scanner list is not the full NIFTY 200 |
| Live scanner | Upstox Market Data Feed V3 WebSocket and reconnect loop | ORB only; experimental |
| ORB candidate alert | Completed 5-minute bar breakout with opening-range volume comparison | One alert per symbol/direction/day; not validated profitability |
| Telegram | Test notification and scanner alert delivery | Configuration must be present |
| News assessment | On-demand official RSS/Atom fetch and risk classification | No continuous worker, persistence, or live scanner integration |
| Intraday risk module | Deterministic rules and tests | Not connected to live positions/orders |
| Backtesting | Deterministic OHLCV CSV strategy research | Current engine serializes one position at a time; not full portfolio simulation |

## Not implemented / not connected

- NIFTY 200 live scanner universe and point-in-time constituent management.
- Live VWAP pullback/rejection and previous-day sweep/reclaim strategy engines.
- Market regime and breadth dashboard feed.
- Sector relative-strength service.
- Time-adjusted RVOL, spread, depth, liquidity and price-impact gates in live scanner.
- Continuous news polling/streaming worker, feed watchdog and event persistence.
- Automatic issuer entity resolution beyond available tags/supplied symbols.
- News-aware historical backtesting with timestamped historical headlines.
- Risk manager integration with scanner, broker positions and execution adapter.
- Automatic square-off / flattening controller.
- Multi-position portfolio backtester with aggregate exposure and risk constraints.
- Live P&L, open positions, fills, trade journal and performance analytics APIs.
- Automatic broker order placement (intentionally disabled).

## Environment variables

- `UPSTOX_ACCESS_TOKEN`: server-side Upstox access token.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: Telegram notification credentials.
- `NEWS_RSS_FEED_URLS`: comma-separated official HTTPS RSS/Atom feed URLs.
- `ORB_SCANNER_SYMBOLS`: comma-separated symbols used by current ORB scanner.

Never put credentials in frontend environment variables or expose them in API responses.

## Operator checks

1. Check `/health` and `/api/health`.
2. Check `/api/integrations/status`; remember configured does not mean validated.
3. Refresh instrument master and confirm all configured symbols resolve.
4. Start scanner only during a controlled paper-testing session.
5. Confirm scanner connection and latest feed timestamp, not merely `running=true`.
6. Run news assessment manually and inspect coverage status, errors and article timestamps.
7. Verify Telegram delivery using the clearly labelled demo notification.
8. Do not use live capital until portfolio risk enforcement, data freshness controls, multi-position backtests and paper-trading validation are complete.

## Safety / operating limits

The target operating policy is intraday only, manual execution, no overnight holdings. The research risk defaults are ₹250 planned risk per trade, ₹500 aggregate planned open risk, max two positions, max three entries/day, ₹750 daily loss stop, entry window 09:30–14:45 IST, and 15:15 IST square-off. These are not currently enforced by a live execution controller. Planned risk is not guaranteed maximum loss because of gaps, slippage, liquidity, and order failures.
