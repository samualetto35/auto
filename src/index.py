"""Entry point demonstrating the ICT inspired trading agent."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_trader import TradingAgent, default_config
from ict_trader.models import Candle


def generate_mock_candles(symbol: str, start: datetime, periods: int) -> list[Candle]:
    candles: list[Candle] = []
    price = 2400.0
    for i in range(periods):
        timestamp = start + timedelta(minutes=i)
        open_price = price
        high = open_price + random.uniform(0.5, 3.0)
        low = open_price - random.uniform(0.5, 3.0)
        close = random.uniform(low, high)
        price = close
        candles.append(Candle(symbol=symbol, timeframe="1m", timestamp=timestamp, open=open_price, high=high, low=low, close=close))
        if i % 5 == 0:
            candles.append(Candle(symbol=symbol, timeframe="5m", timestamp=timestamp, open=open_price, high=high, low=low, close=close))
        if i % 15 == 0:
            candles.append(Candle(symbol=symbol, timeframe="15m", timestamp=timestamp, open=open_price, high=high, low=low, close=close))
        if i % 60 == 0:
            candles.append(Candle(symbol=symbol, timeframe="1h", timestamp=timestamp, open=open_price, high=high, low=low, close=close))
    return candles


def main() -> None:
    config = default_config("XAUUSD", value_per_point=1.0)
    agent = TradingAgent.create(config)

    start = datetime.now(tz=ZoneInfo("America/New_York")).replace(hour=10, minute=0, second=0, microsecond=0)
    candles = generate_mock_candles(config.symbol, start, periods=120)

    agent.replay(candles)


if __name__ == "__main__":
    main()
