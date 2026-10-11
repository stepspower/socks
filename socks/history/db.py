# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""SQLite schema, migrations and small parameterised helpers (PLAN section 5).

All SQL in this package is parameterised; values never get formatted into statements.
Timestamps are stored as UTC ISO-8601 text ('2026-03-02T08:00:00Z'), which sorts
lexicographically in time order.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_PACK_KWH = 80.0  # Model 3 Long Range (NCA) usable pack; used until learned


class DbError(Exception):
    pass


_SCHEMA_V1 = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshot (
        vin TEXT NOT NULL,
        ts TEXT NOT NULL,
        source TEXT NOT NULL,
        soc INTEGER,
        usable_soc INTEGER,
        charge_limit INTEGER,
        charging TEXT,
        charger_power_kw REAL,
        charge_energy_added_kwh REAL,
        rated_range_km REAL,
        odometer_km REAL,
        shift TEXT,
        speed_kmh REAL,
        inside_temp_c REAL,
        outside_temp_c REAL,
        locked INTEGER,
        plugged_in INTEGER,
        PRIMARY KEY (vin, ts)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS charge_session (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vin TEXT NOT NULL,
        start_ts TEXT NOT NULL,
        end_ts TEXT,
        start_soc INTEGER,
        end_soc INTEGER,
        energy_added_kwh REAL,
        max_power_kw REAL,
        location_hint TEXT,
        source TEXT,
        estimated INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_charge_vin_start ON charge_session (vin, start_ts)",
    """
    CREATE TABLE IF NOT EXISTS drive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vin TEXT NOT NULL,
        start_ts TEXT NOT NULL,
        end_ts TEXT,
        start_odo_km REAL,
        end_odo_km REAL,
        start_soc INTEGER,
        end_soc INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_drive_vin_start ON drive (vin, start_ts)",
)


def iso(dt: datetime) -> str:
    """UTC text form used for every stored timestamp (naive input is taken as UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _xdg_data_socks() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "socks"


def open(path: str | os.PathLike[str]) -> sqlite3.Connection:  # noqa: A001
    """Open (creating if needed) the history DB and migrate it to the current schema.

    A missing parent directory is created with mode 0700; the XDG data dir for SOCKS
    is tightened to 0700 as well. The DB file itself is set to 0600. ':memory:' works.
    """
    spath = os.fspath(path)
    in_memory = spath == ":memory:"
    if not in_memory:
        p = Path(spath)
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if p.parent.resolve() == _xdg_data_socks().resolve():
            p.parent.chmod(0o700)
    # isolation_level=None: autocommit, transactions are explicit via transaction().
    conn = sqlite3.connect(spath, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not in_memory:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            Path(spath).chmod(0o600)
        except OSError:
            pass
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    has_meta = (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        is not None
    )
    current = 0
    if has_meta:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        current = int(row["value"]) if row else 0
    if current > SCHEMA_VERSION:
        raise DbError(f"history DB schema {current} is newer than supported {SCHEMA_VERSION}")
    if current < 1:
        with transaction(conn):
            for stmt in _SCHEMA_V1:
                conn.execute(stmt)
            set_meta(conn, "schema_version", str(SCHEMA_VERSION))


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, ROLLBACK on error. Nested use joins the outer one."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def execute(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
    return conn.execute(sql, tuple(params))


def query(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, tuple(params)).fetchall()


def query_one(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, tuple(params)).fetchone()


def scalar(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = (), default: Any = None):
    row = conn.execute(sql, tuple(params)).fetchone()
    return default if row is None or row[0] is None else row[0]


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def del_meta(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM meta WHERE key = ?", (key,))
