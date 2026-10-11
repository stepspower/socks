# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""CSV / JSON export of drives, charges and snapshots, plus the mileage register.

CSV is RFC 4180 (csv module, CRLF, UTF-8). Timestamps are ISO-8601 in the local zone with
UTC offset. Distances are rounded to 1 decimal, energy to 2. Files are written atomically
(temp file in the target directory, then rename) with mode 0600. ``since`` is inclusive
and ``until`` exclusive. Free-text cells starting with = + - @ are prefixed with an
apostrophe so spreadsheets do not execute them as formulas.

Mileage register
----------------
``export_mileage_register_csv`` writes a "sluitende rittenregistratie"-style layout
(one row per trip: date, start/end time, start/end odometer, km, purpose, start/end
place, note), which is the shape the Dutch tax authority expects for a private-use
exemption. This is a convenience export only: the user is responsible for the register
being complete and correct and for complying with the rules (for example, a register must
cover every trip of the year and carry accurate places). SOCKS does not verify
compliance.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

from . import db as dbm
from . import trips, units

KINDS = ("drives", "charges", "snapshots")
_FORMULA_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _local(ts: datetime | str | None, zone: tzinfo) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = dbm.parse_ts(ts)
    return ts.astimezone(zone).isoformat(timespec="seconds")


def _r(v: float | None, n: int) -> float | None:
    return None if v is None else round(v, n)


def _dist(v: float | None, imperial: bool) -> float | None:
    return None if v is None else round(units.km_to_mi(v) if imperial else v, 1)


def _temp(v: float | None, imperial: bool) -> float | None:
    return None if v is None else round(units.c_to_f(v) if imperial else v, 1)


def _text(v: str | None) -> str | None:
    if v and v.startswith(_FORMULA_CHARS):
        return "'" + v
    return v


def _bool(v: int | None) -> bool | None:
    return None if v is None else bool(v)


def _bounds(since: datetime | None, until: datetime | None) -> tuple[str, str]:
    return (
        dbm.iso(since) if since else "",
        dbm.iso(until) if until else "9999-12-31T23:59:59Z",
    )


def _table(
    db: sqlite3.Connection,
    vin: str,
    kind: str,
    since: datetime | None,
    until: datetime | None,
    unit_system: str,
    zone: tzinfo,
) -> tuple[list[str], list[list[Any]]]:
    imp = units.is_imperial(unit_system)
    d = "mi" if imp else "km"
    lo, hi = _bounds(since, until)
    if kind == "drives":
        cols = [
            "id", "start", "end", f"start_odometer_{d}", f"end_odometer_{d}", d,
            "start_soc", "end_soc", "purpose", "start_label", "end_label", "note",
        ]  # fmt: skip
        rows = [
            [
                x.id, _local(x.start, zone), _local(x.end, zone),
                _dist(x.start_odo_km, imp), _dist(x.end_odo_km, imp),
                _dist(x.km if x.end_odo_km is not None else None, imp),
                x.start_soc, x.end_soc, x.purpose,
                _text(x.start_label), _text(x.end_label), _text(x.note),
            ]
            for x in trips.drives(db, vin, since, until)
        ]  # fmt: skip
        return cols, rows
    if kind == "charges":
        cols = [
            "id", "start", "end", "start_soc", "end_soc", "energy_kwh", "max_power_kw",
            "source", "estimated",
        ]  # fmt: skip
        rows = [
            [
                r["id"], _local(r["start_ts"], zone), _local(r["end_ts"], zone),
                r["start_soc"], r["end_soc"], _r(r["energy_added_kwh"], 2),
                _r(r["max_power_kw"], 1), r["source"], bool(r["estimated"]),
            ]
            for r in db.execute(
                "SELECT * FROM charge_session WHERE vin = ? AND start_ts >= ? "
                "AND start_ts < ? ORDER BY start_ts, id",
                (vin, lo, hi),
            )
        ]  # fmt: skip
        return cols, rows
    if kind == "snapshots":
        t = "f" if imp else "c"
        cols = [
            "ts", "source", "soc", "usable_soc", "charge_limit", "charging",
            "charger_power_kw", "charge_energy_added_kwh", f"rated_range_{d}",
            f"odometer_{d}", "shift", "speed_mph" if imp else "speed_kmh",
            f"inside_temp_{t}", f"outside_temp_{t}", "locked", "plugged_in",
        ]  # fmt: skip
        rows = [
            [
                _local(r["ts"], zone), r["source"], r["soc"], r["usable_soc"],
                r["charge_limit"], r["charging"], _r(r["charger_power_kw"], 1),
                _r(r["charge_energy_added_kwh"], 2), _dist(r["rated_range_km"], imp),
                _dist(r["odometer_km"], imp), r["shift"], _dist(r["speed_kmh"], imp),
                _temp(r["inside_temp_c"], imp), _temp(r["outside_temp_c"], imp),
                _bool(r["locked"]), _bool(r["plugged_in"]),
            ]
            for r in db.execute(
                "SELECT * FROM snapshot WHERE vin = ? AND ts >= ? AND ts < ? ORDER BY ts",
                (vin, lo, hi),
            )
        ]  # fmt: skip
        return cols, rows
    raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")


def _write(path: str | os.PathLike[str], text: str) -> None:
    """Atomic, mode 0600; the user's chosen directory is left as it is."""
    from socks.core.keys import write_secret_file

    write_secret_file(Path(path), text.encode("utf-8"), private_dir=False)


