# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Battery-friendly Refresh: asleep → no wake, last state kept; Wake & refresh wakes."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from socks.core.backend import ASLEEP, NotAvailable
from tests.fakes import make_state

pytestmark = pytest.mark.usefixtures("qapp")


class SleepyCar:
    def __init__(self) -> None:
        self.wakes: list[bool] = []
        self.state = make_state(soc=61)

    async def read_state(self, wake: bool = False, include_location: bool = True) -> object:
        self.wakes.append(wake)
        if not wake:
            raise NotAvailable("Bluetooth: car is asleep (not woken)", kind=ASLEEP)
        return self.state

    async def close(self) -> None:
        return None


def _window(tmp_path: Path, may_wake: bool = False):  # noqa: ANN202
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QIcon

    from socks.core.config import Settings
    from socks.gui.main_window import AppContext, MainWindow
    from socks.gui.widgets.log_view import QtLogHandler

    prefs = QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    prefs.setValue("refresh_may_wake", may_wake)
    extras = {"settings_path": tmp_path / "settings.toml", "config_dir": tmp_path}
    win = MainWindow(AppContext(Settings(), True, False, QIcon(), QtLogHandler(), prefs, extras))
    win._backend = SleepyCar()
    win._recorder = None
    win.poll_timer.stop()
    return win


def _settle(qapp: object, win: object) -> None:
    deadline = time.monotonic() + 5
    while win.bridge.busy(win.status_tab) and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()


def _close(win: object) -> None:
    win.bridge.shutdown()
    win.trip_watch.stop()
    win.deleteLater()


def test_refresh_keeps_last_state_and_offers_wake(qapp: object, tmp_path: Path) -> None:
    win = _window(tmp_path)
    try:
        last = make_state(soc=55)
        win._show_state(last)
        win.refresh()
        _settle(qapp, win)
        assert win._backend.wakes == [False]  # never woke
        assert win._last is last  # last known state still shown
        assert win.status_tab.wake_btn.isVisibleTo(win.status_tab)
        assert win.status_tab.chip.text().startswith("asleep · last read ")
        assert "asleep" in win.statusBar().currentMessage()
        win.status_tab.wake_btn.click()
        _settle(qapp, win)
        assert win._backend.wakes == [False, True]
        assert win._last.soc == 61 and not win.status_tab.wake_btn.isVisibleTo(win.status_tab)
    finally:
        _close(win)


def test_first_read_does_not_wake(qapp: object, tmp_path: Path) -> None:
    win = _window(tmp_path)
    try:
        win.first_read()
        _settle(qapp, win)
        assert win._backend.wakes == [False]
    finally:
        _close(win)


def test_setting_restores_waking_refresh(qapp: object, tmp_path: Path) -> None:
    win = _window(tmp_path, may_wake=True)
    try:
        win.refresh()
        _settle(qapp, win)
        assert win._backend.wakes == [True]
    finally:
        _close(win)
