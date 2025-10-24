"""Structure engine implementation."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Deque, Optional

from ..ict_utils import (
    dealing_range,
    detect_fvg,
    fibonacci_zone,
    timeframe_to_minutes,
)
from ..models import Candle, StructureZone, TradeDirection
from ..state_store import StateStore


@dataclass(slots=True)
class StructureEngine:
    """Derives internal liquidity zones from 15 minute candles."""

    store: StateStore
    lookback: int = 40
    timeframe: str = "15m"
    candles: Deque[Candle] = field(default_factory=lambda: deque(maxlen=200))

    def update(self, candle: Candle) -> Optional[StructureZone]:
        if candle.timeframe != self.timeframe:
            return None

        self.candles.append(candle)
        if len(self.candles) < 12:
            return None

        bias = self.store.latest_bias()
        direction = bias.bias if bias else TradeDirection.LONG

        high, low = dealing_range(self.candles, min(self.lookback, len(self.candles)))
        zone_low, zone_high = fibonacci_zone(high, low, direction)

        fvg = detect_fvg(self.candles, direction)
        if fvg:
            if direction is TradeDirection.LONG:
                zone_low = min(zone_low, fvg.low)
                zone_high = max(zone_high, fvg.high)
            else:
                zone_low = min(zone_low, fvg.low)
                zone_high = max(zone_high, fvg.high)

        expires = candle.timestamp + timedelta(minutes=timeframe_to_minutes(self.timeframe) * 4)
        zone = StructureZone(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            generated_at=candle.timestamp,
            direction=direction,
            low=min(zone_low, zone_high),
            high=max(zone_low, zone_high),
            expires_at=expires,
        )
        return self.store.push_structure_zone(zone)

