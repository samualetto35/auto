"""Broker routing abstraction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .models import OrderPlan


class BrokerAPI(Protocol):
    def place_order(self, plan: OrderPlan) -> str: ...
    def cancel_order(self, broker_order_id: str) -> None: ...


@dataclass(slots=True)
class LoggingBroker:
    """A broker adapter that simply logs actions."""

    def place_order(self, plan: OrderPlan) -> str:
        print(f"[BROKER] Placing {plan.order_type} order {plan.order_id} @ {plan.entry} (SL {plan.stop}, TP {plan.target})")
        return plan.order_id

    def cancel_order(self, broker_order_id: str) -> None:
        print(f"[BROKER] Cancelling order {broker_order_id}")

