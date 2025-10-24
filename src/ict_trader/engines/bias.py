"""Bias engine implementation."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean
from typing import Deque, Iterable

from ..models import BiasSnapshot, Candle, TradeDirection
from ..state_store import StateStore


@dataclass(slots=True)
class BiasEngine:
    """Generates directional bias from hourly candles."""

    store: StateStore
    lookback: int = 150
    confidence_floor: float = 0.55
    candles: Deque[Candle] = field(default_factory=lambda: deque(maxlen=200))

    def update(self, candle: Candle) -> BiasSnapshot:
        self.candles.append(candle)
        if len(self.candles) < 5:
            bias = TradeDirection.LONG if candle.close >= candle.open else TradeDirection.SHORT
            confidence = 0.5
            target = candle.close
            invalidate = candle.low if bias is TradeDirection.LONG else candle.high
        else:
            closes = [c.close for c in self.candles][-self.lookback :]
            highs = [c.high for c in self.candles][-self.lookback :]
            lows = [c.low for c in self.candles][-self.lookback :]
            ema_short = self._ema(closes, span=10)
            ema_long = self._ema(closes, span=30)
            bias = TradeDirection.LONG if ema_short >= ema_long else TradeDirection.SHORT
            swing_high = max(highs)
            swing_low = min(lows)
            target = swing_high if bias is TradeDirection.LONG else swing_low
            invalidate = swing_low if bias is TradeDirection.LONG else swing_high
            recent_momentum = closes[-1] - closes[-5]
            confidence = min(0.95, max(self.confidence_floor, abs(recent_momentum) / max(1e-6, mean([abs(c) for c in closes[-10:]]))))

        snapshot = BiasSnapshot(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            generated_at=candle.timestamp,
            bias=bias,
            confidence=float(confidence),
            target_price=target,
            invalidate_below=invalidate,
        )
        return self.store.push_bias(snapshot)

    @staticmethod
    def _ema(values: Iterable[float], span: int) -> float:
        values = list(values)
        if not values:
            return 0.0
        alpha = 2 / (span + 1)
        ema = values[0]
        for value in values[1:]:
            ema = alpha * value + (1 - alpha) * ema
        return ema
