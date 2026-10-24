# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""One-off "Depart by": leave at 07:30 with N % and the cabin at T °C.

The car only knows a *recurring* daily departure time (minutes after midnight, vehicle-local
time). A one-off plan is built on top of it:

* ``arm`` sets the charge limit, the cabin temperature and the daily departure time with
  preconditioning and off-peak charging on (every day, not weekdays only).
* ``maintain`` (the GUI calls it after every successful read) switches the departure off
  again 15 minutes after the planned time and forgets the plan.

Edge rules:
* ``depart_at`` must lie in the future and at most 7 days ahead.
* The car interprets the time of day on its own: if that time has already passed today it
  means tomorrow. A plan more than 24 h ahead therefore also makes the car prepare at that
  time on the days before; ``arm`` warns about it.
* Times are converted with the laptop's time zone, assumed to match the car's.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from socks.core.backend import CommandError
from socks.core.commands import COMMANDS, Command, DepartureArg, validate_arg

DUE_AFTER = timedelta(minutes=15)
MAX_AHEAD = timedelta(days=7)
OK, FAILED, NOTE = "ok", "failed", "note"


@dataclass(frozen=True)
class DeparturePlan:
    depart_at: datetime  # aware
    charge_limit: int  # %
    temp_c: float
    created: datetime  # aware

    def to_dict(self) -> dict[str, Any]:
        return {
            "depart_at": self.depart_at.isoformat(),
            "charge_limit": self.charge_limit,
            "temp_c": self.temp_c,
            "created": self.created.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> DeparturePlan:
        """Parse a stored plan; raise ValueError/TypeError if malformed."""
        if not isinstance(data, dict):
            raise TypeError("expected a table")
        plan = cls(
            depart_at=_aware(data["depart_at"]),
            charge_limit=int(data["charge_limit"]),
            temp_c=float(data["temp_c"]),
            created=_aware(data["created"]),
        )
        plan.check_values()
        return plan

    def check_values(self) -> None:
        validate_arg(Command.SET_CHARGE_LIMIT, self.charge_limit)
        validate_arg(Command.SET_TEMP, self.temp_c)

    def validate(self, now: datetime) -> None:
        """Raise ValueError unless the plan is armable at ``now``."""
        self.check_values()
        if self.depart_at <= now:
            raise ValueError("departure time must be in the future")
        if self.depart_at - now > MAX_AHEAD:
            raise ValueError("departure must be at most 7 days ahead")

    @property
    def minutes_after_midnight(self) -> int:
        local = self.depart_at.astimezone()
        return local.hour * 60 + local.minute


def _aware(value: Any) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        raise ValueError("datetime without time zone")
    return dt


def next_occurrence(hhmm: str, now: datetime) -> datetime:
    """``"07:30"`` -> the next local 07:30 after ``now`` (today or tomorrow), aware."""
    try:
        hour_s, minute_s = hhmm.strip().split(":")
        hour, minute = int(hour_s), int(minute_s)
    except ValueError:
        raise ValueError(f"time must be HH:MM, got {hhmm!r}") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time must be HH:MM, got {hhmm!r}")
    # Build each candidate as a naive wall-clock time and localise it on its own date, so a
    # DST change between now and then picks that day's offset (not now's fixed offset).
    today = now.astimezone().date()
    at = datetime.combine(today, time(hour, minute)).astimezone()
    if at <= now:
        at = datetime.combine(today + timedelta(days=1), time(hour, minute)).astimezone()
    return at


def make_plan(
    depart_at: datetime, charge_limit: int = 90, temp_c: float = 21.0, now: datetime | None = None
) -> DeparturePlan:
    now = now or datetime.now(UTC)
    plan = DeparturePlan(depart_at, int(charge_limit), float(temp_c), now)
    plan.validate(now)
    return plan


def is_due(plan: DeparturePlan, now: datetime) -> bool:
    """True once the departure is 15 minutes past: the plan can be cleared."""
    return now >= plan.depart_at + DUE_AFTER


def _label(cmd: Command) -> str:
    return COMMANDS[cmd].label


async def arm(router: Any, plan: DeparturePlan, now: datetime | None = None) -> list[str]:
    """Send limit, temperature, departure (in that order) through the normal command path.

    Returns one human-readable line per step ("ok: ...", "failed: ...", "note: ...").
    Stops at the first failure (the last line then starts with "failed").
    """
    now = now or datetime.now(UTC)
    try:
        plan.validate(now)
    except ValueError as e:
        return [f"{FAILED}: {e}"]
    local = plan.depart_at.astimezone()
    steps: list[tuple[Command, Any, str]] = [
        (Command.SET_CHARGE_LIMIT, plan.charge_limit, f"{plan.charge_limit} %"),
        (Command.SET_TEMP, plan.temp_c, f"{plan.temp_c:g} °C"),
        (
            Command.SET_SCHEDULED_DEPARTURE,
            DepartureArg(plan.minutes_after_midnight, preconditioning=True, off_peak_charging=True),
            f"{local:%H:%M} with preconditioning and off-peak charging",
        ),
    ]
    results: list[str] = []
    hold = getattr(router, "hold", None)  # keep the BLE link up between the steps
    async with hold() if hold is not None else contextlib.nullcontext():
        for cmd, arg, what in steps:
            try:
                note = await router.send(cmd, arg)
            except CommandError as e:
                results.append(f"{FAILED}: {_label(cmd)} {what}: {e}")
                return results
            results.append(f"{OK}: {_label(cmd)} {what}" + (f" ({note})" if note else ""))
    if plan.depart_at - now > timedelta(hours=24):
        results.append(
            f"{NOTE}: the car repeats the departure daily, so it will also prepare at "
            f"{local:%H:%M} on the days before {local:%a %d %b}"
        )
    return results


async def disarm(router: Any) -> str:
    """Switch the scheduled departure off. Returns "ok: ..." or "failed: ..."."""
    try:
        note = await router.send(Command.SET_SCHEDULED_DEPARTURE, None)
    except CommandError as e:
        return f"{FAILED}: {_label(Command.SET_SCHEDULED_DEPARTURE)} off: {e}"
    return f"{OK}: {_label(Command.SET_SCHEDULED_DEPARTURE)} off" + (f" ({note})" if note else "")


async def maintain(router: Any, settings: Any, now: datetime | None = None) -> str | None:
    """After a successful read: clear a plan that is due. Returns "cleared", a failure line,
    or None when there is nothing to do. The plan is kept if disarming fails."""
    plan = getattr(settings, "depart_plan", None)
    now = now or datetime.now(UTC)
    if plan is None or not is_due(plan, now):
        return None
    result = await disarm(router)
    if result.startswith(FAILED):
        return result
    settings.depart_plan = None
    settings.save()
    return "cleared"
