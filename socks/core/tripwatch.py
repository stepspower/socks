# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Trip watch: decide when to start and stop (paid) cloud polling while the car is away.

Pure logic, no I/O and no timers; the GUI drives it:

    watcher = TripWatcher(settings)
    action = watcher.on_state(state)                 # after every successful read
    action = watcher.on_unavailable(now, asleep=...) # after a failed read
    if action is Action.START_CLOUD_POLL: start a timer every
        settings.trip_watch_interval_seconds calling cloud.read_state(wake=False)
    if action is Action.STOP_CLOUD_POLL: stop that timer

Cloud polls must use ``read_state(wake=False)``: trip watch never wakes the car (a parked,
sleeping car simply ends the watch) and cloud reads never pay for a wake anyway.

Start: cloud enabled and trip watch enabled, and either a BLE state shows the car in gear
(D/R/N) or with a rising odometer, or BLE loses the car <= 5 min after it was last seen
moving (the car drove out of range). Stop: two consecutive polls that show the car parked
(shift P) or asleep/offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from socks.core.model import ShiftState, Source, VehicleState

COST_PER_READ_USD = 0.002  # Fleet API data read (PLAN §6)
DEPARTURE_WINDOW = timedelta(minutes=5)
PARKED_POLLS_TO_STOP = 2
_MOVING = (ShiftState.D, ShiftState.R, ShiftState.N)


class Action(Enum):
    NONE = "none"
    START_CLOUD_POLL = "start_cloud_poll"
    STOP_CLOUD_POLL = "stop_cloud_poll"


def estimate_cost(poll_count: int) -> float:
    """Fleet API cost in USD of ``poll_count`` cloud data reads."""
    return round(max(0, poll_count) * COST_PER_READ_USD, 4)


def is_asleep_error(error: Any) -> bool:
    """True for backend errors that mean "car asleep/offline" (CommandError.kind)."""
    return getattr(error, "kind", None) in ("Asleep", "VehicleOffline")


@dataclass
class TripWatcher:
    settings: Any  # reads cloud_enabled / trip_watch_enabled live on every call

    def __post_init__(self) -> None:
        self.polling = False
        self.polls = 0  # cloud polls since the current watch started (for cost display)
        self._parked = 0
        self._last_moving: datetime | None = None
        self._last_odo: float | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.cloud_enabled and self.settings.trip_watch_enabled)

    def on_state(self, state: VehicleState) -> Action:
        rising = False
        if state.source is Source.BLE and state.odometer_km is not None:
            odo, last = state.odometer_km, self._last_odo
            rising = last is not None and odo > last + 0.05
            self._last_odo = odo
        if self.polling:
            if state.source is Source.CLOUD:
                self.polls += 1
            if not self.enabled:
                return self._stop()
            if state.shift is ShiftState.P:
                return self._parked_poll()
            self._parked = 0
            return Action.NONE
        if state.source is not Source.BLE:
            return Action.NONE
        if state.shift in _MOVING or rising:
            self._last_moving = state.ts
            return self._start()
        return Action.NONE

    def on_unavailable(self, now: datetime, *, asleep: bool = False) -> Action:
        """A read failed. ``asleep``: the car reported asleep/offline (see is_asleep_error)."""
        if self.polling:
            if not self.enabled:
                return self._stop()
            return self._parked_poll() if asleep else Action.NONE
        if asleep or self._last_moving is None:
            return Action.NONE
        if now - self._last_moving <= DEPARTURE_WINDOW:
            return self._start()
        return Action.NONE

    def _start(self) -> Action:
        if not self.enabled:
            return Action.NONE
        self.polling = True
        self.polls = 0
        self._parked = 0
        return Action.START_CLOUD_POLL

    def _parked_poll(self) -> Action:
        self._parked += 1
        if self._parked >= PARKED_POLLS_TO_STOP:
            return self._stop()
        return Action.NONE

    def _stop(self) -> Action:
        self.polling = False
        self._parked = 0
        self._last_moving = None
        return Action.STOP_CLOUD_POLL
