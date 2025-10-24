"""Paper trading broker that simulates fills and lifecycle events."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from .config import AgentConfig
from .models import Candle, OrderEvent, OrderEventType, OrderPlan, TradeDirection, TradeState
from .state_store import StateStore
from .trade_logger import TradeLogger


@dataclass(slots=True)
class PaperBroker:
    """Simulated broker that produces order events based on candle data."""

    config: AgentConfig
    store: StateStore
    logger: TradeLogger

    def place_order(self, plan: OrderPlan, now: datetime) -> OrderEvent:
        print(
            f"[{now.isoformat()}] ORDER_CREATED id={plan.order_id} direction={plan.direction.value} "
            f"entry={plan.entry:.2f} sl={plan.stop:.2f} tp={plan.target:.2f} rr={plan.rr:.2f}"
        )
        self.logger.log_order_event(plan, now, OrderEventType.PLACED, price=plan.entry)
        return OrderEvent(order_id=plan.order_id, timestamp=now, event_type=OrderEventType.PLACED, price=plan.entry)

    def cancel_order(self, order_id: str, now: datetime, reason: str) -> Optional[OrderEvent]:
        order = self.store.get_order(order_id)
        if not order or order.state in {TradeState.CANCELLED, TradeState.EXIT}:
            return None
        print(f"[{now.isoformat()}] ORDER_CANCELLED id={order_id} reason={reason}")
        self.logger.log_order_event(order, now, OrderEventType.CANCELLED, reason=reason)
        return OrderEvent(
            order_id=order_id,
            timestamp=now,
            event_type=OrderEventType.CANCELLED,
            reason=reason,
        )

    def process_candle(self, candle: Candle) -> List[OrderEvent]:
        now = candle.timestamp
        events: List[OrderEvent] = []
        for order in list(self.store.active_orders().values()):
            if order.state == TradeState.WAITING:
                if order.expires_at and now >= order.expires_at:
                    events.append(self._expire_order(order, now))
                    continue
                if self._crossed_entry(order, candle):
                    events.append(self._fill_order(order, now))
                    continue

            if order.state in {TradeState.MANAGING, TradeState.FILLED}:
                exit_event = self._check_exit(order, candle)
                if exit_event:
                    events.append(exit_event)
        return events

    # -- Internal helpers -------------------------------------------------
    def _crossed_entry(self, order: OrderPlan, candle: Candle) -> bool:
        return candle.low <= order.entry <= candle.high

    def _fill_order(self, order: OrderPlan, now: datetime) -> OrderEvent:
        order.metadata["bars_in_trade"] = "0"
        self.store.record_event_metadata(order.order_id, {"bars_in_trade": "0"})
        print(f"[{now.isoformat()}] ORDER_FILLED id={order.order_id} price={order.entry:.2f}")
        self.logger.log_order_event(order, now, OrderEventType.FILLED, price=order.entry)
        return OrderEvent(
            order_id=order.order_id,
            timestamp=now,
            event_type=OrderEventType.FILLED,
            price=order.entry,
        )

    def _check_exit(self, order: OrderPlan, candle: Candle) -> Optional[OrderEvent]:
        if order.filled_price is None:
            filled_price = order.entry
        else:
            filled_price = order.filled_price

        if order.direction is TradeDirection.LONG:
            if candle.low <= order.stop:
                return self.close_order(order, candle.timestamp, OrderEventType.SL_HIT, order.stop, filled_price, reason="sl_hit")
            if candle.high >= order.target:
                return self.close_order(order, candle.timestamp, OrderEventType.TP_HIT, order.target, filled_price, reason="tp_hit")
        else:
            if candle.high >= order.stop:
                return self.close_order(order, candle.timestamp, OrderEventType.SL_HIT, order.stop, filled_price, reason="sl_hit")
            if candle.low <= order.target:
                return self.close_order(order, candle.timestamp, OrderEventType.TP_HIT, order.target, filled_price, reason="tp_hit")
        return None

    def _expire_order(self, order: OrderPlan, now: datetime) -> OrderEvent:
        print(f"[{now.isoformat()}] ORDER_EXPIRED id={order.order_id} reason=expiry")
        self.logger.log_order_event(order, now, OrderEventType.EXPIRED, reason="expiry")
        return OrderEvent(
            order_id=order.order_id,
            timestamp=now,
            event_type=OrderEventType.EXPIRED,
            reason="expiry",
        )

    def close_order(
        self,
        order: OrderPlan,
        now: datetime,
        event_type: OrderEventType,
        exit_price: float,
        entry_price: float,
        *,
        reason: str,
    ) -> OrderEvent:
        pnl_cash, pnl_r = self._compute_pnl(order, entry_price, exit_price)
        print(
            f"[{now.isoformat()}] {event_type.value} id={order.order_id} price={exit_price:.2f} "
            f"pnl={pnl_r:+.2f}R"
        )
        self.logger.log_order_event(
            order,
            now,
            event_type,
            price=exit_price,
            pnl=pnl_cash,
            pnl_r=pnl_r,
            reason=reason,
        )
        return OrderEvent(
            order_id=order.order_id,
            timestamp=now,
            event_type=event_type,
            price=exit_price,
            pnl=pnl_cash,
            pnl_r=pnl_r,
            reason=reason,
        )

    def _compute_pnl(self, order: OrderPlan, entry_price: float, exit_price: float) -> tuple[float, float]:
        direction = 1 if order.direction is TradeDirection.LONG else -1
        move = (exit_price - entry_price) * direction
        cash = move * order.size * self.config.value_per_point
        risk_cash = order.stop_distance * order.size * self.config.value_per_point
        pnl_r = cash / risk_cash if risk_cash else 0.0
        return cash, pnl_r

