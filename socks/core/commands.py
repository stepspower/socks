# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Vehicle commands and their metadata (label, danger level, wake need, argument)."""

from __future__ import annotations

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
    arg: str | None  # "temp_c" | "percent" | None


def _ci(cmd: Command, label: str, *, dangerous: bool = False, wake: bool = True,
        arg: str | None = None) -> CommandInfo:  # fmt: skip
    return CommandInfo(cmd, label, dangerous, wake, arg)


# Lock/unlock/frunk/trunk/wake go to the vehicle-security computer (no wake needed).
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
        _ci(Command.CHARGE_PORT_OPEN, "Open charge port"),
        _ci(Command.CHARGE_PORT_CLOSE, "Close charge port"),
        _ci(Command.FLASH_LIGHTS, "Flash lights"),
        _ci(Command.HONK, "Honk"),
        _ci(Command.FRUNK, "Open frunk", dangerous=True, wake=False),
        _ci(Command.TRUNK, "Open/close trunk", dangerous=True, wake=False),
        _ci(Command.SENTRY_ON, "Sentry mode on"),
        _ci(Command.SENTRY_OFF, "Sentry mode off"),
        _ci(Command.WAKE, "Wake up", wake=False),
    )
}

# Accepted argument ranges (what the car itself accepts).
TEMP_C_RANGE = (15.0, 28.0)
PERCENT_RANGE = (50, 100)


def parse_command(name: str) -> Command:
    """Look up a command by its value, case-insensitive, '-' allowed for '_'."""
    key = name.strip().lower().replace("-", "_")
    try:
        return Command(key)
    except ValueError:
        raise ValueError(f"unknown command {name!r}") from None


def validate_arg(cmd: Command, arg: float | int | None) -> float | int | None:
    """Check and normalise a command argument; raise ValueError if wrong or missing."""
    kind = COMMANDS[cmd].arg
    if kind is None:
        if arg is not None:
            raise ValueError(f"{cmd.value} takes no argument")
        return None
    if arg is None:
        raise ValueError(f"{cmd.value} needs an argument ({kind})")
    if kind == "temp_c":
        lo, hi = TEMP_C_RANGE
        value = round(float(arg) * 2) / 2  # the car works in 0.5 degree steps
        if not lo <= value <= hi:
            raise ValueError(f"temperature must be {lo:g}..{hi:g} °C")
        return value
    plo, phi = PERCENT_RANGE
    if float(arg) != int(float(arg)):
        raise ValueError("percent must be a whole number")
    pct = int(float(arg))
    if not plo <= pct <= phi:
        raise ValueError(f"percent must be {plo}..{phi}")
    return pct
