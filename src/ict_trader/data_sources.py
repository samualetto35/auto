"""Market data source abstractions and helpers."""
from __future__ import annotations

import csv
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

try:
    import requests
except Exception:  # pragma: no cover - requests might be unavailable offline
    requests = None  # type: ignore

from zoneinfo import ZoneInfo

from .ict_utils import floor_time, timeframe_to_minutes
from .models import Candle


@dataclass(slots=True)
class CandleBucket:
    timeframe: str
    include_volume: bool = True
    minutes: int = field(init=False)
    _start: Optional[datetime] = field(init=False, default=None)
    _open: float = field(init=False, default=0.0)
    _high: float = field(init=False, default=float("-inf"))
    _low: float = field(init=False, default=float("inf"))
    _close: float = field(init=False, default=0.0)
    _volume: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "minutes", timeframe_to_minutes(self.timeframe))

    def update(self, candle: Candle) -> Optional[Candle]:
        period_start = floor_time(candle.timestamp, self.timeframe)
        if self._start is None:
            self._start = period_start
            self._open = candle.open
            self._high = candle.high
            self._low = candle.low
            self._close = candle.close
            self._volume = candle.volume
            return None

        if period_start != self._start:
            aggregated = Candle(
                symbol=candle.symbol,
                timeframe=self.timeframe,
                timestamp=self._start,
                open=self._open,
                high=self._high,
                low=self._low,
                close=self._close,
                volume=self._volume,
            )
            self._start = period_start
            self._open = candle.open
            self._high = candle.high
            self._low = candle.low
            self._close = candle.close
            self._volume = candle.volume
            return aggregated

        self._high = max(self._high, candle.high)
        self._low = min(self._low, candle.low)
        self._close = candle.close
        self._volume += candle.volume
        return None

    def finalize(self) -> Optional[Candle]:
        if self._start is None:
            return None
        candle = Candle(
            symbol="",
            timeframe=self.timeframe,
            timestamp=self._start,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=self._volume,
        )
        self._start = None
        return candle


@dataclass(slots=True)
class MultiTimeframeAggregator:
    """Aggregate base timeframe candles into higher timeframes."""

    symbol: str
    base_timeframe: str
    target_timeframes: Sequence[str]
    _ordered_timeframes: List[str] = field(init=False)
    _buckets: Dict[str, CandleBucket] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_ordered_timeframes", sorted(
            set(self.target_timeframes), key=lambda tf: timeframe_to_minutes(tf)
        ))
        if self._ordered_timeframes[0] != self.base_timeframe:
            raise ValueError("Base timeframe must be included in target_timeframes")
        object.__setattr__(
            self,
            "_buckets",
            {
                tf: CandleBucket(tf)
                for tf in self._ordered_timeframes
                if tf != self.base_timeframe
            },
        )

    def add(self, candle: Candle) -> List[Candle]:
        if candle.timeframe != self.base_timeframe:
            raise ValueError(
                f"Aggregator expects base timeframe {self.base_timeframe}, got {candle.timeframe}"
            )

        outputs: List[Candle] = []
        outputs.append(candle)
        for tf, bucket in self._buckets.items():
            result = bucket.update(candle)
            if result:
                outputs.append(
                    Candle(
                        symbol=self.symbol,
                        timeframe=tf,
                        timestamp=result.timestamp,
                        open=result.open,
                        high=result.high,
                        low=result.low,
                        close=result.close,
                        volume=result.volume,
                    )
                )
        return outputs

    def flush(self) -> List[Candle]:
        flushed: List[Candle] = []
        for tf, bucket in self._buckets.items():
            candle = bucket.finalize()
            if candle:
                flushed.append(
                    Candle(
                        symbol=self.symbol,
                        timeframe=tf,
                        timestamp=candle.timestamp,
                        open=candle.open,
                        high=candle.high,
                        low=candle.low,
                        close=candle.close,
                        volume=candle.volume,
                    )
                )
        return flushed


