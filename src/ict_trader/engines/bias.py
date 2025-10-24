"""Bias engine implementation."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Sequence

from ..ict_utils import dealing_range, displacement_strength, last_structure_break
from ..models import BiasSnapshot, Candle, TradeDirection
from ..state_store import StateStore


@dataclass(slots=True)
class BiasEngine:
    """Generates directional bias from hourly candles."""

    store: StateStore
    lookback: int = 150
    primary_timeframe: str = "1h"
    context_timeframes: Sequence[str] = field(default_factory=lambda: ("4h", "1d"))
    confidence_floor: float = 0.55
    candles: Dict[str, Deque[Candle]] = field(default_factory=dict)

    def update(self, candle: Candle) -> Optional[BiasSnapshot]:
        buffer = self.candles.setdefault(candle.timeframe, deque(maxlen=max(self.lookback, 300)))
        buffer.append(candle)

        if candle.timeframe != self.primary_timeframe:
            return None

        primary = list(buffer)
        if len(primary) < 10:
            direction = TradeDirection.LONG if candle.close >= candle.open else TradeDirection.SHORT
            target = candle.high if direction is TradeDirection.LONG else candle.low
            invalidate = candle.low if direction is TradeDirection.LONG else candle.high
            confidence = 0.5
        else:
            high, low = dealing_range(primary, min(self.lookback, len(primary)))
            midpoint = (high + low) / 2
            structure_bias = last_structure_break(primary)
            direction = structure_bias or (
                TradeDirection.LONG if primary[-1].close >= midpoint else TradeDirection.SHORT
            )
            target = high if direction is TradeDirection.LONG else low
            invalidate = low if direction is TradeDirection.LONG else high

            for timeframe in self.context_timeframes:
                context = self.candles.get(timeframe)
                if not context:
                    continue
                ctx = list(context)[-self.lookback :]
                if not ctx:
                    continue
                ctx_high = max(c.high for c in ctx)
                ctx_low = min(c.low for c in ctx)
                if direction is TradeDirection.LONG:
                    target = max(target, ctx_high)
                    invalidate = min(invalidate, ctx_low)
                else:
                    target = min(target, ctx_low)
                    invalidate = max(invalidate, ctx_high)

            range_size = max(high - low, 1e-6)
            displacement = displacement_strength(primary, lookback=8)
            premium_discount = abs(primary[-1].close - midpoint) / range_size
            confidence = min(0.99, max(self.confidence_floor, premium_discount + displacement / range_size))

        snapshot = BiasSnapshot(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            generated_at=candle.timestamp,
            bias=direction,
            confidence=float(confidence),
            target_price=target,
            invalidate_below=invalidate,
        )
        return self.store.push_bias(snapshot)
