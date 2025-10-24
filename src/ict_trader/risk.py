"""Risk and position sizing utilities."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .config import AgentConfig
from .models import ExecutionSignal, OrderPlan


@dataclass(slots=True)
class AccountState:
    equity: float
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    trades_today: int = 0

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
        self.trades_today = 0

    def reset_weekly(self) -> None:
        self.weekly_pnl = 0.0


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
    metadata = {
        "size": str(size),
        "rr": f"{signal.rr:.2f}",
        "reason": signal.reason,
    }

    order = OrderPlan(
        order_id=order_id,
        symbol=signal.symbol,
        direction=signal.direction,
        order_type="limit" if signal.entry != signal.target else "market",
        entry=signal.entry,
        stop=signal.stop,
        target=signal.target,
        rr=signal.rr,
        created_at=now,
        expires_at=expiry,
        bias_snapshot_id=signal.bias_snapshot_id,
        structure_zone_id=signal.structure_zone_id,
        metadata=metadata,
    )
    return order
