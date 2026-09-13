"""
Persists backtest results to a local SQLite file so the UI can show history
across restarts. Deliberately built on Python's stdlib `sqlite3` rather than
adding SQLAlchemy/asyncpg/a Postgres dependency:

- Zero extra dependency to install — `sqlite3` ships with CPython on
  Windows, Linux, and macOS. No compiled/platform-specific wheels, no
  version-matching headaches, nothing that can fail to build on a machine
  without a C toolchain (a real, common failure mode for asyncpg/psycopg on
  Windows in particular).
- One file on disk (`Settings.sqlite_path`), trivially backed up, trivially
  deleted to reset state — appropriate for a local-first, single-user tool.

`sqlite3` connections are synchronous and not safe to share across threads
without care, so every operation here opens a short-lived connection and
runs it via `asyncio.to_thread` — correct or fast enough for the tiny,
infrequent read/write volume a local research tool actually generates.
There is deliberately no in-process connection pool: it would add
complexity to solve a performance problem this workload doesn't have.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from app.sandbox.backtest import BacktestResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_results (
    run_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    as_of TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    price_at_as_of REAL NOT NULL,
    predicted_price REAL NOT NULL,
    predicted_return_pct REAL NOT NULL,
    actual_price REAL NOT NULL,
    actual_return_pct REAL NOT NULL,
    absolute_pct_error REAL NOT NULL,
    directional_hit INTEGER NOT NULL,
    self_check_passed INTEGER NOT NULL,
    self_check_detail TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_backtest_ticker ON backtest_results (ticker);
CREATE INDEX IF NOT EXISTS idx_backtest_recorded_at ON backtest_results (recorded_at);
"""


class BacktestHistoryStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        if db_path not in (":memory:", ""):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _record_sync(self, result: BacktestResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO backtest_results (
                    run_id, ticker, as_of, horizon_days, price_at_as_of,
                    predicted_price, predicted_return_pct, actual_price,
                    actual_return_pct, absolute_pct_error, directional_hit,
                    self_check_passed, self_check_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id, result.ticker, result.as_of.isoformat(), result.horizon_days,
                    result.price_at_as_of, result.predicted_price, result.predicted_return_pct,
                    result.actual_price, result.actual_return_pct, result.absolute_pct_error,
                    int(result.directional_hit), int(result.self_check_passed), result.self_check_detail,
                ),
            )

    async def record(self, result: BacktestResult) -> None:
        await asyncio.to_thread(self._record_sync, result)

    def _list_recent_sync(self, ticker: str | None, limit: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if ticker:
                rows = conn.execute(
                    "SELECT * FROM backtest_results WHERE ticker = ? "
                    "ORDER BY recorded_at DESC LIMIT ?",
                    (ticker, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM backtest_results ORDER BY recorded_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

    async def list_recent(self, ticker: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_recent_sync, ticker, limit)

    def _count_sync(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM backtest_results").fetchone()
            return int(row["n"])

    async def count(self) -> int:
        return await asyncio.to_thread(self._count_sync)
