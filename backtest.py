"""Backtest runner for the ICT trading agent using historical data."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from zoneinfo import ZoneInfo

from ict_trader import TradingAgent, default_config
from ict_trader.data_sources import (
    MultiTimeframeAggregator,
    YahooFinanceSource,
    dispatch_stream,
    generate_mock_candles,
    load_csv,
)


def run_backtest(args: argparse.Namespace) -> None:
    config = default_config(args.symbol, value_per_point=args.value_per_point)
    agent = TradingAgent.create(config)

    aggregator = MultiTimeframeAggregator(
        symbol=config.symbol,
        base_timeframe=config.base_timeframe,
        target_timeframes=config.all_timeframes(),
    )

    if args.data_source == "csv":
        if not args.csv:
            raise SystemExit("--csv path required for csv data source")
        csv_path = Path(args.csv)
        candles = load_csv(csv_path, config.symbol)
        if all(c.timeframe == config.base_timeframe for c in candles):
            dispatch_stream(agent, candles, aggregator)
        else:
            dispatch_stream(agent, candles)
    elif args.data_source == "mock":
        tz = ZoneInfo(args.timezone)
        start = (
            datetime.fromisoformat(args.start).astimezone(tz)
            if args.start
            else datetime.now(tz=tz).replace(hour=9, minute=30, second=0, microsecond=0)
        )
        candles = generate_mock_candles(config.symbol, start, args.periods, config.base_timeframe)
        dispatch_stream(agent, candles, aggregator)
    else:
        tz = ZoneInfo(args.timezone)
        start = datetime.fromisoformat(args.start).astimezone(tz) if args.start else None
        end = datetime.fromisoformat(args.end).astimezone(tz) if args.end else None
        source = YahooFinanceSource(symbol=config.symbol, interval=config.base_timeframe, start=start, end=end, tz=tz)
        candles = source.fetch()
        dispatch_stream(agent, candles, aggregator)

    summary = agent.logger.summarize_trades()
    results = {
        "symbol": config.symbol,
        "closed_trades": summary["closed_trades"],
        "win_rate": summary["win_rate"],
        "avg_rr": summary["avg_rr"],
        "total_pnl": summary["total_pnl"],
        "max_drawdown": agent.account.max_drawdown,
        "final_equity": agent.account.equity,
        "log_db": config.trade_log_path,
    }

    print("Backtest summary:")
    for key, value in results.items():
        print(f"  {key}: {value}")

    output_dir = Path(config.log_dir) / "backtests"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"{config.symbol}-{timestamp}.json"
    csv_summary_path = output_dir / f"{config.symbol}-{timestamp}.csv"

    json_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    with csv_summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results.keys()))
        writer.writeheader()
        writer.writerow(results)

    print(f"Summary saved to {json_path} and {csv_summary_path}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a backtest using historical candle data.")
    parser.add_argument("--data-source", choices=["csv", "yahoo", "mock"], default="csv")
    parser.add_argument("--csv", help="Path to CSV file when data-source=csv")
    parser.add_argument("--symbol", default="XAUUSD", help="Trading symbol for the backtest")
    parser.add_argument("--value-per-point", type=float, default=1.0, help="Value per point for the instrument")
    parser.add_argument("--start", help="ISO timestamp for start (mock/yahoo)")
    parser.add_argument("--end", help="ISO timestamp for end (yahoo)")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--periods", type=int, default=240)
    return parser


if __name__ == "__main__":
    parser = build_arg_parser()
    run_backtest(parser.parse_args())