def _write_csv(path: str | os.PathLike[str], cols: Sequence[str], rows: list[list[Any]]) -> None:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(cols)
    for row in rows:
        w.writerow(["" if v is None else v for v in row])
    _write(path, buf.getvalue())


def export_csv(
    db: sqlite3.Connection,
    vin: str,
    kind: str,
    path: str | os.PathLike[str],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    units: str = "metric",  # noqa: A002
    tz: tzinfo | None = None,
) -> int:
    """Write one table as CSV. Returns the number of data rows."""
    cols, rows = _table(db, vin, kind, since, until, units, dbm.local_zone(tz))
    _write_csv(path, cols, rows)
    return len(rows)


def export_json(
    db: sqlite3.Connection,
    vin: str,
    kind: str,
    path: str | os.PathLike[str],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    units: str = "metric",  # noqa: A002
    tz: tzinfo | None = None,
) -> int:
    """Write one JSON document {vin, kind, exported_at, units, schema_version, rows}."""
    zone = dbm.local_zone(tz)
    cols, rows = _table(db, vin, kind, since, until, units, zone)
    doc = {
        "vin": vin,
        "kind": kind,
        "exported_at": _local(datetime.now(UTC), zone),
        "units": "imperial" if _imp(units) else "metric",
        "schema_version": int(dbm.get_meta(db, "schema_version", "0") or 0),
        "rows": [dict(zip(cols, row, strict=True)) for row in rows],
    }
    _write(path, json.dumps(doc, indent=2, ensure_ascii=False))
    return len(rows)


def _imp(u: str) -> bool:
    return units.is_imperial(u)


def export_mileage_register_csv(
    db: sqlite3.Connection,
    vin: str,
    year: int,
    path: str | os.PathLike[str],
    units: str = "metric",  # noqa: A002
    tz: tzinfo | None = None,
) -> int:
    """Write the yearly trip register (see module docstring). Returns the trip count."""
    zone = dbm.local_zone(tz)
    imp = _imp(units)
    d = "mi" if imp else "km"
    cols = [
        "date", "start_time", "end_time", f"start_odometer_{d}", f"end_odometer_{d}", d,
        "purpose", "start_label", "end_label", "note",
    ]  # fmt: skip
    rows = []
    for x in trips.year_trips(db, vin, year, zone):
        s, e = x.start.astimezone(zone), x.end.astimezone(zone)
        rows.append([
            s.date().isoformat(), s.strftime("%H:%M"), e.strftime("%H:%M"),
            _dist(x.start_odo_km, imp), _dist(x.end_odo_km, imp), _dist(x.km, imp),
            x.purpose, _text(x.start_label), _text(x.end_label), _text(x.note),
        ])  # fmt: skip
    _write_csv(path, cols, rows)
    return len(rows)


GPX_NS = "http://www.topografix.com/GPX/1/1"
SOCKS_GPX_NS = "https://github.com/stepspower/socks/gpx/1"


def export_gpx(db: sqlite3.Connection, drive_id: int, path: str | os.PathLike[str]) -> int:
    """Write a drive's high-rate track as GPX 1.1; speed/power/soc go in extensions.

    Returns the number of trkpt elements. Raises LookupError for an unknown drive and
    ValueError if the drive has no track points with coordinates.
    """
    from xml.etree import ElementTree as ET  # noqa: S405  (only builds XML, never parses input)

    drive = db.execute("SELECT vin, start_ts FROM drive WHERE id = ?", (drive_id,)).fetchone()
    if drive is None:
        raise LookupError(f"no drive with id {drive_id}")
    pts = db.execute(
        "SELECT ts, lat, lon, speed_kmh, power_kw, soc FROM track_point "
        "WHERE drive_id = ? AND lat IS NOT NULL AND lon IS NOT NULL ORDER BY ts",
        (drive_id,),
    ).fetchall()
    if not pts:
        raise ValueError(f"drive {drive_id} has no track points with coordinates")
    ET.register_namespace("", GPX_NS)
    ET.register_namespace("socks", SOCKS_GPX_NS)
    root = ET.Element(f"{{{GPX_NS}}}gpx", {"version": "1.1", "creator": "SOCKS"})
    trk = ET.SubElement(root, f"{{{GPX_NS}}}trk")
    ET.SubElement(trk, f"{{{GPX_NS}}}name").text = f"Drive {drive_id} {drive['start_ts']}"
    seg = ET.SubElement(trk, f"{{{GPX_NS}}}trkseg")
    for p in pts:
        pt = ET.SubElement(
            seg, f"{{{GPX_NS}}}trkpt", {"lat": f"{p['lat']:.6f}", "lon": f"{p['lon']:.6f}"}
        )
        ET.SubElement(pt, f"{{{GPX_NS}}}time").text = p["ts"]
        ext = ET.SubElement(pt, f"{{{GPX_NS}}}extensions")
        for name, val, fmt in (
            ("speed_kmh", p["speed_kmh"], "{:.1f}"),
            ("power_kw", p["power_kw"], "{:.2f}"),
            ("soc", p["soc"], "{:d}"),
        ):
            if val is not None:
                ET.SubElement(ext, f"{{{SOCKS_GPX_NS}}}{name}").text = fmt.format(val)
    body = ET.tostring(root, encoding="unicode")
    _write(path, '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n")
    return len(pts)
