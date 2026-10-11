# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Test-only copy of PLAN section 4 model; used if socks.core.model is not importable."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


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
    ts: datetime
    source: Source
    vin: str
    soc: int | None
    usable_soc: int | None
    charge_limit: int | None
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
    odometer_km: float | None
    shift: ShiftState
    speed_kmh: float | None
    power_kw: float | None
    inside_temp_c: float | None
    outside_temp_c: float | None
    climate_on: bool | None
    target_temp_c: float | None
    locked: bool | None
    doors_open: tuple[str, ...] = ()
    windows_open: tuple[str, ...] = ()
    sentry_on: bool | None = None
    car_name: str | None = None
    firmware: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    heading: int | None = None
    gps_as_of: datetime | None = None
    raw: dict = field(default_factory=dict, compare=False)
