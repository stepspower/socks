# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tests for socks.core.model, socks.core.commands and the shared fakes."""

from __future__ import annotations

import sys
from pathlib import Path

# Run from a source checkout without installing: make `socks` and `tests.fakes` importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from datetime import timedelta

import pytest

from socks.core.backend import CommandError
from socks.core.commands import COMMANDS, Command, parse_command, validate_arg
from socks.core.model import (
    ChargingState,
    ShiftState,
    Source,
    state_from_dict,
    state_to_dict,
)
from tests.fakes import FAKE_START, FakeBackend, fake_sequence, make_state


def test_state_dict_roundtrip() -> None:
    s = make_state(doors_open=("trunk",), scheduled_charging_start=FAKE_START)
    d = state_to_dict(s)
    json.dumps(d)  # JSON-ready
    assert d["charging"] == "Disconnected" and d["shift"] == "P" and d["source"] == "ble"
    assert state_from_dict(d) == s
    assert "raw" not in state_to_dict(s, include_raw=False)


def test_state_from_dict_tolerates_missing_and_unknown() -> None:
    s = state_from_dict({"ts": FAKE_START.isoformat(), "charging": "Weird", "shift": None})
    assert s.charging is ChargingState.UNKNOWN
    assert s.shift is ShiftState.UNKNOWN
    assert s.source is Source.CACHE
    assert s.soc is None
    with pytest.raises(ValueError):
        state_from_dict({})


def test_raw_not_compared() -> None:
    assert make_state(raw={"a": 1}) == make_state(raw={"b": 2})


def test_commands_metadata_complete() -> None:
    assert set(COMMANDS) == set(Command)
    dangerous = {c for c, i in COMMANDS.items() if i.dangerous}
    assert dangerous == {Command.UNLOCK, Command.FRUNK, Command.TRUNK, Command.SENTRY_OFF}
    assert not COMMANDS[Command.CHARGE_PORT_OPEN].dangerous
    assert COMMANDS[Command.SET_TEMP].arg == "temp_c"
    assert COMMANDS[Command.SET_CHARGE_LIMIT].arg == "percent"
    assert not COMMANDS[Command.LOCK].needs_wake
    assert COMMANDS[Command.CLIMATE_ON].needs_wake


def test_parse_and_validate() -> None:
    assert parse_command("Set-Charge-Limit") is Command.SET_CHARGE_LIMIT
    with pytest.raises(ValueError):
        parse_command("self_destruct")
    assert validate_arg(Command.SET_CHARGE_LIMIT, 80.0) == 80
    assert validate_arg(Command.SET_TEMP, 21.3) == 21.5
    assert validate_arg(Command.LOCK, None) is None
    for cmd, arg in [
        (Command.SET_CHARGE_LIMIT, 49),
        (Command.SET_CHARGE_LIMIT, 101),
        (Command.SET_CHARGE_LIMIT, 80.5),
        (Command.SET_CHARGE_LIMIT, None),
        (Command.SET_TEMP, 40),
        (Command.SET_TEMP, float("nan")),
        (Command.SET_CHARGE_LIMIT, float("inf")),
        (Command.LOCK, 1),
    ]:
        with pytest.raises(ValueError):
            validate_arg(cmd, arg)


# ---------------------------------------------------------------- fakes


def test_fake_sequence_story() -> None:
    seq = fake_sequence()
    assert seq == fake_sequence()  # deterministic
    assert all(b.ts - a.ts == timedelta(minutes=1) for a, b in zip(seq, seq[1:], strict=False))
    assert seq[0].shift is ShiftState.P and seq[0].locked
    driving = [s for s in seq if s.shift is ShiftState.D]
    assert driving
    odo = [s.odometer_km for s in seq]
    assert odo[-1] - odo[0] == pytest.approx(12.0)
    charging = [s for s in seq if s.charging is ChargingState.CHARGING]
    assert charging and all(s.plugged_in for s in charging)
    assert seq[-1].soc == 80 and seq[-1].charging is ChargingState.COMPLETE
    assert max(s.charge_energy_added_kwh or 0 for s in seq) > 8


