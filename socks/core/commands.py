# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Vehicle commands and their metadata (label, danger level, wake need, argument)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class Command(Enum):
    LOCK = "lock"
    UNLOCK = "unlock"
    CLIMATE_ON = "climate_on"
    CLIMATE_OFF = "climate_off"
    SET_TEMP = "set_temp"
    CHARGE_START = "charge_start"
    CHARGE_STOP = "charge_stop"
    SET_CHARGE_LIMIT = "set_charge_limit"
    SET_CHARGE_AMPS = "set_charge_amps"
    SET_SCHEDULED_CHARGING = "set_scheduled_charging"
    SET_SCHEDULED_DEPARTURE = "set_scheduled_departure"
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
    dangerous: bool  # GUI asks the user to type the first letters; CLI needs --yes
    needs_wake: bool  # goes to the infotainment computer, which must be awake
    arg: str | None  # "temp_c" | "percent" | "amps" | "minutes_after_midnight" | None
    cloud_only: bool = False  # not available over BLE: the Router sends it via the cloud
    min: float | None = None  # argument range/step/unit for GUI editors (None: no argument)
    max: float | None = None
    step: float | None = None
    unit: str | None = None
    arg_optional: bool = False  # arg may be None (scheduling: None = disable)


# Argument metadata: kind -> (min, max, step, unit). These are what the car accepts.
ARG_SPECS: dict[str, tuple[float, float, float, str]] = {
    "temp_c": (15.0, 28.0, 0.5, "°C"),
    "percent": (50, 100, 1, "%"),
    "amps": (5, 32, 1, "A"),
    "minutes_after_midnight": (0, 1439, 1, "min"),
}


def _ci(cmd: Command, label: str, *, dangerous: bool = False, wake: bool = True,
        arg: str | None = None, cloud_only: bool = False,
        arg_optional: bool = False) -> CommandInfo:  # fmt: skip
    lo, hi, step, unit = ARG_SPECS[arg] if arg else (None, None, None, None)
    return CommandInfo(
        cmd, label, dangerous, wake, arg, cloud_only, lo, hi, step, unit, arg_optional
    )


# Dangerous: unlock, frunk, trunk, sentry_off (owner decision, audit L3).
# Lock/unlock/frunk/trunk/wake go to the vehicle-security computer (no wake needed).
# Everything here exists over BLE as well (tesla-fleet-api shares one Commands class), so
# nothing is cloud_only today; the flag and Router support exist for future commands.
COMMANDS: dict[Command, CommandInfo] = {
    c.command: c
    for c in (
        _ci(Command.LOCK, "Lock", wake=False),
        _ci(Command.UNLOCK, "Unlock", dangerous=True, wake=False),
        _ci(Command.CLIMATE_ON, "Climate on"),
        _ci(Command.CLIMATE_OFF, "Climate off"),
        _ci(Command.SET_TEMP, "Set temperature", arg="temp_c"),
        _ci(Command.CHARGE_START, "Start charging"),
        _ci(Command.CHARGE_STOP, "Stop charging"),
        _ci(Command.SET_CHARGE_LIMIT, "Set charge limit", arg="percent"),
        _ci(Command.SET_CHARGE_AMPS, "Set charging current", arg="amps"),
        _ci(
            Command.SET_SCHEDULED_CHARGING,
            "Scheduled charging start",
            arg="minutes_after_midnight",
            arg_optional=True,
        ),  # fmt: skip
        _ci(
            Command.SET_SCHEDULED_DEPARTURE,
            "Scheduled departure",
            arg="minutes_after_midnight",
            arg_optional=True,
        ),  # fmt: skip
        _ci(Command.CHARGE_PORT_OPEN, "Open charge port"),
        _ci(Command.CHARGE_PORT_CLOSE, "Close charge port"),
        _ci(Command.FLASH_LIGHTS, "Flash lights"),
        _ci(Command.HONK, "Honk"),
        _ci(Command.FRUNK, "Open frunk", dangerous=True, wake=False),
        _ci(Command.TRUNK, "Open/close trunk", dangerous=True, wake=False),
        _ci(Command.SENTRY_ON, "Sentry mode on"),
        _ci(Command.SENTRY_OFF, "Sentry mode off", dangerous=True),
        _ci(Command.WAKE, "Wake up", wake=False),
    )
}

# Kept for compatibility.
TEMP_C_RANGE = ARG_SPECS["temp_c"][:2]
PERCENT_RANGE = ARG_SPECS["percent"][:2]


def parse_command(name: str) -> Command:
    """Look up a command by its value, case-insensitive, '-' allowed for '_'."""
    key = name.strip().lower().replace("-", "_")
    try:
        return Command(key)
    except ValueError:
        raise ValueError(f"unknown command {name!r}") from None


def validate_arg(cmd: Command, arg: float | int | None) -> float | int | None:
    """Check and normalise a command argument; raise ValueError if wrong or missing."""
    info = COMMANDS[cmd]
    kind = info.arg
    if kind is None:
        if arg is not None:
            raise ValueError(f"{cmd.value} takes no argument")
        return None
    if arg is None:
        if info.arg_optional:
            return None
        raise ValueError(f"{cmd.value} needs an argument ({kind})")
    if isinstance(arg, bool) or not math.isfinite(float(arg)):
        raise ValueError(f"{cmd.value} needs a finite number")
    lo, hi, step, unit = ARG_SPECS[kind]
    if kind == "temp_c":
        value: float | int = round(float(arg) / step) * step  # the car works in 0.5 steps
    else:
        if float(arg) != int(float(arg)):
            raise ValueError(f"{cmd.value} needs a whole number")
        value = int(float(arg))
    if not lo <= value <= hi:
        raise ValueError(f"{cmd.value} must be {lo:g}..{hi:g} {unit}")
    return value
