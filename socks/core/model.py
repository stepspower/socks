# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Vehicle state model shared by every SOCKS component (pure data, no I/O).

All values are metric/SI-ish: km, km/h, kW, kWh, degrees Celsius. Conversion from Tesla's
miles happens at the backend boundary (see ``socks.core.backend``), never here.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum
from typing import Any

KM_PER_MILE = 1.609344


class ChargingState(Enum):
    DISCONNECTED = "Disconnected"
    STOPPED = "Stopped"
    CHARGING = "Charging"
    COMPLETE = "Complete"
    NO_POWER = "NoPower"
    STARTING = "Starting"
    UNKNOWN = "Unknown"


class ShiftState(Enum):
    P = "P"
    R = "R"
    N = "N"
    D = "D"
    UNKNOWN = "?"


class Source(Enum):
    BLE = "ble"
    CLOUD = "cloud"
    CACHE = "cache"


@dataclass(frozen=True)
class VehicleState:
    ts: datetime  # UTC, when read
    source: Source
    vin: str
    # charge
    soc: int | None  # %
    usable_soc: int | None
    charge_limit: int | None  # %
    charging: ChargingState
    charger_power_kw: float | None
    charger_voltage: int | None
    charger_current_a: int | None
    charge_energy_added_kwh: float | None
    minutes_to_limit: int | None
    rated_range_km: float | None
    ideal_range_km: float | None
    plugged_in: bool | None
    charge_port_open: bool | None
    scheduled_charging_start: datetime | None
    # drive
    odometer_km: float | None
    shift: ShiftState
    speed_kmh: float | None
    power_kw: float | None
    # climate
    inside_temp_c: float | None
    outside_temp_c: float | None
    climate_on: bool | None
    target_temp_c: float | None
    # closures / security
    locked: bool | None
    doors_open: tuple[str, ...] = ()  # e.g. ("front_left", "trunk")
    windows_open: tuple[str, ...] = ()
    sentry_on: bool | None = None
    # misc
    car_name: str | None = None
    firmware: str | None = None
    raw: dict = field(default_factory=dict, compare=False, repr=False)  # payload, never persisted


# Canonical names used in doors_open / windows_open.
DOOR_NAMES: tuple[str, ...] = (
    "front_left",
    "front_right",
    "rear_left",
    "rear_right",
    "frunk",
    "trunk",
)
WINDOW_NAMES: tuple[str, ...] = ("front_left", "front_right", "rear_left", "rear_right")


def state_to_dict(state: VehicleState, *, include_raw: bool = True) -> dict[str, Any]:
    """JSON-ready dict: enums become their values, datetimes ISO 8601 strings."""
    out: dict[str, Any] = {}
    for f in fields(state):
        if f.name == "raw" and not include_raw:
            continue
        value = getattr(state, f.name)
        if isinstance(value, Enum):
            value = value.value
        elif isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, tuple):
            value = list(value)
        out[f.name] = value
    return out


def state_from_dict(data: dict[str, Any]) -> VehicleState:
    """Inverse of :func:`state_to_dict` (e.g. for a cached last state). Missing keys -> None."""

    def dt(value: Any) -> datetime | None:
        return datetime.fromisoformat(value) if isinstance(value, str) and value else None

    kwargs: dict[str, Any] = {}
    for f in fields(VehicleState):
        kwargs[f.name] = data.get(f.name)
    ts = dt(kwargs["ts"])
    if ts is None:
        raise ValueError("state dict has no valid 'ts'")
    kwargs["ts"] = ts
    kwargs["source"] = Source(kwargs["source"] or Source.CACHE.value)
    kwargs["vin"] = str(kwargs["vin"] or "")
    kwargs["charging"] = _enum_or(ChargingState, kwargs["charging"], ChargingState.UNKNOWN)
    kwargs["shift"] = _enum_or(ShiftState, kwargs["shift"], ShiftState.UNKNOWN)
    kwargs["scheduled_charging_start"] = dt(kwargs["scheduled_charging_start"])
    kwargs["doors_open"] = tuple(kwargs["doors_open"] or ())
    kwargs["windows_open"] = tuple(kwargs["windows_open"] or ())
    kwargs["raw"] = dict(kwargs["raw"] or {})
    return VehicleState(**kwargs)


def _enum_or[E: Enum](cls: type[E], value: Any, default: E) -> E:
    try:
        return cls(value)
    except ValueError:
        return default
