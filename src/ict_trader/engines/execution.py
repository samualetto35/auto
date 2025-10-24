"""Execution engine implementing ICT style confirmations."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Optional

from ..models import Candle, ExecutionSignal, StructureZone, TradeDirection
from ..state_store import StateStore


@dataclass(slots=True)
class ExecutionEngine:
    """Examines 1m/5m candles for sweeps → MSS → FVG patterns."""

    store: StateStore
    lookback: int = 50
    rr_target: float = 2.0
    candles: Deque[Candle] = field(default_factory=lambda: deque(maxlen=120))

    def evaluate(self, candle: Candle) -> Optional[ExecutionSignal]:
        self.candles.append(candle)
        if len(self.candles) < 5:
            return None

        bias = self.store.latest_bias()
        structure = self._valid_structure(candle.timestamp)
        if not bias or not structure:
            return None

        if structure.direction != bias.bias:
            return None

        if not structure.contains(candle.close):
            return None

        sweep_direction = self._detect_liquidity_sweep()
        if not sweep_direction or sweep_direction != bias.bias:
            return None

        displacement, fvg_bounds = self._detect_displacement_and_fvg()
        if displacement is None:
            return None

        entry, stop, target = self._derive_orders(bias.bias, fvg_bounds, candle.close, bias.target_price)
        if entry is None or stop is None or target is None:
            return None

        rr = abs(target - entry) / max(1e-6, abs(entry - stop))
        if rr < self.rr_target:
            return None

        signal = ExecutionSignal(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            generated_at=candle.timestamp,
            direction=bias.bias,
            entry=entry,
            stop=stop,
            target=target,
            rr=rr,
            bias_snapshot_id=bias.id,
            structure_zone_id=structure.id,
            reason=f"Sweep+MSS+FVG ({candle.timeframe})",
        )
        return self.store.push_execution_signal(signal)

    def _valid_structure(self, now: datetime) -> Optional[StructureZone]:
        structure = self.store.latest_structure()
        if not structure:
            return None
        if structure.expires_at and structure.expires_at < now:
            return None
        return structure

    def _detect_liquidity_sweep(self) -> Optional[TradeDirection]:
        candles = list(self.candles)
        recent = candles[-3:]
        prev_range_high = max(c.high for c in candles[:-3]) if len(candles) > 3 else recent[-1].high
        prev_range_low = min(c.low for c in candles[:-3]) if len(candles) > 3 else recent[-1].low

        sweep_up = any(c.high > prev_range_high for c in recent)
        sweep_down = any(c.low < prev_range_low for c in recent)

        if sweep_up and sweep_down:
            return None
        if sweep_up:
            return TradeDirection.SHORT
        if sweep_down:
            return TradeDirection.LONG
        return None

    def _detect_displacement_and_fvg(self) -> tuple[Optional[float], Optional[tuple[float, float]]]:
        candles = list(self.candles)
        c1, c2, c3 = candles[-3:]
        displacement = c2.body
        avg_body = sum(c.body for c in candles[-6:-1]) / 5 if len(candles) >= 6 else displacement
        if displacement < avg_body * 1.2:
            return None, None

        if c1.direction is TradeDirection.LONG and c2.direction is TradeDirection.LONG:
            gap_low = max(c1.high, c2.open)
            gap_high = min(c2.low, c3.low)
        elif c1.direction is TradeDirection.SHORT and c2.direction is TradeDirection.SHORT:
            gap_low = max(c2.high, c3.high)
            gap_high = min(c1.low, c2.open)
        else:
            gap_low = min(c1.high, c2.high)
            gap_high = max(c1.low, c2.low)

        if gap_high <= gap_low:
            return displacement, None
        return displacement, (gap_low, gap_high)

    def _derive_orders(
        self,
        direction: TradeDirection,
        fvg_bounds: Optional[tuple[float, float]],
        last_close: float,
        bias_target: Optional[float],
    ) -> tuple[Optional[float], Optional[float], Optional[float]]:
        if fvg_bounds:
            low, high = fvg_bounds
            entry = (low + high) / 2
            gap = abs(high - low)
        else:
            entry = last_close
            gap = 1.0

        stop_buffer = gap if fvg_bounds else max(0.5, gap)
        stop_distance = max(stop_buffer, 0.5)

        if direction is TradeDirection.LONG:
            stop = entry - stop_distance
            default_target = entry + stop_distance * 2
            target = default_target if bias_target is None else max(default_target, bias_target)
        else:
            stop = entry + stop_distance
            default_target = entry - stop_distance * 2
            target = default_target if bias_target is None else min(default_target, bias_target)

        return entry, stop, target
