# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Shared fakes: a deterministic car for tests, the CLI (``--fake``) and the GUI.

``fake_sequence()`` is the scripted day: parked (locked, 70 %) -> a 12 km drive -> parked
-> plugged in -> charging at 11 kW up to the 80 % limit -> complete. ``FakeBackend`` serves
it one state per ``read_state()`` and then keeps returning the final state; commands mutate
the current state. Nothing here touches Bluetooth or the network.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from socks.core.backend import CommandError, NotAvailable
from socks.core.commands import Command, validate_arg
from socks.core.model import ChargingState, ShiftState, Source, VehicleState

FAKE_VIN = "5YJ3E7EB0KF000001"  # syntactically valid, not a real car
FAKE_START = datetime(2026, 10, 11, 7, 0, tzinfo=UTC)
FAKE_STEP = timedelta(minutes=1)
FAKE_HOME = (52.0, 5.0)  # synthetic coordinates (a field in the Netherlands)
PACK_KWH = 75.0  # used only to make energy numbers plausible


def make_state(**overrides: Any) -> VehicleState:
    """A complete, plausible parked state; any field can be overridden by keyword."""
    base = VehicleState(
        ts=FAKE_START,
        source=Source.BLE,
        vin=FAKE_VIN,
        soc=70,
        usable_soc=69,
        charge_limit=80,
        charging=ChargingState.DISCONNECTED,
        charger_power_kw=0.0,
        charger_voltage=0,
        charger_current_a=0,
        charge_energy_added_kwh=0.0,
        minutes_to_limit=None,
        rated_range_km=345.0,
        ideal_range_km=360.0,
        plugged_in=False,
        charge_port_open=False,
        scheduled_charging_start=None,
        odometer_km=4649.6,
        shift=ShiftState.P,
        speed_kmh=None,
        power_kw=0.0,
        inside_temp_c=16.5,
        outside_temp_c=12.0,
        climate_on=False,
        target_temp_c=20.5,
        locked=True,
        doors_open=(),
        windows_open=(),
        sentry_on=False,
        car_name="Fake Model 3",
        firmware="2026.32.5",
        latitude=FAKE_HOME[0],
        longitude=FAKE_HOME[1],
        heading=90,
        gps_as_of=FAKE_START,
        charge_current_request_a=16,
        charge_current_request_max_a=16,
        scheduled_departure=None,
        scheduled_charging_mode="Off",
        raw={"fake": True},
    )
    return replace(base, **overrides)


def _next_local_minutes(now: datetime, minutes: int) -> datetime:
    """Next occurrence of ``minutes`` after local midnight, as UTC (like the car does)."""
    local = now.astimezone()
    at = local.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
    if at <= local:
        at += timedelta(days=1)
    return at.astimezone(UTC)


def _range_km(soc: int) -> float:
    return round(soc * 4.93, 1)


def fake_sequence(
    vin: str = FAKE_VIN, start: datetime = FAKE_START, step: timedelta = FAKE_STEP
) -> list[VehicleState]:
    """The scripted sequence (about 40 states, ``step`` apart)."""
    states: list[VehicleState] = []
    s = make_state(vin=vin, soc=70, usable_soc=69, rated_range_km=_range_km(70))

    def emit(**changes: Any) -> None:
        nonlocal s
        s = replace(s, ts=start + step * len(states), **changes)
        states.append(s)

    # 1. parked, locked
    for _ in range(3):
        emit()
    # 2. unlock, drive 12 km in 12 steps (1 km/min = 60 km/h), SoC 70 -> 68
    emit(locked=False, shift=ShiftState.D, speed_kmh=30.0, power_kw=15.0, inside_temp_c=18.0)
    odo = s.odometer_km or 0.0
    for i in range(1, 13):
        soc = 70 - (2 * i) // 12
        emit(
            odometer_km=round(odo + i, 1),
            speed_kmh=60.0 if i < 12 else 20.0,
            power_kw=14.0 + (i % 3),
            soc=soc,
            usable_soc=soc - 1,
            rated_range_km=_range_km(soc),
            inside_temp_c=20.0,
            latitude=round(FAKE_HOME[0] + i * 0.009, 4),  # ~1 km north per step
            heading=0,
            gps_as_of=start + step * len(states),
        )
    # 3. parked again, locked
    emit(shift=ShiftState.P, speed_kmh=None, power_kw=0.0)
    emit(locked=True)
    # 4. plug in, charge 68 -> 80 at 11 kW (1 % per step for the demo), then complete
    emit(plugged_in=True, charge_port_open=True, charging=ChargingState.STOPPED)
    emit(charging=ChargingState.STARTING, charger_voltage=230, charger_current_a=16)
    soc = s.soc or 68
    added = 0.0
    while soc < 80:
        soc += 1
        added = round(added + PACK_KWH / 100, 2)
        emit(
            charging=ChargingState.CHARGING,
            soc=soc,
            usable_soc=soc - 1,
            rated_range_km=_range_km(soc),
            charger_power_kw=11.0,
            charger_voltage=230,
            charger_current_a=16,
            charge_energy_added_kwh=added,
            minutes_to_limit=(80 - soc) * 4,
        )
    emit(
        charging=ChargingState.COMPLETE,
        charger_power_kw=0.0,
        charger_current_a=0,
        charger_voltage=0,
        minutes_to_limit=0,
    )
    emit()
    return states


