"""In-memory state storage for the trading agent."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Dict, Optional

from .models import BiasSnapshot, StructureZone, ExecutionSignal, OrderPlan, TradeState


@dataclass(slots=True)
class StateStore:
    """Simple in-memory store used by the agent.

    The implementation is deliberately lightweight to allow the same API to be
    replaced by Redis or a persistent database without changing the engines.
    """

    max_bias_snapshots: int = 50
    max_structure_zones: int = 50
    max_execution_signals: int = 200
    bias_snapshots: Deque[BiasSnapshot] = field(init=False)
    structure_zones: Deque[StructureZone] = field(init=False)
    execution_signals: Deque[ExecutionSignal] = field(init=False)
    orders: Dict[str, OrderPlan] = field(init=False, default_factory=dict)
    last_bias_id: int = field(init=False, default=0)
    last_structure_id: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "bias_snapshots", deque(maxlen=self.max_bias_snapshots))
        object.__setattr__(self, "structure_zones", deque(maxlen=self.max_structure_zones))
        object.__setattr__(self, "execution_signals", deque(maxlen=self.max_execution_signals))
        object.__setattr__(self, "orders", {})
        object.__setattr__(self, "last_bias_id", 0)
        object.__setattr__(self, "last_structure_id", 0)

    # Bias snapshot management -------------------------------------------------
    def push_bias(self, snapshot: BiasSnapshot) -> BiasSnapshot:
        self.last_bias_id += 1
        snapshot.id = self.last_bias_id
        self.bias_snapshots.append(snapshot)
        return snapshot

    def latest_bias(self) -> Optional[BiasSnapshot]:
        return self.bias_snapshots[-1] if self.bias_snapshots else None

    # Structure zone management -------------------------------------------------
    def push_structure_zone(self, zone: StructureZone) -> StructureZone:
        self.last_structure_id += 1
        zone.id = self.last_structure_id
        self.structure_zones.append(zone)
        return zone

    def latest_structure(self) -> Optional[StructureZone]:
        return self.structure_zones[-1] if self.structure_zones else None

    # Execution signals ---------------------------------------------------------
    def push_execution_signal(self, signal: ExecutionSignal) -> ExecutionSignal:
        self.execution_signals.append(signal)
        return signal

    def latest_signal(self) -> Optional[ExecutionSignal]:
        return self.execution_signals[-1] if self.execution_signals else None

    # Orders --------------------------------------------------------------------
    def register_order(self, plan: OrderPlan) -> None:
        self.orders[plan.order_id] = plan

    def update_order_state(self, order_id: str, state: TradeState, *, when: Optional[datetime] = None, reason: str | None = None) -> None:
        order = self.orders.get(order_id)
        if not order:
            return
        order.state = state
        if when and state in {TradeState.FILLED, TradeState.EXIT, TradeState.CANCELLED}:
            order.filled_at = when
        if reason:
            order.exit_reason = reason

    def active_orders(self) -> Dict[str, OrderPlan]:
        return {k: v for k, v in self.orders.items() if v.state not in {TradeState.EXIT, TradeState.CANCELLED}}

