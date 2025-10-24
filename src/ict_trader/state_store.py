"""Hybrid in-memory and SQLite-backed state storage for the trading agent."""
from __future__ import annotations

import json
import os
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Dict, Iterable, Optional

from .models import (
    BiasSnapshot,
    ExecutionSignal,
    OrderPlan,
    StructureZone,
    TradeDirection,
    TradeState,
)


def _ensure_dir(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


@dataclass(slots=True)
class StateStore:
    """State store keeping a fast in-memory cache with SQLite persistence."""

    db_path: str = "logs/state.sqlite3"
    max_bias_snapshots: int = 50
    max_structure_zones: int = 50
    max_execution_signals: int = 200
    bias_snapshots: Deque[BiasSnapshot] = field(init=False)
    structure_zones: Deque[StructureZone] = field(init=False)
    execution_signals: Deque[ExecutionSignal] = field(init=False)
    orders: Dict[str, OrderPlan] = field(init=False, default_factory=dict)
    last_bias_id: int = field(init=False, default=0)
    last_structure_id: int = field(init=False, default=0)
    _conn: sqlite3.Connection = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "bias_snapshots", deque(maxlen=self.max_bias_snapshots))
        object.__setattr__(self, "structure_zones", deque(maxlen=self.max_structure_zones))
        object.__setattr__(self, "execution_signals", deque(maxlen=self.max_execution_signals))
        object.__setattr__(self, "orders", {})
        _ensure_dir(self.db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self.load_snapshots()

    # -- Schema management -------------------------------------------------
    def _init_schema(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bias_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                timeframe TEXT,
                generated_at TEXT,
                bias TEXT,
                confidence REAL,
                target_price REAL,
                invalidate_below REAL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS structure_zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                timeframe TEXT,
                generated_at TEXT,
                direction TEXT,
                zone_low REAL,
                zone_high REAL,
                expires_at TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                timeframe TEXT,
                generated_at TEXT,
                direction TEXT,
                entry REAL,
                stop REAL,
                target REAL,
                rr REAL,
                bias_snapshot_id INTEGER,
                structure_zone_id INTEGER,
                reason TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                symbol TEXT,
                direction TEXT,
                order_type TEXT,
                entry REAL,
                stop REAL,
                target REAL,
                rr REAL,
                size INTEGER,
                created_at TEXT,
                expires_at TEXT,
                bias_snapshot_id INTEGER,
                structure_zone_id INTEGER,
                state TEXT,
                filled_at TEXT,
                filled_price REAL,
                exit_price REAL,
                exit_reason TEXT,
                metadata TEXT
            )
            """
        )
        self._conn.commit()

    # -- Persistence helpers ----------------------------------------------
    def _insert_bias(self, snapshot: BiasSnapshot) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO bias_snapshots (symbol, timeframe, generated_at, bias, confidence, target_price, invalidate_below)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.symbol,
                snapshot.timeframe,
                snapshot.generated_at.isoformat(),
                snapshot.bias.value,
                snapshot.confidence,
                snapshot.target_price,
                snapshot.invalidate_below,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def _insert_structure(self, zone: StructureZone) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO structure_zones (symbol, timeframe, generated_at, direction, zone_low, zone_high, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                zone.symbol,
                zone.timeframe,
                zone.generated_at.isoformat(),
                zone.direction.value,
                zone.low,
                zone.high,
                zone.expires_at.isoformat() if zone.expires_at else None,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def _insert_signal(self, signal: ExecutionSignal) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO execution_signals (
                symbol, timeframe, generated_at, direction, entry, stop, target, rr, bias_snapshot_id, structure_zone_id, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.symbol,
                signal.timeframe,
                signal.generated_at.isoformat(),
                signal.direction.value,
                signal.entry,
                signal.stop,
                signal.target,
                signal.rr,
                signal.bias_snapshot_id,
                signal.structure_zone_id,
                signal.reason,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def _persist_order(self, plan: OrderPlan) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO orders (
                order_id, symbol, direction, order_type, entry, stop, target, rr, size,
                created_at, expires_at, bias_snapshot_id, structure_zone_id, state,
                filled_at, filled_price, exit_price, exit_reason, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.order_id,
                plan.symbol,
                plan.direction.value,
                plan.order_type,
                plan.entry,
                plan.stop,
                plan.target,
                plan.rr,
                plan.size,
                plan.created_at.isoformat(),
                plan.expires_at.isoformat(),
                plan.bias_snapshot_id,
                plan.structure_zone_id,
                plan.state.value,
                plan.filled_at.isoformat() if plan.filled_at else None,
                plan.filled_price,
                plan.exit_price,
                plan.exit_reason,
                json.dumps(plan.metadata),
            ),
        )
        self._conn.commit()

    # -- Public API --------------------------------------------------------
    def load_snapshots(self) -> None:
        self.bias_snapshots.clear()
        self.structure_zones.clear()
        self.execution_signals.clear()
        self.orders.clear()

        for row in self._conn.execute(
            "SELECT * FROM bias_snapshots ORDER BY id DESC LIMIT ?", (self.max_bias_snapshots,)
        ).fetchall()[::-1]:
            snapshot = BiasSnapshot(
                symbol=row["symbol"],
                timeframe=row["timeframe"],
                generated_at=_parse_datetime(row["generated_at"]),
                bias=TradeDirection(row["bias"]),
                confidence=row["confidence"],
                target_price=row["target_price"],
                invalidate_below=row["invalidate_below"],
            )
            snapshot.id = row["id"]
            self.bias_snapshots.append(snapshot)
            self.last_bias_id = max(self.last_bias_id, snapshot.id)

        for row in self._conn.execute(
            "SELECT * FROM structure_zones ORDER BY id DESC LIMIT ?", (self.max_structure_zones,)
        ).fetchall()[::-1]:
            zone = StructureZone(
                symbol=row["symbol"],
                timeframe=row["timeframe"],
                generated_at=_parse_datetime(row["generated_at"]),
                direction=TradeDirection(row["direction"]),
                low=row["zone_low"],
                high=row["zone_high"],
                expires_at=_parse_datetime(row["expires_at"]),
            )
            zone.id = row["id"]
            self.structure_zones.append(zone)
            self.last_structure_id = max(self.last_structure_id, zone.id)

        for row in self._conn.execute(
            "SELECT * FROM execution_signals ORDER BY id DESC LIMIT ?",
            (self.max_execution_signals,),
        ).fetchall()[::-1]:
            signal = ExecutionSignal(
                symbol=row["symbol"],
                timeframe=row["timeframe"],
                generated_at=_parse_datetime(row["generated_at"]),
                direction=TradeDirection(row["direction"]),
                entry=row["entry"],
                stop=row["stop"],
                target=row["target"],
                rr=row["rr"],
                bias_snapshot_id=row["bias_snapshot_id"],
                structure_zone_id=row["structure_zone_id"],
                reason=row["reason"],
            )
            self.execution_signals.append(signal)

        for row in self._conn.execute("SELECT * FROM orders").fetchall():
            plan = OrderPlan(
                order_id=row["order_id"],
                symbol=row["symbol"],
                direction=TradeDirection(row["direction"]),
                order_type=row["order_type"],
                entry=row["entry"],
                stop=row["stop"],
                target=row["target"],
                rr=row["rr"],
                size=row["size"],
                created_at=_parse_datetime(row["created_at"]),
                expires_at=_parse_datetime(row["expires_at"]),
                bias_snapshot_id=row["bias_snapshot_id"],
                structure_zone_id=row["structure_zone_id"],
                state=TradeState(row["state"]),
                filled_at=_parse_datetime(row["filled_at"]),
                filled_price=row["filled_price"],
                exit_price=row["exit_price"],
                exit_reason=row["exit_reason"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            )
            self.orders[plan.order_id] = plan

    # Bias snapshot management ---------------------------------------------
    def push_bias(self, snapshot: BiasSnapshot) -> BiasSnapshot:
        snapshot.id = self._insert_bias(snapshot)
        self.bias_snapshots.append(snapshot)
        self.last_bias_id = max(self.last_bias_id, snapshot.id)
        return snapshot

    def latest_bias(self) -> Optional[BiasSnapshot]:
        return self.bias_snapshots[-1] if self.bias_snapshots else None

    # Structure zone management -------------------------------------------
    def push_structure_zone(self, zone: StructureZone) -> StructureZone:
        zone.id = self._insert_structure(zone)
        self.structure_zones.append(zone)
        self.last_structure_id = max(self.last_structure_id, zone.id)
        return zone

    def latest_structure(self) -> Optional[StructureZone]:
        return self.structure_zones[-1] if self.structure_zones else None

    # Execution signals ----------------------------------------------------
    def push_execution_signal(self, signal: ExecutionSignal) -> ExecutionSignal:
        self._insert_signal(signal)
        self.execution_signals.append(signal)
        return signal

    def latest_signal(self) -> Optional[ExecutionSignal]:
        return self.execution_signals[-1] if self.execution_signals else None

    # Orders ----------------------------------------------------------------
    def register_order(self, plan: OrderPlan) -> None:
        self.orders[plan.order_id] = plan
        self._persist_order(plan)

    def update_order_state(
        self,
        order_id: str,
        state: TradeState,
        *,
        when: Optional[datetime] = None,
        reason: str | None = None,
        filled_price: Optional[float] = None,
        exit_price: Optional[float] = None,
    ) -> None:
        order = self.orders.get(order_id)
        if not order:
            return
        order.state = state
        if when and state in {TradeState.FILLED, TradeState.MANAGING}:
            order.filled_at = when
        if filled_price is not None:
            order.filled_price = filled_price
        if state in {TradeState.EXIT, TradeState.CANCELLED}:
            order.exit_reason = reason
            if when and not order.filled_at:
                order.filled_at = when
        if exit_price is not None:
            order.exit_price = exit_price
        self._persist_order(order)

    def record_event_metadata(self, order_id: str, data: Dict[str, str]) -> None:
        order = self.orders.get(order_id)
        if not order:
            return
        order.metadata.update(data)
        self._persist_order(order)

    def get_order(self, order_id: str) -> Optional[OrderPlan]:
        return self.orders.get(order_id)

    def active_orders(self) -> Dict[str, OrderPlan]:
        return {
            order_id: plan
            for order_id, plan in self.orders.items()
            if plan.state not in {TradeState.EXIT, TradeState.CANCELLED}
        }

    def orders_by_state(self, states: Iterable[TradeState]) -> Dict[str, OrderPlan]:
        state_set = set(states)
        return {order_id: plan for order_id, plan in self.orders.items() if plan.state in state_set}

