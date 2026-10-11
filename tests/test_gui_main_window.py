# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""MainWindow builds in --fake mode and renders a state (offscreen, no event loop, no car)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("qapp")


def test_main_window_constructs_and_shows_state(tmp_path: Path) -> None:
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QIcon

    from socks.core.config import Settings
    from socks.core.model import ShiftState
    from socks.gui.main_window import AppContext, MainWindow
    from socks.gui.widgets.log_view import QtLogHandler
    from tests.fakes import make_state

    prefs = QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    extras = {"settings_path": tmp_path / "settings.toml", "config_dir": tmp_path}
    ctx = AppContext(Settings(), True, False, QIcon(), QtLogHandler(), prefs, extras)
    win = MainWindow(ctx)
    try:
        assert win.tabs.count() == 4
        state = make_state(shift=ShiftState.D)
        win._show_state(state)
        win._drive_mode_update(state)
        assert win.drive_mode.active and win.poll_timer.interval() == 15_000
        assert not win.inhibitor.held  # never in --fake, and off by default
        assert win.status_tab.depart.button.isVisibleTo(win.status_tab)
    finally:
        win.bridge.shutdown()
        win.trip_watch.stop()
        win.deleteLater()
