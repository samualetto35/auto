"""Configuration objects for the ICT trading agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, Iterable, List, Sequence

from .ict_utils import timeframe_to_minutes


@dataclass(slots=True)
class SessionWindow:
    """Defines a trading session window in New York local time."""

    name: str
    start: time
    end: time

    def contains(self, session_time: time) -> bool:
        return self.start <= session_time <= self.end


@dataclass(slots=True)
class RiskConfig:
    """Risk parameters for the agent."""

    risk_per_trade_pct: float = 0.02
    max_daily_drawdown_pct: float = 0.06
    max_weekly_drawdown_pct: float = 0.1
    max_trades_per_day: int = 4
    max_concurrent_positions: int = 1
    rr_target: float = 2.0
    time_stop_bars: int = 20
    expiry_minutes: int = 30


@dataclass(slots=True)
class EngineConfig:
    """Parameters controlling the individual engines."""

    bias_lookback: int = 150
    structure_lookback: int = 40
    execution_lookback: int = 50
    use_llm_bias: bool = False


@dataclass(slots=True)
class AgentConfig:
    """Full configuration for the trading agent."""

    symbol: str = "XAUUSD"
    account_currency: str = "USD"
    value_per_point: float = 1.0
    sessions: Sequence[SessionWindow] = field(default_factory=list)
    risk: RiskConfig = field(default_factory=RiskConfig)
    engines: EngineConfig = field(default_factory=EngineConfig)
    enable_sessions: Sequence[str] = field(default_factory=list)
    metrics_db_path: str = "data/trading_metrics.sqlite3"
    broker: Dict[str, str] = field(default_factory=dict)
    log_dir: str = "logs"
    trade_log_path: str = "logs/trades.sqlite3"
    state_db_path: str = "logs/state.sqlite3"
    news_halt_minutes: int = 5
    news_events: Sequence[datetime] = field(default_factory=list)
    base_timeframe: str = "1m"
    execution_timeframes: Sequence[str] = field(default_factory=lambda: ["1m", "5m"])
    structure_timeframe: str = "15m"
    bias_timeframes: Sequence[str] = field(default_factory=lambda: ["1h", "4h", "1d"])
    data_source: str = "mock"
    data_source_args: Dict[str, str] = field(default_factory=dict)
    timezone: str = "America/New_York"

    def enabled_sessions(self) -> List[SessionWindow]:
        if not self.enable_sessions:
            return list(self.sessions)
        return [session for session in self.sessions if session.name in self.enable_sessions]

    def all_timeframes(self) -> List[str]:
        combined = set(self.execution_timeframes)
        combined.add(self.structure_timeframe)
        combined.update(self.bias_timeframes)
        return sorted(combined, key=timeframe_to_minutes)


DEFAULT_SESSIONS: Iterable[SessionWindow] = (
    SessionWindow("London Kill Zone", time(2, 0), time(5, 0)),
    SessionWindow("NY AM Kill Zone", time(10, 0), time(11, 0)),
    SessionWindow("NY PM Silver Bullet", time(14, 0), time(15, 0)),
)


def default_config(symbol: str = "XAUUSD", value_per_point: float = 1.0) -> AgentConfig:
    """Return a default configuration for the provided symbol."""

    cfg = AgentConfig(symbol=symbol, value_per_point=value_per_point)
    cfg.sessions = list(DEFAULT_SESSIONS)
    cfg.enable_sessions = [session.name for session in DEFAULT_SESSIONS]
    return cfg
