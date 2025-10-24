"""Order management and state machine handling."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from .models import OrderPlan, TradeState
from .state_store import StateStore


@dataclass(slots=True)
class OrderManager:
    store: StateStore

    def place_order(self, plan: OrderPlan) -> None:
        self.store.register_order(plan)

    def cancel_order(self, order_id: str, reason: str, now: datetime) -> None:
        self.store.update_order_state(order_id, TradeState.CANCELLED, when=now, reason=reason)

    def on_fill(self, order_id: str, now: datetime) -> None:
        self.store.update_order_state(order_id, TradeState.FILLED, when=now)

    def exit_order(self, order_id: str, reason: str, now: datetime) -> None:
        self.store.update_order_state(order_id, TradeState.EXIT, when=now, reason=reason)

    def active_orders(self) -> Dict[str, OrderPlan]:
        return self.store.active_orders()

