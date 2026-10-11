# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""History DB access for the GUI. Everything here blocks: run it on the bridge's I/O thread."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from socks.history import db as dbm
from socks.history import places, stats, trips

T = TypeVar("T")


@dataclass
class HistoryData:
    summary: Any = None
    daily: list[Any] = field(default_factory=list)
    sessions: list[Any] = field(default_factory=list)
    soc: list[tuple[datetime, int]] = field(default_factory=list)
    health: Any = None
    nerd: dict[str, str] = field(default_factory=dict)
    drives: list[Any] = field(default_factory=list)
    places: list[Any] = field(default_factory=list)
    mileage: Any = None
    year: int = 0


def with_db[T](path: Path, fn: Callable[[sqlite3.Connection], T]) -> T:
    """Open the history DB, run ``fn(db)``, close it."""
    db = dbm.open(path)
    try:
        return fn(db)
    finally:
        db.close()


def load_history(
    path: Path, vin: str, since: datetime | None, days: int, tariff: float, units: str
) -> HistoryData:
    year = datetime.now().year

    def go(db: sqlite3.Connection) -> HistoryData:
        return HistoryData(
            summary=stats.summary(db, vin, since, tariff=tariff),
            daily=stats.daily_km(db, vin, days),
            sessions=stats.charge_sessions(db, vin, 50, tariff=tariff),
            soc=stats.soc_series(db, vin, since),
            health=stats.battery_health(db, vin),
            nerd=stats.nerd_stats(db, vin, units_=units),
            drives=trips.drives(db, vin, since)[::-1],  # newest first
            places=places.list_places(db, vin),
            mileage=trips.mileage_summary(db, vin, year),
            year=year,
        )

    return with_db(path, go)


def seed_fake_history(path: Path, vin: str, days: int = 45) -> None:
    """Fill a throwaway DB with synthetic states (tests/fakes.py) through the real Recorder."""
    from socks.core.backend import fakes_module
    from socks.history.recorder import Recorder

    rec = Recorder(path)
    try:
        for s in fakes_module().synthetic_states(vin, days):
            rec.record(s)
    finally:
        rec.close()

    def learn(db: sqlite3.Connection) -> None:
        places.learn_places(db, vin)
        places.relabel_drives(db, vin)

    with_db(path, learn)
