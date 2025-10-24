"""Data feed abstraction for candle close events."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, DefaultDict, Dict, Iterable, List

from .models import Candle

Callback = Callable[[Candle], None]


@dataclass
class BarFeed:
    """Simple event dispatcher for candle close events."""

    symbol: str

    def __post_init__(self) -> None:
        self._listeners: DefaultDict[str, List[Callback]] = defaultdict(list)

    def register(self, timeframe: str, callback: Callback) -> None:
        self._listeners[timeframe].append(callback)

    def push(self, candle: Candle) -> None:
        for callback in self._listeners.get(candle.timeframe, []):
            callback(candle)

    def replay(self, candles: Iterable[Candle]) -> None:
        for candle in candles:
            self.push(candle)
