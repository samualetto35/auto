"""CLI entrypoint for the ICT-inspired trading agent."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from zoneinfo import ZoneInfo

from ict_trader import TradingAgent, default_config
from ict_trader.config import AgentConfig
from ict_trader.data_sources import (
    MultiTimeframeAggregator,
    YahooFinanceSource,
    dispatch_stream,
    generate_mock_candles,
    load_csv,
)
from ict_trader.models import Candle


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the ICT trading agent in simulation mode.")
    parser.add_argument("--symbol", default="XAUUSD", help="Trading symbol")
    parser.add_argument("--value-per-point", type=float, default=1.0, help="Instrument value per point")
    parser.add_argument(
        "--data-source",
        choices=["mock", "csv", "yahoo"],
        default="mock",
        help="Data source for candles",
    )
    parser.add_argument("--csv", help="Path to CSV file when --data-source=csv")
    parser.add_argument("--start", help="ISO timestamp for data start (used by mock/yahoo)")
    parser.add_argument("--end", help="ISO timestamp for data end (yahoo only)")
    parser.add_argument("--periods", type=int, default=240, help="Number of mock candles to generate")
    parser.add_argument("--timezone", default="America/New_York", help="Session timezone")
    parser.add_argument("--live", action="store_true", help="Stream live data from Yahoo Finance")
    parser.add_argument("--initial-equity", type=float, default=100000.0, help="Starting account equity")
    return parser.parse_args(argv)


def build_agent(config: AgentConfig, initial_equity: float) -> TradingAgent:
    agent = TradingAgent.create(config)
    agent.account.equity = initial_equity
    agent.account.starting_equity = initial_equity
    return agent


def load_mock_data(args: argparse.Namespace, config: AgentConfig) -> Iterable[Candle]:
    tz = ZoneInfo(args.timezone)
    start = (
        datetime.fromisoformat(args.start).astimezone(tz)
        if args.start
        else datetime.now(tz=tz).replace(hour=9, minute=30, second=0, microsecond=0)
    )
    return generate_mock_candles(
        symbol=config.symbol,
        start=start,
        periods=args.periods,
        timeframe=config.base_timeframe,
        base_price=2400.0,
    )


def load_yahoo_data(args: argparse.Namespace, config: AgentConfig) -> Iterable[Candle]:
    tz = ZoneInfo(args.timezone)
    start = datetime.fromisoformat(args.start).astimezone(tz) if args.start else None
    end = datetime.fromisoformat(args.end).astimezone(tz) if args.end else None
    source = YahooFinanceSource(symbol=config.symbol, interval=config.base_timeframe, start=start, end=end, tz=tz)
    if args.live:
        return source.stream()
    return source.fetch()


def run(args: argparse.Namespace) -> None:
    config = default_config(args.symbol, value_per_point=args.value_per_point)
    config.data_source = args.data_source
    config.timezone = args.timezone

    agent = build_agent(config, args.initial_equity)
    aggregator = MultiTimeframeAggregator(
        symbol=config.symbol,
        base_timeframe=config.base_timeframe,
        target_timeframes=config.all_timeframes(),
    )

    if args.data_source == "mock":
        candles = load_mock_data(args, config)
        dispatch_stream(agent, candles, aggregator)
    elif args.data_source == "csv":
        if not args.csv:
            raise SystemExit("--csv path is required when data-source=csv")
        csv_path = Path(args.csv)
        candles = load_csv(csv_path, config.symbol)
        if all(c.timeframe == config.base_timeframe for c in candles):
            dispatch_stream(agent, candles, aggregator)
        else:
            dispatch_stream(agent, candles)
    else:
        candles = load_yahoo_data(args, config)
        if args.live:
            try:
                dispatch_stream(agent, candles, aggregator)
            except KeyboardInterrupt:
                print("\nLive stream stopped by user")
        else:
            dispatch_stream(agent, candles, aggregator)

    summary = agent.logger.summarize_trades()
    print("\n=== Simulation Summary ===")
    print(f"Equity: {agent.account.equity:.2f} {config.account_currency}")
    print(f"Closed trades: {summary['closed_trades']}")
    print(f"Win rate: {summary['win_rate'] * 100:.1f}%")
    print(f"Total PnL: {summary['total_pnl']:.2f}")
    print(f"Average R:R: {summary['avg_rr']:.2f}")
    print(f"Log database: {config.trade_log_path}")


def main(argv: List[str] | None = None) -> None:
    args = parse_args(argv or sys.argv[1:])
    run(args)


if __name__ == "__main__":
    main()
