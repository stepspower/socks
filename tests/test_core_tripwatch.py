# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tests for socks.core.tripwatch (pure state machine, synthetic sequences)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta

# Run from a source checkout without installing: make `socks` and `tests.fakes` importable.
from socks.core.backend import ASLEEP, CommandError, NotAvailable
from socks.core.model import ShiftState, Source
from socks.core.tripwatch import Action, TripWatcher, estimate_cost, is_asleep_error
from tests.fakes import FAKE_START, make_state

MIN = timedelta(minutes=1)


@dataclass
class S:
    cloud_enabled: bool = True
    trip_watch_enabled: bool = True


def ble(i: int, **kw: object):
    return make_state(ts=FAKE_START + i * MIN, source=Source.BLE, **kw)


def cloud(i: int, **kw: object):
    return make_state(ts=FAKE_START + i * MIN, source=Source.CLOUD, **kw)


def test_departure_seen_on_ble_then_parked_twice_stops() -> None:
    w = TripWatcher(S())
    assert w.on_state(ble(0)) is Action.NONE  # parked at home
    assert w.on_state(ble(1, shift=ShiftState.D, speed_kmh=10.0)) is Action.START_CLOUD_POLL
    assert w.polling
    assert w.on_unavailable(FAKE_START + 2 * MIN) is Action.NONE  # out of BLE range
    assert w.on_state(cloud(12, shift=ShiftState.D, odometer_km=4660.0)) is Action.NONE
    assert w.on_state(cloud(22, shift=ShiftState.P)) is Action.NONE  # first parked poll
    assert w.on_state(cloud(32, shift=ShiftState.D)) is Action.NONE  # resets the count
    assert w.on_state(cloud(42, shift=ShiftState.P)) is Action.NONE
    assert w.on_state(cloud(52, shift=ShiftState.P)) is Action.STOP_CLOUD_POLL
    assert not w.polling
    assert w.polls == 5
    assert estimate_cost(w.polls) == 0.01


def test_rising_odometer_starts_and_asleep_stops() -> None:
    w = TripWatcher(S())
    w.on_state(ble(0, shift=ShiftState.UNKNOWN, odometer_km=100.0))
    assert w.on_state(ble(1, shift=ShiftState.UNKNOWN, odometer_km=100.5)) is (
        Action.START_CLOUD_POLL
    )
    assert w.on_unavailable(FAKE_START + 20 * MIN, asleep=True) is Action.NONE
    assert w.on_unavailable(FAKE_START + 30 * MIN, asleep=True) is Action.STOP_CLOUD_POLL


def test_disappearing_soon_after_moving_starts() -> None:
    w = TripWatcher(S(trip_watch_enabled=False))
    assert w.on_state(ble(0, shift=ShiftState.R)) is Action.NONE  # watch disabled
    w.settings.trip_watch_enabled = True
    assert w.on_unavailable(FAKE_START + 4 * MIN) is Action.START_CLOUD_POLL


def test_disappearing_late_or_asleep_does_not_start() -> None:
    w = TripWatcher(S(trip_watch_enabled=False))
    w.on_state(ble(0, shift=ShiftState.D))
    w.settings.trip_watch_enabled = True
    assert w.on_unavailable(FAKE_START + 6 * MIN) is Action.NONE  # > 5 min
    w2 = TripWatcher(S())
    assert w2.on_unavailable(FAKE_START) is Action.NONE  # never seen moving
    w3 = TripWatcher(S(trip_watch_enabled=False))
    w3.on_state(ble(0, shift=ShiftState.D))
    w3.settings.trip_watch_enabled = True
    assert w3.on_unavailable(FAKE_START + MIN, asleep=True) is Action.NONE


def test_never_starts_without_cloud() -> None:
    w = TripWatcher(S(cloud_enabled=False))
    assert w.on_state(ble(0, shift=ShiftState.D)) is Action.NONE
    assert w.on_unavailable(FAKE_START + MIN) is Action.NONE
    assert not w.polling


def test_cloud_states_never_start_and_disabling_stops() -> None:
    w = TripWatcher(S())
    assert w.on_state(cloud(0, shift=ShiftState.D)) is Action.NONE
    w.on_state(ble(1, shift=ShiftState.D))
    w.settings.cloud_enabled = False
    assert w.on_state(cloud(11, shift=ShiftState.D)) is Action.STOP_CLOUD_POLL


def test_fake_drive_sequence_end_to_end() -> None:
    from tests.fakes import fake_sequence

    w = TripWatcher(S())
    actions = [w.on_state(s) for s in fake_sequence()]
    assert actions.count(Action.START_CLOUD_POLL) == 1
    # back over BLE, parked: two parked states end the watch
    assert Action.STOP_CLOUD_POLL in actions
    assert not w.polling


def test_helpers() -> None:
    assert estimate_cost(0) == 0 and estimate_cost(-3) == 0
    assert estimate_cost(1000) == 2.0
    assert is_asleep_error(NotAvailable("x", kind=ASLEEP))
    assert is_asleep_error(NotAvailable("x", kind="VehicleOffline"))
    assert not is_asleep_error(CommandError("x"))
    assert replace(ble(0), shift=ShiftState.P).shift is ShiftState.P
