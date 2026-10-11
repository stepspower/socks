# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Development shim: fabricates data in the PLAN §4 shapes.

Used only when the real ``socks.core`` / ``socks.history`` modules (or
``tests.fakes``) cannot be imported. Nothing here talks to a car, Bluetooth or
the network. Safe to delete once the real modules have landed.
"""

from __future__ import annotations

import asyncio
import math
import random
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

_RNG = random.Random(0)  # noqa: S311 - simulation, not crypto

# --------------------------------------------------------------------- model


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
    raw: dict = field(default_factory=dict, compare=False)


# ------------------------------------------------------------------ commands


class Command(Enum):
    LOCK = "lock"
    UNLOCK = "unlock"
    CLIMATE_ON = "climate_on"
    CLIMATE_OFF = "climate_off"
    SET_TEMP = "set_temp"
    CHARGE_START = "charge_start"
    CHARGE_STOP = "charge_stop"
    SET_CHARGE_LIMIT = "set_charge_limit"
    CHARGE_PORT_OPEN = "charge_port_open"
    CHARGE_PORT_CLOSE = "charge_port_close"
    FLASH_LIGHTS = "flash_lights"
    HONK = "honk"
    FRUNK = "frunk"
    TRUNK = "trunk"
    SENTRY_ON = "sentry_on"
    SENTRY_OFF = "sentry_off"
    WAKE = "wake"


@dataclass(frozen=True)
class CommandInfo:
    command: Command
    label: str
    dangerous: bool
    needs_wake: bool
    arg: str | None


def _ci(c: Command, label: str, dangerous: bool = False, arg: str | None = None) -> CommandInfo:
    return CommandInfo(c, label, dangerous, c is not Command.WAKE, arg)


COMMANDS: dict[Command, CommandInfo] = {
    i.command: i
    for i in (
        _ci(Command.LOCK, "Lock"),
        _ci(Command.UNLOCK, "Unlock", True),
        _ci(Command.CLIMATE_ON, "Climate on"),
        _ci(Command.CLIMATE_OFF, "Climate off"),
        _ci(Command.SET_TEMP, "Set temperature", arg="temp_c"),
        _ci(Command.CHARGE_START, "Start charging"),
        _ci(Command.CHARGE_STOP, "Stop charging"),
        _ci(Command.SET_CHARGE_LIMIT, "Set charge limit", arg="percent"),
        _ci(Command.CHARGE_PORT_OPEN, "Open charge port"),
        _ci(Command.CHARGE_PORT_CLOSE, "Close charge port"),
        _ci(Command.FLASH_LIGHTS, "Flash lights"),
        _ci(Command.HONK, "Honk"),
        _ci(Command.FRUNK, "Frunk open", True),
        _ci(Command.TRUNK, "Trunk open/close", True),
        _ci(Command.SENTRY_ON, "Sentry on"),
        _ci(Command.SENTRY_OFF, "Sentry off"),
        _ci(Command.WAKE, "Wake"),
    )
}


class CommandError(Exception):
    pass


class NotAvailable(CommandError):
    pass


# ------------------------------------------------------------------ settings


@dataclass
class AppPaths:
    config: Path
    data: Path
    cache: Path


@dataclass
class Settings:
    vin: str = ""
    key_path: Path = field(default_factory=lambda: Path.home() / ".config/tesla/private_key.pem")
    units: str = "metric"
    prefer: str = "ble"
    cloud_enabled: bool = False
    client_id: str = ""
    region: str = "eu"
    poll_seconds: int = 0
    tariff_eur_per_kwh: float = 0.0
    currency: str = "EUR"

    @staticmethod
    def load() -> Settings:
        return Settings(vin="5YJ3E7EB0SHIM0001")

    def save(self) -> None:
        # The shim never writes to disk.
        return None

    @staticmethod
    def paths() -> AppPaths:
        base = Path(tempfile.gettempdir()) / "socks-devshim"
        return AppPaths(base, base, base)


# ------------------------------------------------------------------- backend


class FakeBackend:
    """A car parked at home, slowly charging; commands mutate the state."""

    name = Source.BLE

    def __init__(self, vin: str = "5YJ3E7EB0SHIM0001") -> None:
        now = datetime.now(UTC)
        self._s = VehicleState(
            ts=now,
            source=Source.BLE,
            vin=vin,
            soc=63,
            usable_soc=62,
            charge_limit=80,
            charging=ChargingState.CHARGING,
            charger_power_kw=11.0,
            charger_voltage=230,
            charger_current_a=16,
            charge_energy_added_kwh=4.2,
            minutes_to_limit=55,
            rated_range_km=312.0,
            ideal_range_km=330.0,
            plugged_in=True,
            charge_port_open=True,
            scheduled_charging_start=None,
            odometer_km=48213.4,
            shift=ShiftState.P,
            speed_kmh=None,
            power_kw=0.0,
            inside_temp_c=17.5,
            outside_temp_c=11.0,
            climate_on=False,
            target_temp_c=20.5,
            locked=True,
            doors_open=(),
            windows_open=(),
            sentry_on=False,
            car_name="Shim Model 3",
            firmware="2026.32.5",
        )

    async def available(self) -> bool:
        return True

    async def read_state(self) -> VehicleState:
        await asyncio.sleep(0.15)
        s = self._s
        soc = s.soc or 0
        if s.charging is ChargingState.CHARGING and soc < (s.charge_limit or 80):
            soc += 1
        charging = s.charging
        if charging is ChargingState.CHARGING and soc >= (s.charge_limit or 80):
            charging = ChargingState.COMPLETE
        mins = (
            max(0, ((s.charge_limit or 80) - soc) * 5)
            if charging is ChargingState.CHARGING
            else None
        )
        self._s = replace(
            s,
            ts=datetime.now(UTC),
            soc=soc,
            usable_soc=max(0, soc - 1),
            charging=charging,
            charger_power_kw=11.0 if charging is ChargingState.CHARGING else 0.0,
            minutes_to_limit=mins,
            rated_range_km=round(soc * 4.95, 1),
            charge_energy_added_kwh=round((s.charge_energy_added_kwh or 0) + 0.2, 2),
            inside_temp_c=round((s.inside_temp_c or 17) + _RNG.uniform(-0.2, 0.2), 1),
        )
        return self._s

    async def send(self, cmd: Command, arg: float | int | None = None) -> None:
        await asyncio.sleep(0.4)
        s = self._s
        if cmd is Command.HONK and _RNG.random() < 0.25:
            raise CommandError("vehicle did not respond (simulated)")
        changes: dict[str, Any] = {}
        match cmd:
            case Command.LOCK:
                changes["locked"] = True
            case Command.UNLOCK:
                changes["locked"] = False
            case Command.CLIMATE_ON:
                changes["climate_on"] = True
            case Command.CLIMATE_OFF:
                changes["climate_on"] = False
            case Command.SET_TEMP:
                changes["target_temp_c"] = float(arg or 20)
            case Command.CHARGE_START:
                if not s.plugged_in:
                    raise CommandError("not plugged in")
                changes["charging"] = ChargingState.CHARGING
            case Command.CHARGE_STOP:
                changes["charging"] = ChargingState.STOPPED
            case Command.SET_CHARGE_LIMIT:
                changes["charge_limit"] = int(arg or 80)
            case Command.CHARGE_PORT_OPEN:
                changes["charge_port_open"] = True
            case Command.CHARGE_PORT_CLOSE:
                if s.plugged_in:
                    raise CommandError("cable is connected")
                changes["charge_port_open"] = False
            case Command.SENTRY_ON:
                changes["sentry_on"] = True
            case Command.SENTRY_OFF:
                changes["sentry_on"] = False
            case Command.TRUNK:
                doors = set(s.doors_open) ^ {"trunk"}
                changes["doors_open"] = tuple(sorted(doors))
            case Command.FRUNK:
                changes["doors_open"] = tuple(sorted(set(s.doors_open) | {"frunk"}))
            case _:
                pass
        self._s = replace(s, **changes)


# ------------------------------------------------------------------- history


@dataclass(frozen=True)
class Summary:
    since: datetime | None
    km_driven: float
    drives: int
    charge_sessions: int
    energy_added_kwh: float
    cost: float
    avg_wh_per_km_estimate: float | None
    longest_drive_km: float
    best_efficiency_day: date | None
    hours_plugged_in: float
    hours_charging: float


@dataclass(frozen=True)
class DayKm:
    day: date
    km: float


@dataclass(frozen=True)
class ChargeSession:
    start_ts: datetime
    end_ts: datetime | None
    start_soc: int
    end_soc: int | None
    energy_added_kwh: float
    max_power_kw: float
    source: str


@dataclass(frozen=True)
class BatteryHealth:
    first_range_at_100_km: float | None
    current_range_at_100_km: float | None
    degradation_pct: float | None
    samples: int


def _rng(vin: str) -> random.Random:
    return random.Random(vin)  # noqa: S311 - simulation, not crypto


def summary(db: object, vin: str, since: datetime | None) -> Summary:
    days = 30 if since is None else max(1, (datetime.now(UTC) - since).days)
    km = sum(d.km for d in daily_km(db, vin, days))
    return Summary(
        since=since,
        km_driven=round(km, 1),
        drives=days * 2,
        charge_sessions=days // 3,
        energy_added_kwh=round(km * 0.155, 1),
        cost=round(km * 0.155 * 0.25, 2),
        avg_wh_per_km_estimate=155.0,
        longest_drive_km=212.4,
        best_efficiency_day=date.today() - timedelta(days=4),
        hours_plugged_in=days * 9.5,
        hours_charging=days * 1.1,
    )


def daily_km(db: object, vin: str, days: int) -> list[DayKm]:
    r = _rng(vin)
    today = date.today()
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        km = (
            0.0
            if r.random() < 0.15
            else round(r.uniform(8, 60) + (90 if d.weekday() == 5 else 0), 1)
        )
        out.append(DayKm(d, km))
    return out


def charge_sessions(db: object, vin: str, limit: int) -> list[ChargeSession]:
    r = _rng(vin)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    out = []
    for i in range(limit):
        start = now - timedelta(days=3 * i + 1, hours=r.randint(0, 6))
        s0 = r.randint(25, 55)
        s1 = 80
        out.append(
            ChargeSession(
                start,
                start + timedelta(minutes=(s1 - s0) * 6),
                s0,
                s1,
                round((s1 - s0) * 0.75, 1),
                11.0,
                "ble",
            )
        )
    return out


def soc_series(db: object, vin: str, since: datetime | None) -> list[tuple[datetime, int]]:
    now = datetime.now(UTC)
    start = since or now - timedelta(days=30)
    out = []
    t = start
    while t <= now:
        h = (t - start).total_seconds() / 3600
        soc = 55 + 22 * math.sin(h / 26) + 6 * math.sin(h / 3.1)
        out.append((t, int(max(5, min(100, soc)))))
        t += timedelta(hours=1)
    return out


def battery_health(db: object, vin: str) -> BatteryHealth:
    return BatteryHealth(
        first_range_at_100_km=510.0, current_range_at_100_km=496.0, degradation_pct=2.7, samples=142
    )


# ------------------------------------------------------- synthetic sequences


# Synthetic coordinates (open fields, not anyone's address); never shown in screenshots.
SYNTH_HOME = (52.0, 5.0)
SYNTH_WORK = (52.05, 5.08)


def synthetic_states(vin: str, days: int, cls: Any, enums: Any) -> Iterator[Any]:
    """Yield a plausible history of VehicleState objects (oldest first).

    ``cls`` is the VehicleState class and ``enums`` a namespace with
    ChargingState/ShiftState/Source, so this works with the real model too.
    Pattern per day: two commute drives, a charge in the evening when SoC < 50.
    """
    r = _rng(vin)
    cs, sh, src = enums.ChargingState, enums.ShiftState, enums.Source
    t = (datetime.now(UTC) - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = datetime.now(UTC) - timedelta(minutes=30)
    soc, odo, energy = 70.0, 48000.0, 0.0
    charging = was_plugged = False
    step = timedelta(minutes=10)
    while t < end:
        hour = t.hour + t.minute / 60
        driving = (
            7.5 <= hour < 8.2 or 17.3 <= hour < 18.1 or (t.weekday() == 5 and 10 <= hour < 11.5)
        )
        if driving:
            km = r.uniform(2.5, 5)
            odo += km
            soc = max(5.0, soc - km * 0.155 / 0.75)
            charging = False
        elif 19 <= hour and soc < 50:
            charging = True
        if charging and not driving:
            soc = min(80.0, soc + 11 / 6 / 0.75)
            energy += 11 / 6
            if soc >= 80:
                charging = False
        plugged = (hour >= 19 or hour < 7.4) and not driving
        if plugged and not was_plugged:
            energy = 0.0  # the car's counter restarts at each plug-in
        was_plugged = plugged
        weekday_at_work = t.weekday() < 5 and 8.2 <= hour < 17.3
        where = None if driving else (SYNTH_WORK if weekday_at_work else SYNTH_HOME)
        extra = {}
        if where is not None and "latitude" in getattr(cls, "__dataclass_fields__", {}):
            extra = {"latitude": where[0], "longitude": where[1]}
        yield cls(
            ts=t,
            source=src.BLE,
            vin=vin,
            soc=int(soc),
            usable_soc=int(soc) - 1,
            charge_limit=80,
            charging=cs.CHARGING if charging else (cs.STOPPED if plugged else cs.DISCONNECTED),
            charger_power_kw=11.0 if charging else 0.0,
            charger_voltage=230 if charging else 0,
            charger_current_a=16 if charging else 0,
            charge_energy_added_kwh=round(energy, 2),
            minutes_to_limit=int((80 - soc) * 4) if charging else None,
            rated_range_km=round(soc * 5.0 - days * 0.0, 1),
            ideal_range_km=round(soc * 5.3, 1),
            plugged_in=plugged,
            charge_port_open=plugged,
            scheduled_charging_start=None,
            odometer_km=round(odo, 1),
            shift=sh.D if driving else sh.P,
            speed_kmh=45.0 if driving else 0.0,
            power_kw=15.0 if driving else 0.0,
            inside_temp_c=18.0,
            outside_temp_c=10.0 + 5 * math.sin(hour / 24 * 2 * math.pi),
            climate_on=False,
            target_temp_c=20.5,
            locked=not driving,
            **extra,
        )
        t += step
