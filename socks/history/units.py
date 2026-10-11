# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Unit conversion and locale-independent formatting.

The database stores km, kWh and degrees C. Everything here converts only for display.
Output never depends on the process locale (plain format specs, no thousands
separators from the locale).
"""

from __future__ import annotations

from datetime import timedelta

KM_PER_MI = 1.609344
DASH = "—"


def is_imperial(units: str) -> bool:
    return units.lower() == "imperial"


def km_to_mi(km: float) -> float:
    return km / KM_PER_MI


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def fmt_distance(km: float | None, units: str = "metric", decimals: int = 1) -> str:
    if km is None:
        return DASH
    if is_imperial(units):
        return f"{km_to_mi(km):.{decimals}f} mi"
    return f"{km:.{decimals}f} km"


def fmt_energy(kwh: float | None, decimals: int = 1) -> str:
    return DASH if kwh is None else f"{kwh:.{decimals}f} kWh"


def fmt_power(kw: float | None, decimals: int = 1) -> str:
    return DASH if kw is None else f"{kw:.{decimals}f} kW"


def fmt_percent(pct: float | None, decimals: int = 0) -> str:
    return DASH if pct is None else f"{pct:.{decimals}f} %"


def fmt_duration(delta: timedelta) -> str:
    """'2 h 05 min', '35 min', '3 d 4 h'."""
    minutes = int(delta.total_seconds() // 60)
    if minutes < 0:
        minutes = 0
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {mins:02d} min"
    return f"{mins} min"
