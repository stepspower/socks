# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Purpose cycling, places/trip-watch wiring and geocode gating (offscreen, no network)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from socks.core.backend import ASLEEP, NotAvailable  # noqa: E402
from socks.core.model import ShiftState, Source  # noqa: E402
from socks.gui.location import (  # noqa: E402
    GeocodeGate,
    TripWatch,
    next_purpose,
    purpose_for_key,
)
from tests.fakes import make_state  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> object:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_purpose_cycle() -> None:
    seq = [None]
    for _ in range(4):
        seq.append(next_purpose(seq[-1]))
    assert seq == [None, "business", "private", "commute", None]
    assert next_purpose("bogus") == "business"


def test_purpose_keys() -> None:
    assert purpose_for_key("b", False) == (True, "business")
    assert purpose_for_key("P", False) == (True, "private")
    assert purpose_for_key("c", False) == (True, "commute")
    assert purpose_for_key("", True) == (True, None)
    assert purpose_for_key("x", False) == (False, None)


def test_drives_table_click_and_keys(qapp: object) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    from socks.gui.tabs.history import PURPOSE_COL, HistoryTab

    tab = HistoryTab()
    start = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
    drive = SimpleNamespace(id=42, start=start, end=start.replace(hour=8), km=30.0,
                            start_soc=80, end_soc=74, start_label="Home", end_label="Work",
                            purpose=None)  # fmt: skip
    tab._fill_drives([drive])
    t = tab.drives_table
    assert t.item(0, PURPOSE_COL - 1).text() == "Home → Work"
    got: list[tuple[int, object]] = []
    tab.purpose_requested.connect(lambda i, p: got.append((i, p)))
    t._clicked(0, PURPOSE_COL)
    t._clicked(0, 0)  # other columns are read-only, nothing happens
    t.setCurrentCell(0, 0)
    t.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_P, Qt.KeyboardModifier.NoModifier, "p")
    )
    t.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier, "")
    )
    assert got == [(42, "business"), (42, "private"), (42, None)]
    t.set_purpose(42, "commute")
    assert t.item(0, PURPOSE_COL).text() == "Commute"


def _settings(**kw: object) -> SimpleNamespace:
    base = {"cloud_enabled": True, "trip_watch_enabled": True, "trip_watch_interval_seconds": 60}
    return SimpleNamespace(**{**base, **kw})


def test_trip_watch_starts_and_stops(qapp: object) -> None:
    tw = TripWatch(_settings())
    chips: list[tuple[bool, int, float]] = []
    tw.changed.connect(lambda p, n, usd: chips.append((p, n, usd)))
    tw.on_state(make_state(shift=ShiftState.D, source=Source.BLE))
    assert tw.polling and tw.timer.isActive()
    assert tw.timer.interval() == 300_000  # floor even though settings ask for 60 s
    tw.on_state(make_state(shift=ShiftState.D, source=Source.CLOUD))
    assert chips[-1] == (True, 1, 0.002)
    asleep = NotAvailable("car asleep", kind=ASLEEP)
    tw.on_error(asleep)
    tw.on_error(asleep)
    assert not tw.polling and not tw.timer.isActive()


def test_trip_watch_needs_cloud(qapp: object) -> None:
    tw = TripWatch(_settings(cloud_enabled=False))
    tw.on_state(make_state(shift=ShiftState.D, source=Source.BLE))
    assert not tw.polling and not tw.timer.isActive()


def test_geocode_gate() -> None:
    clock = [1000.0]
    gate = GeocodeGate(clock=lambda: clock[0])
    on = SimpleNamespace(geocode_enabled=True)
    assert not gate.due(SimpleNamespace(geocode_enabled=False), fake=False)
    assert not gate.due(on, fake=True)
    assert gate.due(on, fake=False)
    clock[0] += 30
    assert not gate.due(on, fake=False)  # at most once per minute
    clock[0] += 31
    assert gate.due(on, fake=False)
