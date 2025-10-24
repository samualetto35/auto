"""Utility helpers for ICT-inspired market structure analysis."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Tuple

from .models import Candle, TradeDirection


TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "45m": 45,
    "1h": 60,
    "2h": 120,
    "3h": 180,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
}


def timeframe_to_minutes(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"Unsupported timeframe '{timeframe}'")
    return TIMEFRAME_MINUTES[timeframe]


def floor_time(timestamp: datetime, timeframe: str) -> datetime:
    """Floor a timestamp to the beginning of the timeframe bucket."""

    minutes = timeframe_to_minutes(timeframe)
    seconds = minutes * 60
    epoch = int(timestamp.timestamp())
    floored = epoch - (epoch % seconds)
    return datetime.fromtimestamp(floored, tz=timestamp.tzinfo or timezone.utc)


def dealing_range(candles: Sequence[Candle], lookback: int) -> Tuple[float, float]:
    window = list(candles)[-lookback:]
    highs = [c.high for c in window]
    lows = [c.low for c in window]
    return max(highs), min(lows)


def fibonacci_zone(high: float, low: float, direction: TradeDirection) -> Tuple[float, float]:
    """Return the 62%–79% discount/premium zone within the dealing range."""

    range_size = high - low
    if range_size <= 0:
        return high, low

    if direction is TradeDirection.LONG:
        zone_high = low + range_size * 0.62
        zone_low = low + range_size * 0.79
    else:
        zone_low = high - range_size * 0.62
        zone_high = high - range_size * 0.79
    return (max(zone_high, zone_low), min(zone_high, zone_low)) if direction is TradeDirection.SHORT else (
        min(zone_high, zone_low),
        max(zone_high, zone_low),
    )


def _is_swing_high(candles: Sequence[Candle], idx: int, left: int, right: int) -> bool:
    pivot = candles[idx]
    left_side = candles[max(idx - left, 0) : idx]
    right_side = candles[idx + 1 : idx + 1 + right]
    return all(pivot.high >= c.high for c in left_side) and all(pivot.high > c.high for c in right_side)


def _is_swing_low(candles: Sequence[Candle], idx: int, left: int, right: int) -> bool:
    pivot = candles[idx]
    left_side = candles[max(idx - left, 0) : idx]
    right_side = candles[idx + 1 : idx + 1 + right]
    return all(pivot.low <= c.low for c in left_side) and all(pivot.low < c.low for c in right_side)


def swing_points(
    candles: Sequence[Candle],
    left: int = 2,
    right: int = 2,
    max_points: int = 10,
) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """Return indices/highs and indices/lows of the last swing points."""

    candles = list(candles)
    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []
    for idx in range(left, len(candles) - right):
        if _is_swing_high(candles, idx, left, right):
            swing_highs.append((idx, candles[idx].high))
        if _is_swing_low(candles, idx, left, right):
            swing_lows.append((idx, candles[idx].low))
    return swing_highs[-max_points:], swing_lows[-max_points:]


def last_structure_break(
    candles: Sequence[Candle],
    left: int = 2,
    right: int = 2,
) -> Optional[TradeDirection]:
    """Determine the most recent market structure break direction."""

    candles = list(candles)
    if len(candles) < left + right + 3:
        return None

    highs, lows = swing_points(candles, left=left, right=right, max_points=20)
    if not highs or not lows:
        return None

    last_close = candles[-1].close
    recent_high = max((price for _, price in highs[:-1]), default=highs[-1][1])
    recent_low = min((price for _, price in lows[:-1]), default=lows[-1][1])

    if last_close > recent_high:
        return TradeDirection.LONG
    if last_close < recent_low:
        return TradeDirection.SHORT
    return None


def displacement_strength(candles: Sequence[Candle], lookback: int = 5) -> float:
    seq = list(candles)
    bodies = [c.body for c in seq[-lookback:]]
    if not bodies:
        return 0.0
    return sum(bodies) / len(bodies)


@dataclass(slots=True)
class FVG:
    direction: TradeDirection
    low: float
    high: float
    displacement: float
    start_index: int
    end_index: int


def detect_fvg(candles: Sequence[Candle], direction: TradeDirection) -> Optional[FVG]:
    """Detect the most recent fair value gap aligned with direction."""

    candles = list(candles)
    if len(candles) < 3:
        return None

    c1, c2, c3 = candles[-3:]
    if direction is TradeDirection.LONG:
        gap_low = max(c1.high, c2.high)
        gap_high = min(c2.low, c3.low)
        if gap_low >= gap_high:
            return None
        displacement = c2.close - c2.open
        if displacement <= 0:
            return None
        return FVG(direction=direction, low=gap_low, high=gap_high, displacement=displacement, start_index=len(candles) - 3, end_index=len(candles) - 1)

    gap_high = min(c1.low, c2.low)
    gap_low = max(c2.high, c3.high)
    if gap_high <= gap_low:
        return None
    displacement = c2.open - c2.close
    if displacement <= 0:
        return None
    return FVG(direction=direction, low=gap_low, high=gap_high, displacement=displacement, start_index=len(candles) - 3, end_index=len(candles) - 1)


def liquidity_sweep(
    candles: Sequence[Candle],
    direction: TradeDirection,
    sweep_lookback: int = 20,
    sweep_window: int = 3,
) -> bool:
    """Check whether the last candles swept external liquidity."""

    candles = list(candles)
    if len(candles) < sweep_window + 2:
        return False

    recent = candles[-sweep_window:]
    body_direction = TradeDirection.LONG if recent[-1].close >= recent[-1].open else TradeDirection.SHORT
    if direction is TradeDirection.LONG and body_direction is not TradeDirection.LONG:
        return False
    if direction is TradeDirection.SHORT and body_direction is not TradeDirection.SHORT:
        return False

    historical = candles[-(sweep_window + sweep_lookback) : -sweep_window]
    if not historical:
        return False

    if direction is TradeDirection.LONG:
        previous_low = min(c.low for c in historical)
        return any(c.low < previous_low for c in recent)
    previous_high = max(c.high for c in historical)
    return any(c.high > previous_high for c in recent)


def market_structure_shift(
    candles: Sequence[Candle],
    direction: TradeDirection,
    confirmation_lookback: int = 10,
) -> bool:
    """Confirm that price broke internal structure after sweep."""

    candles = list(candles)
    if len(candles) < confirmation_lookback + 2:
        return False

    confirmation_slice = candles[-confirmation_lookback:]
    closing_prices = [c.close for c in confirmation_slice]

    if direction is TradeDirection.LONG:
        internal_high = max(c.high for c in confirmation_slice[:-1])
        return closing_prices[-1] > internal_high
    internal_low = min(c.low for c in confirmation_slice[:-1])
    return closing_prices[-1] < internal_low


def rr_ratio(entry: float, stop: float, target: float) -> float:
    stop_distance = abs(entry - stop)
    return abs(target - entry) / max(stop_distance, 1e-6)


def to_timedelta(timeframe: str) -> timedelta:
    return timedelta(minutes=timeframe_to_minutes(timeframe))

