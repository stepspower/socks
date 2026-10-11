# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Drive mode switching, the logind inhibitor wrapper (fake D-Bus), parked/profile UI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from socks.core.model import ShiftState, Source
from socks.gui.drive_mode import (
    INHIBIT_MODE,
    INHIBIT_WHAT,
    INHIBIT_WHO,
    INHIBIT_WHY,
    DriveMode,
    SleepInhibitor,
)

pytestmark = pytest.mark.usefixtures("qapp")


def _s(shift: ShiftState, source: Source = Source.BLE) -> SimpleNamespace:
    return SimpleNamespace(shift=shift, source=source)


def test_drive_mode_enters_on_ble_gear_and_leaves_after_two_parked_reads() -> None:
    dm = DriveMode()
    assert not dm.on_state(_s(ShiftState.P))
    assert dm.on_state(_s(ShiftState.D)) and dm.active
    assert not dm.on_state(_s(ShiftState.P)) and dm.active  # one P read: still driving
    assert not dm.on_state(_s(ShiftState.D))
    assert not dm.on_state(_s(ShiftState.P))
    assert dm.on_state(_s(ShiftState.P)) and not dm.active  # two in a row: off


def test_drive_mode_never_over_cloud() -> None:
    dm = DriveMode()
    assert not dm.on_state(_s(ShiftState.D, Source.CLOUD)) and not dm.active
    dm.on_state(_s(ShiftState.R))
    assert dm.active
    assert dm.on_state(_s(ShiftState.D, Source.CLOUD)) and not dm.active


class _Reply:
    def __init__(self, args: list[object], error: str = "", msg: str = "") -> None:
        self._args, self._err, self._msg = args, error, msg

    def errorName(self) -> str:  # noqa: N802 - mimics QDBusMessage
        return self._err

    def errorMessage(self) -> str:  # noqa: N802
        return self._msg

    def arguments(self) -> list[object]:
        return self._args


class _Manager:
    def __init__(self, reply: _Reply) -> None:
        self.reply, self.calls = reply, []

    def call(self, *args: object) -> _Reply:
        self.calls.append(args)
        return self.reply


def test_inhibitor_holds_and_releases_fd() -> None:
    mgr = _Manager(_Reply([SimpleNamespace(fileDescriptor=lambda: 17)]))
    closed: list[int] = []
    inh = SleepInhibitor(lambda: mgr, dup=lambda fd: fd + 100, close=closed.append)
    assert inh.acquire() and inh.held and inh.fd == 117
    assert mgr.calls == [("Inhibit", INHIBIT_WHAT, INHIBIT_WHO, INHIBIT_WHY, INHIBIT_MODE)]
    assert INHIBIT_WHAT == "sleep:handle-lid-switch" and INHIBIT_MODE == "block"
    assert inh.acquire() and len(mgr.calls) == 1  # idempotent
    inh.release()
    assert closed == [117] and not inh.held
    inh.release()  # idempotent
    assert closed == [117]


def test_inhibitor_refused_by_polkit() -> None:
    mgr = _Manager(_Reply([], "org.freedesktop.DBus.Error.AccessDenied", "Permission denied"))
    inh = SleepInhibitor(lambda: mgr, dup=lambda fd: fd, close=lambda fd: None)
    assert not inh.acquire() and not inh.held
    assert "AccessDenied" in (inh.error or "")


def test_inhibitor_no_bus() -> None:
    def boom() -> object:
        raise RuntimeError("no system bus")

    inh = SleepInhibitor(boom)
    assert not inh.acquire() and inh.error == "no system bus"


def test_history_parked_cards_and_profile() -> None:
    from socks.gui.tabs.history import HistoryTab

    tab = HistoryTab()
    parked = SimpleNamespace(
        hours_parked=48.0,
        soc_lost_pct=2.0,
        kwh_lost=1.5,
        drain_pct_per_day=1.0,
        hours_asleep_estimate=36.0,
    )
    tab._fill_parked(parked)
    assert tab.drain_card.value_text() == "1.00 %/day"
    assert tab.asleep_card.value_text() == "75 %"
    t0 = datetime(2026, 10, 1, 8, tzinfo=UTC)
    pts = [(t0 + timedelta(seconds=15 * i), 50.0 + i, 20.0 - i, 80 - i // 4) for i in range(8)]
    tab._profile_drive = 7
    tab.show_profile(7, pts, has_gpx=True)
    assert not tab.gpx_btn.isHidden()
    tab.show_profile(8, [], has_gpx=False)  # stale reply for another drive: ignored
    assert not tab.gpx_btn.isHidden()
    tab.show_profile(7, [], has_gpx=False)
    assert tab.gpx_btn.isHidden()
