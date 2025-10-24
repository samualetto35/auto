"""Structure engine implementation."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Deque

from ..models import Candle, StructureZone, TradeDirection
from ..state_store import StateStore


@dataclass(slots=True)
class StructureEngine:
    """Derives internal liquidity zones from 15 minute candles."""

    store: StateStore
    lookback: int = 40
    candles: Deque[Candle] = field(default_factory=lambda: deque(maxlen=100))

    def update(self, candle: Candle) -> StructureZone | None:
        self.candles.append(candle)
        if len(self.candles) < 10:
            return None

        highs = [c.high for c in self.candles][-self.lookback :]
        lows = [c.low for c in self.candles][-self.lookback :]
        avg_range = (sum(h - l for h, l in zip(highs, lows)) / len(highs)) if highs else 0
        mid_price = (max(highs) + min(lows)) / 2

        last_bias = self.store.latest_bias()
        direction = last_bias.bias if last_bias else TradeDirection.LONG

        if direction is TradeDirection.LONG:
            zone_high = mid_price - avg_range * 0.1
            zone_low = zone_high - avg_range * 0.5
        else:
            zone_low = mid_price + avg_range * 0.1
            zone_high = zone_low + avg_range * 0.5

        zone = StructureZone(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            generated_at=candle.timestamp,
            direction=direction,
            low=min(zone_low, zone_high),
            high=max(zone_low, zone_high),
            expires_at=candle.timestamp + timedelta(minutes=60),
        )
        return self.store.push_structure_zone(zone)

