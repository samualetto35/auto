"""Persistent trade and equity logging utilities."""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional

from .models import OrderEventType, OrderPlan
from .risk import AccountState


def _ensure_directory(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


@dataclass(slots=True)
class TradeLogger:
    """Structured trade logger backed by SQLite with optional exports."""

    db_path: str
    export_dir: Optional[str] = None
    _conn: sqlite3.Connection = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _ensure_directory(self.db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        if self.export_dir:
            os.makedirs(self.export_dir, exist_ok=True)

    # -- Schema -----------------------------------------------------------
    def _init_schema(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                symbol TEXT,
                direction TEXT,
                size INTEGER,
                rr REAL,
                entry REAL,
                stop REAL,
                target REAL,
                created_at TEXT,
                exit_at TEXT,
                status TEXT,
                pnl REAL,
                pnl_r REAL,
                metadata TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                order_id TEXT,
                action TEXT,
                price REAL,
                rr REAL,
                pnl REAL,
                pnl_r REAL,
                reason TEXT,
                equity REAL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS equity (
                timestamp TEXT PRIMARY KEY,
                equity REAL,
                daily_pnl REAL,
                weekly_pnl REAL,
                drawdown REAL,
                max_drawdown REAL
            )
            """
        )
        self._conn.commit()

    # -- Logging ----------------------------------------------------------
    def log_order_created(self, order: OrderPlan) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO orders (
                order_id, symbol, direction, size, rr, entry, stop, target, created_at,
                exit_at, status, pnl, pnl_r, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.order_id,
                order.symbol,
                order.direction.value,
                order.size,
                order.rr,
                order.entry,
                order.stop,
                order.target,
                order.created_at.isoformat(),
                order.filled_at.isoformat() if order.filled_at else None,
                order.state.value,
                None,
                None,
                json.dumps(order.metadata or {}),
            ),
        )
        self._conn.commit()

    def log_order_event(
        self,
        order: OrderPlan,
        timestamp: datetime,
        action: OrderEventType,
        *,
        price: Optional[float] = None,
        pnl: Optional[float] = None,
        pnl_r: Optional[float] = None,
        reason: Optional[str] = None,
        equity: Optional[float] = None,
    ) -> None:
        self.log_order_created(order)
        self._conn.execute(
            """
            INSERT INTO events (timestamp, order_id, action, price, rr, pnl, pnl_r, reason, equity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp.isoformat(),
                order.order_id,
                action.value,
                price,
                order.rr,
                pnl,
                pnl_r,
                reason,
                equity,
            ),
        )
        if action in {OrderEventType.TP_HIT, OrderEventType.SL_HIT, OrderEventType.TIME_STOP, OrderEventType.CANCELLED, OrderEventType.EXPIRED}:
            self._conn.execute(
                """
                UPDATE orders
                SET exit_at = ?, status = ?, pnl = COALESCE(?, pnl), pnl_r = COALESCE(?, pnl_r), metadata = ?
                WHERE order_id = ?
                """,
                (
                    timestamp.isoformat(),
                    action.value,
                    pnl,
                    pnl_r,
                    json.dumps(order.metadata or {}),
                    order.order_id,
                ),
            )
        else:
            self._conn.execute(
                "UPDATE orders SET status = ?, metadata = ? WHERE order_id = ?",
                (
                    action.value,
                    json.dumps(order.metadata or {}),
                    order.order_id,
                ),
            )
        self._conn.commit()

    def log_account_update(self, account: AccountState, timestamp: datetime, reason: str | None = None) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO equity (timestamp, equity, daily_pnl, weekly_pnl, drawdown, max_drawdown)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp.isoformat(),
                account.equity,
                account.daily_pnl,
                account.weekly_pnl,
                account.current_drawdown,
                account.max_drawdown,
            ),
        )
        if reason:
            self._conn.execute(
                """
                INSERT INTO events (timestamp, order_id, action, reason, equity)
                VALUES (?, NULL, ?, ?, ?)
                """,
                (
                    timestamp.isoformat(),
                    "ACCOUNT_UPDATE",
                    reason,
                    account.equity,
                ),
            )
        self._conn.commit()

    # -- Reporting ---------------------------------------------------------
    def summarize_trades(self) -> Dict[str, float]:
        cursor = self._conn.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE status IN ('TP_HIT', 'SL_HIT', 'TIME_STOP')) AS closed_trades,
                SUM(pnl) AS total_pnl,
                AVG(pnl_r) AS avg_rr,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS winners
            FROM orders
            WHERE status IN ('TP_HIT', 'SL_HIT', 'TIME_STOP')
            """
        )
        row = cursor.fetchone()
        closed = row["closed_trades"] or 0
        winners = row["winners"] or 0
        win_rate = (winners / closed) if closed else 0.0
        return {
            "closed_trades": closed,
            "total_pnl": row["total_pnl"] or 0.0,
            "avg_rr": row["avg_rr"] or 0.0,
            "win_rate": win_rate,
        }

    def export_daily_csv(self, day: Optional[date] = None) -> Path:
        target_day = day or datetime.utcnow().date()
        file_path = Path(self.export_dir or os.path.dirname(self.db_path)) / f"trades-{target_day.isoformat()}.csv"
        os.makedirs(file_path.parent, exist_ok=True)
        cursor = self._conn.execute(
            "SELECT * FROM events WHERE date(timestamp) = ? ORDER BY timestamp",
            (target_day.isoformat(),),
        )
        rows = cursor.fetchall()
        with open(file_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "order_id", "action", "price", "rr", "pnl", "pnl_r", "reason", "equity"])
            for row in rows:
                writer.writerow(
                    [
                        row["timestamp"],
                        row["order_id"],
                        row["action"],
                        row["price"],
                        row["rr"],
                        row["pnl"],
                        row["pnl_r"],
                        row["reason"],
                        row["equity"],
                    ]
                )
        return file_path

