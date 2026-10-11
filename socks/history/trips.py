# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Trip classification (business / private / commute) and yearly mileage totals.

Location labels are free text typed by the user. No coordinates are stored; the
start_lat/start_lon/end_lat/end_lon columns are reserved for a possible future opt-in.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo

from . import db as dbm
from . import places

PURPOSES = dbm.PURPOSES
MIN_TRIP_KM = 0.05  # moves shorter than this (driveway shuffles) are not trips
MAX_TEXT = 500


@dataclass(frozen=True)
class Drive:
    id: int
    vin: str
    start: datetime
    end: datetime
    start_odo_km: float | None
    end_odo_km: float | None
    start_soc: int | None
    end_soc: int | None
    purpose: str | None = None
    note: str | None = None
    start_label: str | None = None
    end_label: str | None = None
    start_lat: float | None = None
    start_lon: float | None = None
    end_lat: float | None = None
    end_lon: float | None = None

    @property
    def km(self) -> float:
        if self.start_odo_km is None or self.end_odo_km is None:
            return 0.0
        return max(0.0, self.end_odo_km - self.start_odo_km)


@dataclass(frozen=True)
class MileageSummary:
    total_km: float = 0.0
    business_km: float = 0.0
    private_km: float = 0.0
    commute_km: float = 0.0
    unclassified_km: float = 0.0
    drives: int = 0


def _clean(text: str | None) -> str | None:
    return None if text is None else text.strip()[:MAX_TEXT]


def set_drive_purpose(
    db: sqlite3.Connection, drive_id: int, purpose: str | None, note: str | None = None
) -> None:
    """Set the purpose (None clears it). ``note=None`` leaves the note unchanged; pass
    an empty string to clear it."""
    if purpose is not None and purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES} or None, got {purpose!r}")
    with dbm.transaction(db):
        cur = dbm.execute(
            db,
            "UPDATE drive SET purpose = ?, purpose_source = ?, note = COALESCE(?, note) "
            "WHERE id = ?",
            (purpose, "user" if purpose else None, _clean(note), drive_id),
        )
        if cur.rowcount == 0:
            raise LookupError(f"no drive with id {drive_id}")
        if purpose:  # remember it for the destination place
            row = dbm.query_one(
                db, "SELECT vin, end_lat, end_lon FROM drive WHERE id = ?", (drive_id,)
            )
            if row and row["end_lat"] is not None:
                place = places.place_at(db, row["vin"], row["end_lat"], row["end_lon"])
                if place:
                    places.set_place_purpose(db, place.id, purpose)


def set_drive_labels(
    db: sqlite3.Connection,
    drive_id: int,
    start_label: str | None = None,
    end_label: str | None = None,
) -> None:
    """Set free-text place labels; None leaves a label unchanged, '' clears it."""
    with dbm.transaction(db):
        cur = dbm.execute(
            db,
            "UPDATE drive SET start_label = COALESCE(?, start_label), "
            "end_label = COALESCE(?, end_label), label_source = 'user' WHERE id = ?",
            (_clean(start_label), _clean(end_label), drive_id),
        )
        if cur.rowcount == 0:
            raise LookupError(f"no drive with id {drive_id}")


def drives(
    db: sqlite3.Connection,
    vin: str,
    since: datetime | None = None,
    until: datetime | None = None,
    purpose: str | None = None,
) -> list[Drive]:
    """Drives starting in [since, until), oldest first; optional purpose filter."""
    rows = dbm.query(
        db,
        "SELECT * FROM drive WHERE vin = ? AND start_ts >= ? AND start_ts < ? "
        "AND (? IS NULL OR purpose = ?) ORDER BY start_ts, id",
        (
            vin,
            dbm.iso(since) if since else "",
            dbm.iso(until) if until else "9999-12-31T23:59:59Z",
            purpose,
            purpose,
        ),
    )
    return [
        Drive(
            id=r["id"],
            vin=r["vin"],
            start=dbm.parse_ts(r["start_ts"]),
            end=dbm.parse_ts(r["end_ts"] or r["start_ts"]),
            start_odo_km=r["start_odo_km"],
            end_odo_km=r["end_odo_km"],
            start_soc=r["start_soc"],
            end_soc=r["end_soc"],
            purpose=r["purpose"],
            note=r["note"],
            start_label=r["start_label"],
            end_label=r["end_label"],
            start_lat=r["start_lat"],
            start_lon=r["start_lon"],
            end_lat=r["end_lat"],
            end_lon=r["end_lon"],
        )
        for r in rows
    ]


def year_bounds(year: int, tz: tzinfo | None = None) -> tuple[datetime, datetime]:
    zone = tz or datetime.now().astimezone().tzinfo or UTC
    return (
        datetime(year, 1, 1, tzinfo=zone).astimezone(UTC),
        datetime(year + 1, 1, 1, tzinfo=zone).astimezone(UTC),
    )


def year_trips(
    db: sqlite3.Connection, vin: str, year: int, tz: tzinfo | None = None
) -> list[Drive]:
    start, end = year_bounds(year, tz)
    return [d for d in drives(db, vin, start, end) if d.km >= MIN_TRIP_KM]


def mileage_summary(
    db: sqlite3.Connection, vin: str, year: int, tz: tzinfo | None = None
) -> MileageSummary:
    by = {"business": 0.0, "private": 0.0, "commute": 0.0, None: 0.0}
    trips = year_trips(db, vin, year, tz)
    for d in trips:
        by[d.purpose] += d.km
    return MileageSummary(
        total_km=sum(by.values()),
        business_km=by["business"],
        private_km=by["private"],
        commute_km=by["commute"],
        unclassified_km=by[None],
        drives=len(trips),
    )
