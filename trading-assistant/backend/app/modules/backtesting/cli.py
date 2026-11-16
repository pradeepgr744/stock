from __future__ import annotations

import argparse
import json

from app.modules.backtesting.engine import (
    BacktestConfig,
    CostModel,
    compare_strategies,
    load_bars,
    run_backtest,
    write_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="NSE equity intraday historical backtester")
    parser.add_argument("--input", required=True, help="5-minute OHLCV CSV")
    parser.add_argument("--strategy", choices=["orb", "vwap", "sweep", "all"], default="all")
    parser.add_argument("--output", default="backtest-report.json")
    parser.add_argument("--capital", type=float, default=50_000)
    parser.add_argument("--risk-pct", type=float, default=0.5)
    parser.add_argument("--max-trades-per-day", type=int, default=3)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--brokerage-per-order", type=float, default=20.0)
    parser.add_argument("--buy-cost-bps", type=float, required=True, help="Buy-side statutory + exchange costs from broker contract note")
    parser.add_argument("--sell-cost-bps", type=float, required=True, help="Sell-side statutory + exchange costs from broker contract note")
    args = parser.parse_args()

    if args.capital <= 0 or not 0 < args.risk_pct <= 2 or args.max_trades_per_day < 1:
        parser.error("capital must be positive; risk-pct must be in (0, 2]; max-trades-per-day must be >= 1")
    if min(args.slippage_bps, args.brokerage_per_order, args.buy_cost_bps, args.sell_cost_bps) < 0:
        parser.error("cost inputs cannot be negative")

    bars = load_bars(args.input)
    config = BacktestConfig(
        initial_capital=args.capital,
        risk_pct=args.risk_pct,
        max_trades_per_day=args.max_trades_per_day,
    )
    costs = CostModel(
        buy_cost_bps=args.buy_cost_bps,
        sell_cost_bps=args.sell_cost_bps,
        slippage_bps_per_side=args.slippage_bps,
        brokerage_per_order=args.brokerage_per_order,
    )
    if args.strategy == "all":
        report = compare_strategies(bars, config, costs)
    else:
        report = run_backtest(bars, [args.strategy], config, costs)
    write_report(report, args.output)
    if "results" in report:
        summary = {
            name: result["metrics"]
            for name, result in report["results"].items()
        }
        print(json.dumps({"ranking": report["ranking_by_observed_expectancy_r"], "metrics": summary}, indent=2))
    else:
        print(json.dumps(report["metrics"], indent=2))
    print(f"Report saved to {args.output}")


if __name__ == "__main__":
    main()
