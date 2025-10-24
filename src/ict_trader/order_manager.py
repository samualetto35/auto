"""Order management and lifecycle orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from .config import AgentConfig
from .models import Candle, OrderEvent, OrderEventType, OrderPlan, TradeState
from .state_store import StateStore

if TYPE_CHECKING:
    from .paper_broker import PaperBroker


@dataclass(slots=True)
class OrderManager:
    store: StateStore
    config: AgentConfig

    def place_order(self, plan: OrderPlan) -> None:
        """Register a new order plan in the state store."""
        self.store.register_order(plan)

    def active_orders(self) -> Dict[str, OrderPlan]:
        return self.store.active_orders()

    def cancel_order(self, order_id: str, reason: str, when: datetime) -> None:
        self.store.update_order_state(order_id, TradeState.CANCELLED, when=when, reason=reason)

    def handle_event(self, event: OrderEvent) -> Tuple[Optional[OrderPlan], Optional[float]]:
        order = self.store.get_order(event.order_id)
        if not order:
            return None, None

        if event.metadata:
            self.store.record_event_metadata(order.order_id, event.metadata)

        if event.event_type is OrderEventType.PLACED:
            self.store.update_order_state(order.order_id, TradeState.WAITING, when=event.timestamp)
            return order, None

        if event.event_type is OrderEventType.FILLED:
            self.store.update_order_state(
                order.order_id,
                TradeState.FILLED,
                when=event.timestamp,
                filled_price=event.price,
            )
            self.store.record_event_metadata(order.order_id, {"bars_in_trade": "0"})
            return order, None

        if event.event_type in {OrderEventType.TP_HIT, OrderEventType.SL_HIT, OrderEventType.TIME_STOP}:
            self.store.update_order_state(
                order.order_id,
                TradeState.EXIT,
                when=event.timestamp,
                reason=event.reason,
                exit_price=event.price,
            )
            return order, event.pnl

        if event.event_type in {OrderEventType.EXPIRED, OrderEventType.CANCELLED}:
            self.store.update_order_state(order.order_id, TradeState.CANCELLED, when=event.timestamp, reason=event.reason)
            return order, event.pnl

        return order, None

    def enforce_time_stop(self, candle: Candle, broker: "PaperBroker") -> List[OrderEvent]:
        """Check open orders for time stop violations and emit close events."""

        if candle.timeframe != "1m":
            return []

        events: List[OrderEvent] = []
        for plan in list(self.store.orders_by_state({TradeState.FILLED, TradeState.MANAGING}).values()):
            bars = int(plan.metadata.get("bars_in_trade", "0"))
            bars += 1
            plan.metadata["bars_in_trade"] = str(bars)
            self.store.record_event_metadata(plan.order_id, {"bars_in_trade": str(bars)})

            if plan.state is TradeState.FILLED:
                self.store.update_order_state(plan.order_id, TradeState.MANAGING, when=candle.timestamp, filled_price=plan.filled_price)
                plan.state = TradeState.MANAGING

            if bars >= self.config.risk.time_stop_bars:
                exit_price = candle.close
                event = broker.close_order(
                    plan,
                    candle.timestamp,
                    OrderEventType.TIME_STOP,
                    exit_price,
                    plan.filled_price or plan.entry,
                    reason="time_stop",
                )
                events.append(event)
        return events

