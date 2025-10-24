"""Execution engine implementing ICT style confirmations."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Dict, Optional, Sequence

from ..ict_utils import (
    dealing_range,
    detect_fvg,
    liquidity_sweep,
    market_structure_shift,
    rr_ratio,
)
from ..models import Candle, ExecutionSignal, StructureZone, TradeDirection
from ..state_store import StateStore


@dataclass(slots=True)
class ExecutionEngine:
    """Examines 1m/5m candles for sweeps → MSS → FVG patterns."""

    store: StateStore
    lookback: int = 50
    rr_target: float = 2.0
    primary_timeframe: str = "1m"
    context_timeframes: Sequence[str] = field(default_factory=lambda: ("5m",))
    sweep_lookback: int = 30
    confirmation_lookback: int = 12
    candles: Dict[str, Deque[Candle]] = field(default_factory=dict)

    def evaluate(self, candle: Candle) -> Optional[ExecutionSignal]:
        buffer = self.candles.setdefault(candle.timeframe, deque(maxlen=max(self.lookback, 200)))
        buffer.append(candle)

        if candle.timeframe != self.primary_timeframe:
            return None

        if len(buffer) < 15:
            return None

        bias = self.store.latest_bias()
        structure = self._valid_structure(candle.timestamp)
        if not bias or not structure:
            return None

        if structure.direction != bias.bias:
            return None

        if not structure.contains(candle.close):
            return None

        if not liquidity_sweep(buffer, bias.bias, sweep_lookback=self.sweep_lookback):
            return None

        if not market_structure_shift(buffer, bias.bias, confirmation_lookback=self.confirmation_lookback):
            return None

        context_ok = True
        for timeframe in self.context_timeframes:
            ctx = self.candles.get(timeframe)
            if not ctx:
                continue
            last_ctx = ctx[-1]
            if last_ctx.direction != bias.bias:
                context_ok = False
                break
        if not context_ok:
            return None

        fvg = detect_fvg(buffer, bias.bias)
        if not fvg:
            return None

        entry = (fvg.low + fvg.high) / 2
        recent_range_high, recent_range_low = dealing_range(buffer, min(self.lookback, len(buffer)))

        if bias.bias is TradeDirection.LONG:
            stop = min(c.low for c in buffer[-self.confirmation_lookback :])
            stop -= max(0.1, (entry - stop) * 0.1)
            target = max(bias.target_price or recent_range_high, structure.high)
        else:
            stop = max(c.high for c in buffer[-self.confirmation_lookback :])
            stop += max(0.1, (stop - entry) * 0.1)
            target = min(bias.target_price or recent_range_low, structure.low)

        rr = rr_ratio(entry, stop, target)
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
