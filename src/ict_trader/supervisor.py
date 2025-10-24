"""Supervisory guard rails for the trading agent."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import AgentConfig
from .risk import AccountState
from .sessions import current_session


@dataclass(slots=True)
class Supervisor:
    config: AgentConfig
    account: AccountState

    def can_trade(self, now: datetime) -> bool:
        if self.account.trades_today >= self.config.risk.max_trades_per_day:
            return False
        if abs(self.account.daily_pnl) >= self.config.risk.max_daily_drawdown_pct * self.account.equity:
            return False
        if abs(self.account.weekly_pnl) >= self.config.risk.max_weekly_drawdown_pct * self.account.equity:
            return False

        session = current_session(now, list(self.config.enabled_sessions()))
        return session is not None

    def record_trade(self, pnl: float) -> None:
        self.account.trades_today += 1
        self.account.daily_pnl += pnl
        self.account.weekly_pnl += pnl

