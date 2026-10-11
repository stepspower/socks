# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""GUI polling safety (audit M2): non-waking reads, asleep detection, interval floor."""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from socks.gui.main_window import is_asleep, read_state  # noqa: E402
from socks.gui.tabs.settings import min_poll_seconds  # noqa: E402
from socks.gui.widgets.poll_spinbox import PollSpinBox  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> object:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class WakeAware:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def read_state(self, *, wake: bool = True) -> str:
        self.calls.append(wake)
        return "state"


class Legacy:
    async def read_state(self) -> str:
        return "legacy"


def test_read_state_passes_wake_flag() -> None:
    b = WakeAware()
    assert asyncio.run(read_state(b, wake=False)) == "state"
    assert b.calls == [False]


def test_read_state_falls_back_without_kwarg() -> None:
    assert asyncio.run(read_state(Legacy(), wake=False)) == "legacy"


def test_is_asleep() -> None:
    class NotAvailable(Exception):
        pass

    assert is_asleep(NotAvailable("car asleep; not woken"))
    err = NotAvailable("x")
    from socks.core.backend import ASLEEP, NotAvailable as CoreNA

    assert is_asleep(CoreNA("Bluetooth: car is asleep", kind=ASLEEP))
    assert not is_asleep(CoreNA("Bluetooth: car is asleep?", kind="Timeout"))
    err.kind = ASLEEP  # type: ignore[attr-defined]
    assert is_asleep(err)
    assert not is_asleep(NotAvailable("out of range"))


def test_min_poll_fallback() -> None:
    assert min_poll_seconds(object(), cloud_enabled=False) == 60
    assert min_poll_seconds(object(), cloud_enabled=True) == 600


def test_spinbox_floor(qapp: object) -> None:
    sb = PollSpinBox()
    sb.set_minimum_on(60)
    assert sb.allowed(0) == 0
    assert sb.allowed(1) == 60
    assert sb.allowed(59) == 60
    assert sb.allowed(90) == 90
    sb.setValue(0)
    sb.stepBy(1)
    assert sb.value() == 60
    sb.stepBy(-1)
    assert sb.value() == 0
    sb.setValue(120)
    sb.set_minimum_on(600)  # cloud switched on raises a too-short interval
    assert sb.value() == 600
    from PySide6.QtGui import QValidator

    assert sb.validate("30 s", 2)[0] == QValidator.State.Intermediate
    assert sb.validate("900 s", 3)[0] == QValidator.State.Acceptable
    assert sb.fixup("30 s") == "600 s"
