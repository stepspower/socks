# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Value → command mapping for the Status tab's click-to-change controls (pure, testable).

Every result goes through MainWindow._on_command, i.e. the single confirm gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from socks.gui import fmt


@dataclass(frozen=True)
class Toggle:
    field: str
    on_cmd: str  # command value sent to turn it on (e.g. "lock")
    off_cmd: str
    on_text: str
    off_text: str


TOGGLES: dict[str, Toggle] = {
    t.field: t
    for t in (
        Toggle("locked", "lock", "unlock", "Locked", "Unlocked"),
        Toggle("climate_on", "climate_on", "climate_off", "Climate on", "Climate off"),
        Toggle("sentry_on", "sentry_on", "sentry_off", "Sentry on", "Sentry off"),
        Toggle(
            "charge_port_open", "charge_port_open", "charge_port_close", "Port open", "Port closed"
        ),
        Toggle("charging", "charge_start", "charge_stop", "Charging", "Not charging"),
    )
}
CHARGING_ACTIVE = {"CHARGING", "STARTING"}

# fallbacks when core's CommandInfo carries no min/max/step/unit
ARG_DEFAULTS: dict[str, tuple[float, float, float, str]] = {
    "temp_c": (15.0, 28.0, 0.5, "°C"),
    "percent": (50, 100, 1, "%"),
    "amps": (5, 32, 1, "A"),
    "minutes_after_midnight": (0, 1439, 1, "min"),
}


def is_on(field: str, value: Any) -> bool | None:
    if field == "charging":
        if value is None:
            return None
        return getattr(value, "name", str(value)) in CHARGING_ACTIVE
    return None if value is None else bool(value)


def toggle_text(field: str, value: Any) -> str:
    t = TOGGLES[field]
    state = is_on(field, value)
    if state is None:
        return f"{t.on_text.split()[0]} ?"
    return t.on_text if state else t.off_text


def toggle_command(field: str, value: Any) -> str:
    """The command value a click sends: the opposite of the current state."""
    t = TOGGLES[field]
    return t.off_cmd if is_on(field, value) else t.on_cmd


def spec(info: Any) -> tuple[float, float, float, str]:
    """(min, max, step, unit) from CommandInfo, falling back to ARG_DEFAULTS."""
    lo, hi, step, unit = ARG_DEFAULTS.get(getattr(info, "arg", None) or "", (0, 100, 1, ""))
    return (
        getattr(info, "min", None) if getattr(info, "min", None) is not None else lo,
        getattr(info, "max", None) if getattr(info, "max", None) is not None else hi,
        getattr(info, "step", None) if getattr(info, "step", None) is not None else step,
        getattr(info, "unit", None) or unit,
    )


def display_range(info: Any, units: str) -> tuple[float, float, float, str]:
    """Editor range in display units (°F for temperature in imperial)."""
    lo, hi, step, unit = spec(info)
    if getattr(info, "arg", None) == "temp_c" and fmt.imperial(units):
        return fmt.temp_to_display(lo, units), fmt.temp_to_display(hi, units), 1.0, "°F"
    return lo, hi, step, unit


def arg_for(info: Any, display_value: Any, units: str) -> float | int | None:
    """Editor value (display units / minutes / None) → the command argument."""
    kind = getattr(info, "arg", None)
    lo, hi, step, _unit = spec(info)
    if display_value is None:
        if getattr(info, "arg_optional", False):
            return None
        raise ValueError(f"{getattr(info, 'label', kind)} needs a value")
    if kind == "temp_c":
        c = fmt.temp_from_display(float(display_value), units)
        c = round(c / step) * step if step else c
        return round(min(hi, max(lo, c)), 1)
    v = int(round(float(display_value)))
    return int(min(hi, max(lo, v)))


def minutes_of(dt: Any) -> int | None:
    """Local minutes after midnight of a UTC datetime (None stays None)."""
    if dt is None:
        return None
    local = dt.astimezone()
    return local.hour * 60 + local.minute


def hhmm(minutes: int | None) -> str:
    if minutes is None:
        return "off"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
