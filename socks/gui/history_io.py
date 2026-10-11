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
    parked: Any = None
    energy_split: tuple[float, float] = (0.0, 0.0)  # (measured, estimated) kWh, this year


def with_db[T](path: Path, fn: Callable[[sqlite3.Connection], T]) -> T:
    """Open the history DB, run ``fn(db)``, close it."""
    db = dbm.open(path)
    try:
        return fn(db)
    finally:
        db.close()


def _energy_split(db: sqlite3.Connection, vin: str, year: int) -> tuple[float, float]:
    """Drive energy this year: (measured by power integration, estimated from SoC) kWh."""
    lo, hi = (dbm.iso(t) for t in trips.year_bounds(year))
    out = {"measured": 0.0, "estimated": 0.0}
    for source, kwh in db.execute(
        "SELECT energy_source, COALESCE(SUM(energy_kwh), 0) FROM drive "
        "WHERE vin = ? AND start_ts >= ? AND start_ts < ? AND energy_source IS NOT NULL "
        "GROUP BY energy_source",
        (vin, lo, hi),
    ):
        out[source] = float(kwh)
    return out["measured"], out["estimated"]


def drive_detail(path: Path, drive_id: int) -> tuple[list[Any], bool]:
    """(drive_profile points, whether a GPX export is possible) for one drive."""

    def go(db: sqlite3.Connection) -> tuple[list[Any], bool]:
        has_gps = (
            db.execute(
                "SELECT 1 FROM track_point WHERE drive_id = ? AND lat IS NOT NULL "
                "AND lon IS NOT NULL LIMIT 1",
                (drive_id,),
            ).fetchone()
            is not None
        )
        return stats.drive_profile(db, drive_id), has_gps

    return with_db(path, go)


def export_gpx(path: Path, drive_id: int, out: str) -> int:
    from socks.history import export

    return with_db(path, lambda db: export.export_gpx(db, drive_id, out))


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
            parked=stats.parked_stats(db, vin, since),
            energy_split=_energy_split(db, vin, year),
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
