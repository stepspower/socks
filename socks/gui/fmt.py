# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Display formatting for the GUI. Raw values are metric (km, kWh, °C)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import Any

KM_PER_MI = 1.609344
DASH = "–"


def imperial(units: str) -> bool:
    return units == "imperial"


def dist(km: float | None, units: str, digits: int = 0) -> str:
    if km is None:
        return DASH
    if imperial(units):
        return f"{km / KM_PER_MI:,.{digits}f} mi"
    return f"{km:,.{digits}f} km"


def speed(kmh: float | None, units: str) -> str:
    if kmh is None:
        return DASH
    return f"{kmh / KM_PER_MI:.0f} mph" if imperial(units) else f"{kmh:.0f} km/h"


def temp(c: float | None, units: str) -> str:
    if c is None:
        return DASH
    return f"{c * 9 / 5 + 32:.1f} °F" if imperial(units) else f"{c:.1f} °C"


def temp_to_display(c: float, units: str) -> float:
    return c * 9 / 5 + 32 if imperial(units) else c


def temp_from_display(v: float, units: str) -> float:
    return (v - 32) * 5 / 9 if imperial(units) else v


def power(kw: float | None) -> str:
    return DASH if kw is None else f"{kw:.1f} kW"


def energy(kwh: float | None) -> str:
    return DASH if kwh is None else f"{kwh:.1f} kWh"


def efficiency(wh_per_km: float | None, units: str) -> str:
    if wh_per_km is None:
        return DASH
    if imperial(units):
        return f"{wh_per_km * KM_PER_MI:.0f} Wh/mi"
    return f"{wh_per_km:.0f} Wh/km"


def minutes(m: int | None) -> str:
    if m is None:
        return DASH
    h, mm = divmod(int(m), 60)
    return f"{h} h {mm:02d} min" if h else f"{mm} min"


def age(ts: datetime | None, now: datetime | None = None) -> str:
    if ts is None:
        return "never"
    now = now or datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    s = (now - ts).total_seconds()
    if s < 45:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} d ago"


def local_time(ts: datetime | None, with_date: bool = False) -> str:
    if ts is None:
        return DASH
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    lt = ts.astimezone()
    return lt.strftime("%Y-%m-%d %H:%M") if with_date else lt.strftime("%H:%M:%S")


def yes_no(b: bool | None, yes: str = "yes", no: str = "no") -> str:
    return DASH if b is None else (yes if b else no)


LABELS = {
    "total_km": "Distance in period",
    "km_7d": "Distance, last 7 days",
    "km_30d": "Distance, last 30 days",
    "kwh_charged_30d": "Charged, last 30 days",
    "cost_30d": "Charging cost, last 30 days",
    "sessions_30d": "Charge sessions, last 30 days",
    "avg_wh_per_km": "Consumption (estimate)",
    "longest_drive_km": "Longest drive",
    "best_day_km": "Best day",
    "start_soc": "Start SoC",
    "end_soc": "End SoC",
    "energy_kwh": "Energy",
    "max_power_kw": "Max power",
    "first_range_at_100_km": "First seen",
    "latest_range_at_100_km": "Latest",
}


def humanize(name: str) -> str:
    """``energy_added_kwh`` → ``Energy added``."""
    if name in LABELS:
        return LABELS[name]
    skip = {"kwh", "km", "kw", "pct", "eur", "h", "c", "estimate", "ts"}
    words = []
    for w in name.split("_"):
        if w in skip:
            continue
        if w[:-1].isdigit() and w[-1] == "d":
            w = f"({w[:-1]} days)"
        words.append(w)
    text = " ".join(words) or name
    if name.endswith("_estimate"):
        text += " (est.)"
    return text[:1].upper() + text[1:]


def field_value(name: str, value: Any, units: str, currency: str = "EUR") -> str:
    """Format a history-stats field by its name suffix (km, kwh, pct, …)."""
    if value is None:
        return DASH
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return local_time(value, with_date=True)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, timedelta):
        return minutes(int(value.total_seconds() // 60))
    if isinstance(value, bool):
        return yes_no(value)
    if not isinstance(value, int | float):
        return str(value)
    n = name.lower()
    if "wh_per_km" in n:
        return efficiency(value, units)
    if n.endswith("_km") or n == "km" or "_km_" in n or n.startswith("km_"):
        return dist(value, units, 1)
    if "kwh" in n:
        return energy(value)
    if n.endswith("_kw"):
        return power(value)
    if n.endswith(("_pct", "soc", "_percent")) or "soc" in n:
        return f"{value:.1f} %" if isinstance(value, float) else f"{value} %"
    if n.startswith(("hours_", "time_")) or n.endswith(("_h", "_hours")):
        return f"{value:,.1f} h"
    if n.endswith(("_s", "_seconds")):
        return minutes(int(value // 60))
    if n.endswith("_c"):
        return temp(value, units)
    if "cost" in n or n.endswith("_eur"):
        return f"{value:,.2f} {currency}"
    if isinstance(value, float):
        return f"{value:,.1f}"
    return f"{value:,}"
