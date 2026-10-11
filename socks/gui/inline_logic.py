# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Value → command mapping for the Status tab's click-to-change controls (pure, testable).

Every result goes through MainWindow._on_command, i.e. the single confirm gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

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
        return value.name in CHARGING_ACTIVE
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
    lo, hi, step, unit = ARG_DEFAULTS.get(info.arg or "", (0, 100, 1, ""))
    return (
        info.min if info.min is not None else lo,
        info.max if info.max is not None else hi,
        info.step if info.step is not None else step,
        info.unit or unit,
    )


def display_range(info: Any, units: str) -> tuple[float, float, float, str]:
    """Editor range in display units (°F for temperature in imperial)."""
    lo, hi, step, unit = spec(info)
    if info.arg == "temp_c" and fmt.imperial(units):
        return fmt.temp_to_display(lo, units), fmt.temp_to_display(hi, units), 1.0, "°F"
    return lo, hi, step, unit


def arg_for(info: Any, display_value: Any, units: str) -> float | int | None:
    """Editor value (display units / minutes / None) → the command argument."""
    kind = info.arg
    lo, hi, step, _unit = spec(info)
    if display_value is None:
        if info.arg_optional:
            return None
        raise ValueError(f"{info.label} needs a value")
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


# ------------------------------------------------------------------ switches


@dataclass(frozen=True)
class SwitchSpec:
    name: str
    on_cmd: str
    off_cmd: str | None  # None: one-way (frunk/trunk open)
    on_text: str
    off_text: str
    risky_on: bool = False
    risky_off: bool = False


# The risky direction needs a full slide (or Enter/Space held ~1 s): that gesture is the
# confirmation. Safe directions are a click. No dialogs.
SWITCHES: dict[str, SwitchSpec] = {
    "locked": SwitchSpec(
        QT_TRANSLATE_NOOP("Switches", "Lock"), "lock", "unlock",
        QT_TRANSLATE_NOOP("Switches", "Locked"), QT_TRANSLATE_NOOP("Switches", "Unlocked"),
        risky_off=True,
    ),
    "sentry_on": SwitchSpec(
        QT_TRANSLATE_NOOP("Switches", "Sentry"), "sentry_on", "sentry_off",
        QT_TRANSLATE_NOOP("Switches", "Sentry on"), QT_TRANSLATE_NOOP("Switches", "Sentry off"),
        risky_off=True,
    ),
    "climate_on": SwitchSpec(
        QT_TRANSLATE_NOOP("Switches", "Climate"), "climate_on", "climate_off",
        QT_TRANSLATE_NOOP("Switches", "Climate on"), QT_TRANSLATE_NOOP("Switches", "Climate off"),
    ),
    "charging": SwitchSpec(
        QT_TRANSLATE_NOOP("Switches", "Charging"), "charge_start", "charge_stop",
        QT_TRANSLATE_NOOP("Switches", "Charging"), QT_TRANSLATE_NOOP("Switches", "Not charging"),
    ),
    "charge_port_open": SwitchSpec(
        QT_TRANSLATE_NOOP("Switches", "Charge port"), "charge_port_open", "charge_port_close",
        QT_TRANSLATE_NOOP("Switches", "Port open"), QT_TRANSLATE_NOOP("Switches", "Port closed"),
        risky_on=True,
    ),
    "frunk": SwitchSpec(
        QT_TRANSLATE_NOOP("Switches", "Frunk"), "frunk", None,
        QT_TRANSLATE_NOOP("Switches", "Frunk open"), QT_TRANSLATE_NOOP("Switches", "Frunk closed"),
        risky_on=True,
    ),
    "trunk": SwitchSpec(
        QT_TRANSLATE_NOOP("Switches", "Trunk"), "trunk", None,
        QT_TRANSLATE_NOOP("Switches", "Trunk open"), QT_TRANSLATE_NOOP("Switches", "Trunk closed"),
        risky_on=True,
    ),
}  # fmt: skip


def tr_switch(text: str) -> str:
    return QCoreApplication.translate("Switches", text)


def switch_state(field: str, s: Any) -> bool | None:
    """The switch position the car's state implies (None = unknown)."""
    if s is None:
        return None
    if field in ("frunk", "trunk"):
        return field in (s.doors_open or ())
    if field == "charging":
        return is_on("charging", s.charging)
    return getattr(s, field, None)


def switch_enabled(field: str, s: Any) -> bool:
    """Charging can only be switched with the cable in; everything else once state exists."""
    if s is None:
        return False
    if field == "charging":
        return bool(s.plugged_in)
    return True


def switch_command(field: str, on: bool) -> str | None:
    """Command value for a completed switch gesture to ``on`` (None: no such direction)."""
    sp = SWITCHES[field]
    return sp.on_cmd if on else sp.off_cmd


def editor_value(cmd: str, s: Any, units: str) -> tuple[str, Any] | None:
    """(display text, editor value) for an inline editor from the car state; None = hide."""
    if cmd == "set_temp":
        if s.target_temp_c is None:
            return None
        return fmt.temp(s.target_temp_c, units), fmt.temp_to_display(s.target_temp_c, units)
    if cmd == "set_charge_limit":
        return None if s.charge_limit is None else (f"{s.charge_limit} %", s.charge_limit)
    if cmd == "set_charge_amps":
        a = s.charge_current_request_a
        return None if a is None else (f"{a} A", a)
    if cmd == "set_scheduled_charging":
        m = minutes_of(s.scheduled_charging_start)
        return (hhmm(m) if m is not None else QCoreApplication.translate("ControlsTab", "off")), m
    if cmd == "set_scheduled_departure":
        m = minutes_of(s.scheduled_departure) if s.scheduled_charging_mode != "Off" else None
        return (hhmm(m) if m is not None else QCoreApplication.translate("ControlsTab", "off")), m
    return None
