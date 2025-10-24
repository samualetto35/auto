"""Risk and position sizing utilities."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .config import AgentConfig
from .models import ExecutionSignal, OrderPlan


@dataclass(slots=True)
class AccountState:
    equity: float
    starting_equity: float = field(init=False)
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    closed_pnl: float = 0.0
    trades_today: int = 0
    max_equity: float = field(init=False)
    min_equity: float = field(init=False)
    max_drawdown: float = 0.0
    last_daily_reset: Optional[datetime] = None
    last_weekly_reset: Optional[datetime] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "starting_equity", self.equity)
        object.__setattr__(self, "max_equity", self.equity)
        object.__setattr__(self, "min_equity", self.equity)

    def reset_daily(self, when: datetime) -> None:
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.last_daily_reset = when

    def reset_weekly(self, when: datetime) -> None:
        self.weekly_pnl = 0.0
        self.last_weekly_reset = when

    @property
    def current_drawdown(self) -> float:
        return self.max_equity - self.equity

    @property
    def current_drawdown_pct(self) -> float:
        if self.max_equity == 0:
            return 0.0
        return (self.current_drawdown / self.max_equity) if self.max_equity else 0.0

    def apply_trade_result(self, pnl_cash: float, when: datetime) -> None:
        self.equity += pnl_cash
        self.closed_pnl += pnl_cash
        self.daily_pnl += pnl_cash
        self.weekly_pnl += pnl_cash
        self.max_equity = max(self.max_equity, self.equity)
        self.min_equity = min(self.min_equity, self.equity)
        drawdown = self.max_equity - self.equity
        self.max_drawdown = max(self.max_drawdown, drawdown)
        self.trades_today += 1


def position_size(config: AgentConfig, equity: float, entry: float, stop: float) -> int:
    risk_cash = equity * config.risk.risk_per_trade_pct
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return 0
    size = risk_cash / (stop_distance * config.value_per_point)
    return max(0, int(size))


def build_order_plan(config: AgentConfig, signal: ExecutionSignal, account: AccountState, now: datetime) -> Optional[OrderPlan]:
    size = position_size(config, account.equity, signal.entry, signal.stop)
    if size <= 0:
        return None

    expiry = now + timedelta(minutes=config.risk.expiry_minutes)
    order_id = f"{signal.symbol}-{now:%Y%m%d-%H%M%S}"
    order = OrderPlan(
        order_id=order_id,
        symbol=signal.symbol,
        direction=signal.direction,
        order_type="limit" if signal.entry != signal.target else "market",
        entry=signal.entry,
        stop=signal.stop,
        target=signal.target,
        rr=signal.rr,
        size=size,
        created_at=now,
        expires_at=expiry,
        bias_snapshot_id=signal.bias_snapshot_id,
        structure_zone_id=signal.structure_zone_id,
        metadata={
            "rr": f"{signal.rr:.2f}",
            "reason": signal.reason,
        },
    )
    return order
