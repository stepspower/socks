# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""High-rate drive tracks: energy integration done when a drive is closed."""

from __future__ import annotations

import sqlite3

from . import db as dbm

TRACK_MAX_DT_S = 60.0  # reads further apart than this are not high-rate track points
MIN_COVERAGE = 0.9  # share of the drive duration the track must cover to call it measured


def integrate_kwh(db: sqlite3.Connection, drive_id: int) -> tuple[float, float]:
    """Trapezoid integral of power_kw over the drive's track: (kWh, seconds covered).

    Segments longer than TRACK_MAX_DT_S or with a missing power value are skipped.
    Negative power (regeneration) subtracts.
    """
    kwh = covered = 0.0
    prev: tuple[float, float] | None = None
    for ts, power in dbm.tuples(db).execute(
        "SELECT CAST(strftime('%s', ts) AS INTEGER), power_kw FROM track_point "
        "WHERE drive_id = ? ORDER BY ts",
        (drive_id,),
    ):
        if prev is not None and power is not None and prev[1] is not None:
            dt = ts - prev[0]
            if 0 < dt <= TRACK_MAX_DT_S:
                kwh += (prev[1] + power) / 2.0 * dt / 3600.0
                covered += dt
        prev = (ts, power) if power is not None else (ts, None)  # type: ignore[assignment]
    return kwh, covered


def finalize_drive(db: sqlite3.Connection, drive_id: int, pack_kwh: float) -> None:
    """Store energy_kwh/energy_source on a closed drive.

    'measured' when the track covers >= 90 % of the drive; otherwise 'estimated' from
    the SoC drop times pack_kwh (only if SoC fell); otherwise left empty.
    """
    row = db.execute(
        "SELECT CAST(strftime('%s', end_ts) AS INTEGER) "
        "- CAST(strftime('%s', start_ts) AS INTEGER) AS dur, start_soc, end_soc "
        "FROM drive WHERE id = ?",
        (drive_id,),
    ).fetchone()
    if row is None:
        return
    kwh, covered = integrate_kwh(db, drive_id)
    if row["dur"] and row["dur"] > 0 and covered >= MIN_COVERAGE * row["dur"]:
        energy, source = kwh, "measured"
    elif (
        row["start_soc"] is not None
        and row["end_soc"] is not None
        and (row["end_soc"] < row["start_soc"])
    ):
        energy, source = (row["start_soc"] - row["end_soc"]) / 100.0 * pack_kwh, "estimated"
    else:
        energy, source = None, None
    db.execute(
        "UPDATE drive SET energy_kwh = ?, energy_source = ? WHERE id = ?",
        (energy, source, drive_id),
    )
