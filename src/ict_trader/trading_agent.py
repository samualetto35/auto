"""Top-level trading agent orchestrating all components."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import AgentConfig, default_config
from .data_feed import BarFeed
from .engines.bias import BiasEngine
from .engines.structure import StructureEngine
from .engines.execution import ExecutionEngine
from .models import Candle, ExecutionSignal
from .order_manager import OrderManager
from .order_router import BrokerAPI, LoggingBroker
from .risk import AccountState, build_order_plan
from .sessions import current_session, kill_zone_label
from .state_store import StateStore
from .supervisor import Supervisor


@dataclass(slots=True)
class TradingAgent:
    config: AgentConfig
    feed: BarFeed
    store: StateStore
    bias_engine: BiasEngine
    structure_engine: StructureEngine
    execution_engine: ExecutionEngine
    supervisor: Supervisor
    order_manager: OrderManager
    broker: BrokerAPI

    @classmethod
    def create(cls, config: AgentConfig | None = None) -> "TradingAgent":
        cfg = config or default_config()
        store = StateStore()
        feed = BarFeed(symbol=cfg.symbol)
        bias_engine = BiasEngine(store=store, lookback=cfg.engines.bias_lookback)
        structure_engine = StructureEngine(store=store, lookback=cfg.engines.structure_lookback)
        execution_engine = ExecutionEngine(store=store, lookback=cfg.engines.execution_lookback, rr_target=cfg.risk.rr_target)
        account = AccountState(equity=100000.0)
        supervisor = Supervisor(config=cfg, account=account)
        order_manager = OrderManager(store)
        broker = LoggingBroker()
        agent = cls(
            config=cfg,
            feed=feed,
            store=store,
            bias_engine=bias_engine,
            structure_engine=structure_engine,
            execution_engine=execution_engine,
            supervisor=supervisor,
            order_manager=order_manager,
            broker=broker,
        )
        agent._wire()
        return agent

    def _wire(self) -> None:
        self.feed.register("1h", self.on_hourly_close)
        self.feed.register("15m", self.on_structure_close)
        self.feed.register("1m", self.on_execution_close)
        self.feed.register("5m", self.on_execution_close)

    # Event handlers ---------------------------------------------------------
    def on_hourly_close(self, candle: Candle) -> None:
        session = current_session(candle.timestamp, list(self.config.enabled_sessions()))
        print(f"[BIAS] {candle.timestamp.isoformat()} session={kill_zone_label(session)}")
        self.bias_engine.update(candle)

    def on_structure_close(self, candle: Candle) -> None:
        self.structure_engine.update(candle)

    def on_execution_close(self, candle: Candle) -> None:
        if not self.supervisor.can_trade(candle.timestamp):
            return
        signal = self.execution_engine.evaluate(candle)
        if signal:
            self._handle_signal(signal, candle.timestamp)
        self._manage_orders(candle.timestamp)

    def _handle_signal(self, signal: ExecutionSignal, now: datetime) -> None:
        order = build_order_plan(self.config, signal, self.supervisor.account, now)
        if not order:
            return
        print(f"[SIGNAL] {signal.generated_at.isoformat()} direction={signal.direction} entry={signal.entry} rr={signal.rr:.2f}")
        self.order_manager.place_order(order)
        self.broker.place_order(order)

    def _manage_orders(self, now: datetime) -> None:
        for order_id, order in list(self.order_manager.active_orders().items()):
            if order.expires_at <= now:
                self.broker.cancel_order(order_id)
                self.order_manager.cancel_order(order_id, "expiry", now)

    # External API -----------------------------------------------------------
    def on_candle(self, candle: Candle) -> None:
        self.feed.push(candle)

    def replay(self, candles: list[Candle]) -> None:
        self.feed.replay(candles)

