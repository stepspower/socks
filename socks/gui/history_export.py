# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""History export: which items exist, default file names, and running one (I/O thread).

All data comes from ``socks.history.export``; this module only maps menu choices to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

KINDS = ("drives", "charges", "snapshots")
FORMATS = ("csv", "json")


@dataclass(frozen=True)
class ExportItem:
    kind: str  # drives | charges | snapshots | mileage
    fmt: str  # csv | json
    label: str


def export_items(year: int) -> list[ExportItem]:
    items = [ExportItem(k, f, f"{k.capitalize()} ({f.upper()})") for k in KINDS for f in FORMATS]
    items.append(ExportItem("mileage", "csv", f"Mileage register {year} (CSV)"))
    return items


def _day(ts: datetime | None) -> str:
    return ts.astimezone().date().isoformat() if ts is not None else "start"


def default_filename(
    item: ExportItem, since: datetime | None, until: datetime | None, year: int
) -> str:
    if item.kind == "mileage":
        return f"socks-mileage-register-{year}.csv"
    end = _day(until) if until is not None else date.today().isoformat()
    return f"socks-{item.kind}-{_day(since)}_{end}.{item.fmt}"


def ensure_suffix(path: str, fmt: str) -> str:
    return path if path.lower().endswith(f".{fmt}") else f"{path}.{fmt}"


def run_export(
    db_path: Path,
    vin: str,
    item: ExportItem,
    path: str,
    since: datetime | None,
    until: datetime | None,
    units: str,
    year: int,
) -> int:
    """Blocking: open the DB, write the file (0600, atomic), return the row count."""
    from socks.gui._deps import open_db
    from socks.history import export

    db = open_db(db_path)
    try:
        if item.kind == "mileage":
            return int(export.export_mileage_register_csv(db, vin, year, path, units))
        fn = export.export_json if item.fmt == "json" else export.export_csv
        return int(fn(db, vin, item.kind, path, since=since, until=until, units=units))
    finally:
        db.close()


def route_text(start: str | None, end: str | None) -> str:
    if not start and not end:
        return ""
    return f"{start or '?'} → {end or '?'}"


def drive_rows(drives: list[Any]) -> list[dict[str, Any]]:
    """Read-only Drives table rows: date, times, km, duration, avg speed, SoC."""
    rows = []
    for d in drives:
        start, end = d.start.astimezone(), d.end.astimezone()
        km = float(getattr(d, "km", 0.0) or 0.0)
        hours = max(0.0, (end - start).total_seconds() / 3600)
        rows.append(
            {
                "date": start.date().isoformat(),
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "km": km,
                "minutes": round(hours * 60),
                "avg_kmh": km / hours if hours >= 1 / 60 and km > 0 else None,
                "start_soc": d.start_soc,
                "end_soc": d.end_soc,
                "id": getattr(d, "id", None),
                "route": route_text(getattr(d, "start_label", None), getattr(d, "end_label", None)),
                "purpose": getattr(d, "purpose", None),
            }
        )
    return rows
