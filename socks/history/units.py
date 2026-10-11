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


def mi_to_km(mi: float) -> float:
    return mi * KM_PER_MI


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def kwh_to_wh(kwh: float) -> float:
    return kwh * 1000.0


def wh_to_kwh(wh: float) -> float:
    return wh / 1000.0


def wh_per_km_to_wh_per_mi(wh_per_km: float) -> float:
    return wh_per_km * KM_PER_MI


def fmt_distance(km: float | None, units: str = "metric", decimals: int = 1) -> str:
    if km is None:
        return DASH
    if is_imperial(units):
        return f"{km_to_mi(km):.{decimals}f} mi"
    return f"{km:.{decimals}f} km"


def fmt_speed(kmh: float | None, units: str = "metric") -> str:
    if kmh is None:
        return DASH
    if is_imperial(units):
        return f"{km_to_mi(kmh):.0f} mph"
    return f"{kmh:.0f} km/h"


def fmt_temp(c: float | None, units: str = "metric", decimals: int = 1) -> str:
    if c is None:
        return DASH
    if is_imperial(units):
        return f"{c_to_f(c):.{decimals}f} °F"
    return f"{c:.{decimals}f} °C"


def fmt_energy(kwh: float | None, decimals: int = 1) -> str:
    return DASH if kwh is None else f"{kwh:.{decimals}f} kWh"


def fmt_power(kw: float | None, decimals: int = 1) -> str:
    return DASH if kw is None else f"{kw:.{decimals}f} kW"


def fmt_efficiency(wh_per_km: float | None, units: str = "metric") -> str:
    if wh_per_km is None:
        return DASH
    if is_imperial(units):
        return f"{wh_per_km_to_wh_per_mi(wh_per_km):.0f} Wh/mi"
    return f"{wh_per_km:.0f} Wh/km"


def fmt_percent(pct: float | None, decimals: int = 0) -> str:
    return DASH if pct is None else f"{pct:.{decimals}f} %"


def fmt_money(amount: float | None, currency: str = "EUR") -> str:
    return DASH if amount is None else f"{amount:.2f} {currency}"


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