async def test_fake_backend_reads_then_holds() -> None:
    fb = FakeBackend()
    n = len(fake_sequence())
    states = [await fb.read_state() for _ in range(n + 3)]
    assert states[:n] == fake_sequence()
    assert states[-1].soc == 80
    assert states[-1].ts > states[-2].ts  # time keeps moving


async def test_fake_backend_commands() -> None:
    fb = FakeBackend()
    await fb.read_state()
    await fb.send(Command.SET_CHARGE_LIMIT, 90)
    await fb.send(Command.UNLOCK)
    s = await fb.read_state()
    assert s.charge_limit == 90
    assert fb.sent == [(Command.SET_CHARGE_LIMIT, 90), (Command.UNLOCK, None)]
    with pytest.raises(CommandError):
        await fb.send(Command.CHARGE_START)  # not plugged in yet
    with pytest.raises(CommandError):
        await fb.send(Command.SET_CHARGE_LIMIT, 10)


async def test_fake_backend_realtime_and_failures() -> None:
    fb = FakeBackend(start=None, source=Source.CLOUD)
    s = await fb.read_state()
    assert s.source is Source.CLOUD and s.ts > FAKE_START
    off = FakeBackend(available_flag=False)
    assert not await off.available()
    with pytest.raises(CommandError):
        await off.read_state()


def test_arg_metadata_for_editors() -> None:
    t = COMMANDS[Command.SET_TEMP]
    assert (t.min, t.max, t.step, t.unit) == (15.0, 28.0, 0.5, "°C")
    lim = COMMANDS[Command.SET_CHARGE_LIMIT]
    assert (lim.min, lim.max, lim.step, lim.unit) == (50, 100, 1, "%")
    amps = COMMANDS[Command.SET_CHARGE_AMPS]
    assert (amps.arg, amps.min, amps.max, amps.step, amps.unit) == ("amps", 5, 32, 1, "A")
    assert amps.needs_wake and not amps.dangerous
    for cmd in (Command.SET_SCHEDULED_CHARGING, Command.SET_SCHEDULED_DEPARTURE):
        info = COMMANDS[cmd]
        assert info.arg == "minutes_after_midnight" and info.arg_optional
        assert (info.min, info.max) == (0, 1439)
        assert validate_arg(cmd, None) is None
        assert validate_arg(cmd, 450.0) == 450
    assert COMMANDS[Command.LOCK].min is None and COMMANDS[Command.LOCK].unit is None
    assert not any(i.cloud_only for i in COMMANDS.values())
    assert validate_arg(Command.SET_CHARGE_AMPS, 16) == 16
    for bad in (4, 33, 16.5, None):
        with pytest.raises(ValueError):
            validate_arg(Command.SET_CHARGE_AMPS, bad)


async def test_fake_backend_schedule_and_amps() -> None:
    fb = FakeBackend()
    s0 = await fb.read_state()
    assert s0.charge_current_request_a == 16 and s0.scheduled_charging_mode == "Off"
    await fb.send(Command.SET_CHARGE_AMPS, 10)
    await fb.send(Command.SET_SCHEDULED_DEPARTURE, 450)
    s = await fb.read_state()
    assert s.charge_current_request_a == 10
    assert s.scheduled_charging_mode == "DepartBy" and s.scheduled_departure is not None
    local = s.scheduled_departure.astimezone()
    assert (local.hour, local.minute) == (7, 30)
    await fb.send(Command.SET_SCHEDULED_CHARGING, 120)
    s = await fb.read_state()
    assert s.scheduled_charging_mode == "StartAt" and s.scheduled_departure is None
    await fb.send(Command.SET_SCHEDULED_CHARGING, None)
    s = await fb.read_state()
    assert s.scheduled_charging_mode == "Off" and s.scheduled_charging_start is None
    d = state_to_dict(s)
    assert state_from_dict(d) == s
