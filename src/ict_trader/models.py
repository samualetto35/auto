"""Core models used across the ICT trading agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional


class TradeDirection(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def multiplier(self) -> int:
        return 1 if self is TradeDirection.LONG else -1


class TradeState(str, Enum):
    WAITING = "waiting"
    FILLED = "filled"
    MANAGING = "managing"
    EXIT = "exit"
    CANCELLED = "cancelled"


class OrderEventType(str, Enum):
    PLACED = "PLACED"
    FILLED = "FILLED"
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    TIME_STOP = "TIME_STOP"


@dataclass(slots=True)
class Candle:
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def direction(self) -> TradeDirection:
        return TradeDirection.LONG if self.close >= self.open else TradeDirection.SHORT


@dataclass(slots=True)
class BiasSnapshot:
    symbol: str
    timeframe: str
    generated_at: datetime
    bias: TradeDirection
    confidence: float
    target_price: Optional[float] = None
    invalidate_below: Optional[float] = None
    id: int = 0


@dataclass(slots=True)
class StructureZone:
    symbol: str
    timeframe: str
    generated_at: datetime
    direction: TradeDirection
    low: float
    high: float
    id: int = 0
    expires_at: Optional[datetime] = None

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


@dataclass(slots=True)
class ExecutionSignal:
    symbol: str
    timeframe: str
    generated_at: datetime
    direction: TradeDirection
    entry: float
    stop: float
    target: float
    rr: float
    bias_snapshot_id: int
    structure_zone_id: int
    reason: str


@dataclass(slots=True)
class OrderPlan:
    order_id: str
    symbol: str
    direction: TradeDirection
    order_type: str
    entry: float
    stop: float
    target: float
    rr: float
    size: int
    created_at: datetime
    expires_at: datetime
    bias_snapshot_id: int
    structure_zone_id: int
    state: TradeState = TradeState.WAITING
    filled_at: Optional[datetime] = None
    filled_price: Optional[float] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def stop_distance(self) -> float:
        return abs(self.entry - self.stop)

    def time_remaining(self, now: datetime) -> timedelta:
        return max(self.expires_at - now, timedelta(0))


@dataclass(slots=True)
class OrderEvent:
    order_id: str
    timestamp: datetime
    event_type: OrderEventType
    price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_r: Optional[float] = None
    reason: Optional[str] = None
    metadata: Optional[Dict[str, str]] = None