class FakeBackend:
    """Backend over :func:`fake_sequence`.

    ``start=None`` stamps states with the wall clock (for the GUI); a fixed ``start`` gives
    fully deterministic timestamps (for tests and HISTORY). ``delay`` simulates latency.
    ``available_flag=False`` / ``fail_reads=True`` / ``fail_commands=True`` simulate outages.
    """

    def __init__(
        self,
        vin: str = FAKE_VIN,
        *,
        start: datetime | None = FAKE_START,
        step: timedelta = FAKE_STEP,
        delay: float = 0.0,
        source: Source = Source.BLE,
        available_flag: bool = True,
        fail_reads: bool = False,
        fail_commands: bool = False,
    ) -> None:
        self.name = source
        self._realtime = start is None
        self._states = fake_sequence(vin, start or FAKE_START, step)
        self._index = 0
        self._step = step
        self._current: VehicleState | None = None
        self.delay = delay
        self.available_flag = available_flag
        self.fail_reads = fail_reads
        self.fail_commands = fail_commands
        self.sent: list[tuple[Command, float | int | None]] = []
        self.reads = 0
        self.last_wake: bool | None = None
        self.last_include_location: bool | None = None
        self.closed = False
        self.paired: list[str] = []
        self.pair_result = True

    async def available(self) -> bool:
        return self.available_flag

    async def read_state(self, wake: bool = False, include_location: bool = True) -> VehicleState:
        self.last_wake = wake
        self.last_include_location = include_location
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.available_flag:
            raise NotAvailable(f"fake {self.name.value}: offline")
        if self.fail_reads:
            raise CommandError(f"fake {self.name.value}: read failed")
        self.reads += 1
        script = self._states[min(self._index, len(self._states) - 1)]
        ts = script.ts + self._step * max(0, self._index - len(self._states) + 1)
        if self._realtime:
            ts = datetime.now(UTC)
        self._index += 1
        base = script if self._current is None else self._merge(self._current, script)
        self._current = replace(base, ts=ts, source=self.name)
        if not include_location:
            return replace(
                self._current, latitude=None, longitude=None, heading=None, gps_as_of=None
            )
        return self._current

    @staticmethod
    def _merge(current: VehicleState, script: VehicleState) -> VehicleState:
        """Keep user-changed settings (limit, climate, sentry...) across scripted steps."""
        return replace(
            script,
            charge_limit=current.charge_limit,
            climate_on=current.climate_on,
            target_temp_c=current.target_temp_c,
            sentry_on=current.sentry_on,
            charge_port_open=current.charge_port_open or script.charge_port_open,
            doors_open=current.doors_open,
            charge_current_request_a=current.charge_current_request_a,
            scheduled_charging_mode=current.scheduled_charging_mode,
            scheduled_charging_start=current.scheduled_charging_start,
            scheduled_departure=current.scheduled_departure,
        )

    async def send(self, cmd: Command, arg: float | int | None = None) -> None:
        try:
            arg = validate_arg(cmd, arg)
        except ValueError as e:
            raise CommandError(str(e)) from None
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.available_flag:
            raise NotAvailable(f"fake {self.name.value}: offline")
        if self.fail_commands:
            raise CommandError(f"fake {self.name.value}: command failed")
        self.sent.append((cmd, arg))
        s = self._current or self._states[0]
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
                changes["target_temp_c"] = float(arg or 20.0)
            case Command.CHARGE_START:
                if not s.plugged_in:
                    raise CommandError("fake: not plugged in")
                changes["charging"] = ChargingState.CHARGING
            case Command.CHARGE_STOP:
                changes["charging"] = (
                    ChargingState.STOPPED if s.plugged_in else ChargingState.DISCONNECTED
                )
            case Command.SET_CHARGE_LIMIT:
                changes["charge_limit"] = int(arg or 80)
            case Command.SET_CHARGE_AMPS:
                cap = s.charge_current_request_max_a or 32
                changes["charge_current_request_a"] = min(int(arg or 0), cap)
            case Command.SET_SCHEDULED_CHARGING:
                if arg is None:
                    changes["scheduled_charging_mode"] = "Off"
                    changes["scheduled_charging_start"] = None
                else:
                    changes["scheduled_charging_mode"] = "StartAt"
                    changes["scheduled_charging_start"] = _next_local_minutes(s.ts, int(arg))
                    changes["scheduled_departure"] = None
            case Command.SET_SCHEDULED_DEPARTURE:
                if arg is None:
                    changes["scheduled_charging_mode"] = "Off"
                    changes["scheduled_departure"] = None
                else:
                    changes["scheduled_charging_mode"] = "DepartBy"
                    changes["scheduled_departure"] = _next_local_minutes(s.ts, int(arg))
                    changes["scheduled_charging_start"] = None
            case Command.CHARGE_PORT_OPEN:
                changes["charge_port_open"] = True
            case Command.CHARGE_PORT_CLOSE:
                if s.plugged_in:
                    raise CommandError("fake: cable is connected")
                changes["charge_port_open"] = False
            case Command.SENTRY_ON:
                changes["sentry_on"] = True
            case Command.SENTRY_OFF:
                changes["sentry_on"] = False
            case Command.FRUNK:
                changes["doors_open"] = tuple(sorted({*s.doors_open, "frunk"}))
            case Command.TRUNK:
                changes["doors_open"] = tuple(sorted(set(s.doors_open) ^ {"trunk"}))
            case Command.FLASH_LIGHTS | Command.HONK | Command.WAKE:
                pass
        self._current = replace(s, **changes)

    async def pair(self, role: str = "driver", timeout: float = 60.0) -> bool:
        """Simulate the BLE add-key request; ``pair_result`` decides the outcome."""
        if not self.available_flag:
            raise NotAvailable(f"fake {self.name.value}: offline")
        self.paired.append(role)
        return self.pair_result

    async def close(self) -> None:
        self.closed = True
