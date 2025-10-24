"""Top-level trading agent orchestrating all components."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import List

from .config import AgentConfig, default_config
from .data_feed import BarFeed
from .engines.bias import BiasEngine
from .engines.execution import ExecutionEngine
from .engines.structure import StructureEngine
from .models import Candle, ExecutionSignal, OrderEvent, TradeState
from .order_manager import OrderManager
from .paper_broker import PaperBroker
from .risk import AccountState, build_order_plan
from .sessions import current_session, kill_zone_label
from .state_store import StateStore
from .supervisor import Supervisor
from .trade_logger import TradeLogger


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
    broker: PaperBroker
    account: AccountState
    logger: TradeLogger

    @classmethod
    def create(cls, config: AgentConfig | None = None) -> "TradingAgent":
        cfg = config or default_config()
        os.makedirs(cfg.log_dir, exist_ok=True)
        store = StateStore(db_path=cfg.state_db_path)
        feed = BarFeed(symbol=cfg.symbol)
        bias_context = list(cfg.bias_timeframes[1:])
        if not bias_context:
            bias_context = [cfg.bias_timeframes[0]]
        exec_context = list(cfg.execution_timeframes[1:])
        if not exec_context:
            exec_context = [cfg.execution_timeframes[0]]

        bias_engine = BiasEngine(
            store=store,
            lookback=cfg.engines.bias_lookback,
            primary_timeframe=cfg.bias_timeframes[0],
            context_timeframes=tuple(bias_context),
        )
        structure_engine = StructureEngine(
            store=store,
            lookback=cfg.engines.structure_lookback,
            timeframe=cfg.structure_timeframe,
        )
        execution_engine = ExecutionEngine(
            store=store,
            lookback=cfg.engines.execution_lookback,
            rr_target=cfg.risk.rr_target,
            primary_timeframe=cfg.execution_timeframes[0],
            context_timeframes=tuple(exec_context),
        )
        account = AccountState(equity=100000.0)
        logger = TradeLogger(cfg.trade_log_path, export_dir=cfg.log_dir)
        supervisor = Supervisor(config=cfg, account=account, logger=logger)
        order_manager = OrderManager(store=store, config=cfg)
        broker = PaperBroker(config=cfg, store=store, logger=logger)
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
            account=account,
            logger=logger,
        )
        agent._wire()
        return agent

    def _wire(self) -> None:
        for timeframe in self.config.bias_timeframes:
            self.feed.register(timeframe, self.on_bias_close)
        self.feed.register(self.config.structure_timeframe, self.on_structure_close)
        for timeframe in self.config.execution_timeframes:
            self.feed.register(timeframe, self.on_execution_close)

    # Event handlers ---------------------------------------------------------
    def on_bias_close(self, candle: Candle) -> None:
        snapshot = self.bias_engine.update(candle)
        if not snapshot:
            return
        session = current_session(candle.timestamp, list(self.config.enabled_sessions()))
        print(f"[BIAS] {candle.timestamp.isoformat()} tf={candle.timeframe} session={kill_zone_label(session)} bias={snapshot.bias}")
        events = self.supervisor.handle_bias_snapshot(
            snapshot.bias,
            candle.timestamp,
            self.broker,
            self.order_manager,
            candle.close,
        )
        if events:
            self._process_events(events)

    def on_structure_close(self, candle: Candle) -> None:
        self.structure_engine.update(candle)

    def on_execution_close(self, candle: Candle) -> None:
        broker_events = self.broker.process_candle(candle)
        if broker_events:
            self._process_events(broker_events)

        time_stop_events = self.order_manager.enforce_time_stop(candle, self.broker)
        if time_stop_events:
            self._process_events(time_stop_events)

        if not self.supervisor.can_trade(candle.timestamp):
            return

        signal = self.execution_engine.evaluate(candle)
        if signal and self._within_position_limits():
            self._handle_signal(signal, candle.timestamp)

    def _handle_signal(self, signal: ExecutionSignal, now: datetime) -> None:
        order = build_order_plan(self.config, signal, self.account, now)
        if not order:
            return
        print(f"[SIGNAL] {signal.generated_at.isoformat()} direction={signal.direction} entry={signal.entry} rr={signal.rr:.2f}")
        self.order_manager.place_order(order)
        place_event = self.broker.place_order(order, now)
        self._process_events([place_event])

    def _within_position_limits(self) -> bool:
        active_trades = [
            order
            for order in self.order_manager.active_orders().values()
            if order.state in {TradeState.FILLED, TradeState.MANAGING}
        ]
        return len(active_trades) < self.config.risk.max_concurrent_positions

    def _process_events(self, events: List[OrderEvent]) -> None:
        for event in events:
            order, pnl = self.order_manager.handle_event(event)
            if not order:
                continue
            if pnl is not None:
                self.account.apply_trade_result(pnl, event.timestamp)
                self.supervisor.on_trade_closed(event.timestamp)

    # External API -----------------------------------------------------------
    def on_candle(self, candle: Candle) -> None:
        self.feed.push(candle)

    def replay(self, candles: list[Candle]) -> None:
        self.feed.replay(candles)

