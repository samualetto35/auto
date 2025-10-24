"""Supervisory guard rails with session, drawdown, and news controls."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from .config import AgentConfig
from .models import OrderEvent, OrderEventType, TradeDirection, TradeState
from .order_manager import OrderManager
from .paper_broker import PaperBroker
from .risk import AccountState
from .sessions import current_session
from .trade_logger import TradeLogger


@dataclass(slots=True)
class Supervisor:
    config: AgentConfig
    account: AccountState
    logger: Optional[TradeLogger] = None
    halted: bool = False
    halt_reason: Optional[str] = None
    last_bias_direction: Optional[TradeDirection] = None

    def _maybe_reset(self, now: datetime) -> None:
        if not self.account.last_daily_reset or now.date() != self.account.last_daily_reset.date():
            self.account.reset_daily(now)
            self.halted = False
            self.halt_reason = None
            if self.logger:
                self.logger.log_account_update(self.account, now, reason="Daily reset")

        iso_week = now.isocalendar().week
        if not self.account.last_weekly_reset or self.account.last_weekly_reset.isocalendar().week != iso_week:
            self.account.reset_weekly(now)
            if self.logger:
                self.logger.log_account_update(self.account, now, reason="Weekly reset")

    def _in_news_blackout(self, now: datetime) -> bool:
        window = timedelta(minutes=self.config.news_halt_minutes)
        for event_time in self.config.news_events:
            if abs(event_time - now) <= window:
                return True
        return False

    def can_trade(self, now: datetime) -> bool:
        self._maybe_reset(now)
        if self.halted:
            return False

        if self._in_news_blackout(now):
            self._log_action("Trading halted due to news blackout", now)
            return False

        session = current_session(now, list(self.config.enabled_sessions()))
        if session is None:
            return False

        if self.account.trades_today >= self.config.risk.max_trades_per_day:
            self.halt("Max trades reached", now)
            return False

        daily_limit = -self.account.daily_pnl >= self.account.starting_equity * self.config.risk.max_daily_drawdown_pct
        weekly_limit = -self.account.weekly_pnl >= self.account.starting_equity * self.config.risk.max_weekly_drawdown_pct
        if daily_limit:
            self.halt("Daily drawdown limit reached", now)
            return False
        if weekly_limit:
            self.halt("Weekly drawdown limit reached", now)
            return False

        return True

    def halt(self, reason: str, now: datetime) -> None:
        self.halted = True
        self.halt_reason = reason
        self._log_action(f"Trading halted due to {reason}", now)

    def resume(self, now: datetime) -> None:
        if self.halted:
            self.halted = False
            self.halt_reason = None
            self._log_action("Trading resumed", now)

    def evaluate_limits(self, now: datetime) -> None:
        if -self.account.daily_pnl >= self.account.starting_equity * self.config.risk.max_daily_drawdown_pct:
            self.halt("Daily drawdown limit reached", now)
        elif -self.account.weekly_pnl >= self.account.starting_equity * self.config.risk.max_weekly_drawdown_pct:
            self.halt("Weekly drawdown limit reached", now)

    def on_trade_closed(self, now: datetime) -> None:
        self.evaluate_limits(now)
        if self.logger:
            self.logger.log_account_update(self.account, now, reason="Trade closed")

    def handle_bias_snapshot(self, direction: TradeDirection, now: datetime, broker: PaperBroker, orders: OrderManager, last_price: float) -> List[OrderEvent]:
        events: List[OrderEvent] = []
        if self.last_bias_direction and direction is not self.last_bias_direction:
            for order in list(orders.active_orders().values()):
                if order.direction is direction:
                    continue
                if order.state is TradeState.WAITING:
                    cancel_event = broker.cancel_order(order.order_id, now, "bias_flip")
                    if cancel_event:
                        events.append(cancel_event)
                elif order.state in {TradeState.FILLED, TradeState.MANAGING}:
                    close_event = broker.close_order(
                        order,
                        now,
                        OrderEventType.CANCELLED,
                        last_price,
                        order.filled_price or order.entry,
                        reason="bias_flip",
                    )
                    events.append(close_event)
        self.last_bias_direction = direction
        return events

    def _log_action(self, message: str, now: datetime) -> None:
        if self.logger:
            self.logger.log_account_update(self.account, now, reason=message)
        else:
            print(f"[{now.isoformat()}] {message}")