def random_walk(start: float, variance: float = 1.5) -> Iterator[float]:
    price = start
    while True:
        change = random.uniform(-variance, variance)
        price = max(0.1, price + change)
        yield price


def generate_mock_candles(
    symbol: str,
    start: datetime,
    periods: int,
    timeframe: str,
    base_price: float = 2400.0,
) -> List[Candle]:
    step = timedelta(minutes=timeframe_to_minutes(timeframe))
    prices = random_walk(base_price)
    candles: List[Candle] = []
    timestamp = start
    for _ in range(periods):
        open_price = next(prices)
        high = open_price + random.uniform(0.3, 3.0)
        low = open_price - random.uniform(0.3, 3.0)
        close = random.uniform(low, high)
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=timestamp,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=random.uniform(50, 250),
            )
        )
        timestamp += step
    return candles


def load_csv(path: Path, symbol: str) -> List[Candle]:
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        candles: List[Candle] = []
        for row in reader:
            timeframe = row.get("timeframe") or row.get("tf") or "1m"
            timestamp = datetime.fromisoformat(row["timestamp"])
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=timestamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0.0)),
                )
            )
    return sorted(candles, key=lambda c: (c.timestamp, timeframe_to_minutes(c.timeframe)))


@dataclass(slots=True)
class YahooFinanceSource:
    symbol: str
    interval: str = "1m"
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    tz: ZoneInfo = ZoneInfo("UTC")
    session_sleep: float = 60.0

    def _query_params(self) -> dict:
        params: dict[str, str] = {"interval": self.interval}
        if self.start and self.end:
            params["period1"] = str(int(self.start.timestamp()))
            params["period2"] = str(int(self.end.timestamp()))
        else:
            params["range"] = "7d"
        return params

    def fetch(self) -> List[Candle]:
        if requests is None:
            raise RuntimeError("requests library not available; cannot fetch real data")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{self.symbol}"
        response = requests.get(url, params=self._query_params(), timeout=10)
        response.raise_for_status()
        payload = response.json()
        result = payload.get("chart", {}).get("result")
        if not result:
            raise RuntimeError(f"No data returned for {self.symbol}")
        data = result[0]
        timestamps = data.get("timestamp", [])
        quote = data.get("indicators", {}).get("quote", [{}])[0]
        candles: List[Candle] = []
        for idx, ts in enumerate(timestamps):
            timestamp = datetime.fromtimestamp(ts, tz=ZoneInfo("UTC")).astimezone(self.tz)
            open_price = float(quote.get("open", [None])[idx])
            high = float(quote.get("high", [None])[idx])
            low = float(quote.get("low", [None])[idx])
            close = float(quote.get("close", [None])[idx])
            volume = float(quote.get("volume", [0])[idx] or 0.0)
            if any(value is None for value in (open_price, high, low, close)):
                continue
            candles.append(
                Candle(
                    symbol=self.symbol,
                    timeframe=self.interval,
                    timestamp=timestamp,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
            )
        return candles

    def stream(self) -> Iterator[Candle]:  # pragma: no cover - requires network
        last_timestamp: Optional[int] = None
        while True:
            try:
                candles = self.fetch()
            except Exception as exc:  # pragma: no cover
                print(f"[WARN] Failed to fetch {self.symbol}: {exc}")
                time.sleep(self.session_sleep)
                continue
            for candle in candles:
                epoch = int(candle.timestamp.timestamp())
                if last_timestamp and epoch <= last_timestamp:
                    continue
                last_timestamp = epoch
                yield candle
            time.sleep(self.session_sleep)


def dispatch_stream(
    agent,
    candles: Iterable[Candle],
    aggregator: Optional[MultiTimeframeAggregator] = None,
) -> None:
    """Feed candles (optionally aggregated) into the trading agent."""

    for candle in candles:
        if aggregator:
            if candle.timeframe != aggregator.base_timeframe:
                agent.on_candle(candle)
                continue
            for derived in aggregator.add(candle):
                agent.on_candle(derived)
        else:
            agent.on_candle(candle)

    if aggregator:
        for candle in aggregator.flush():
            agent.on_candle(candle)

