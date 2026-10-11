# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Status title rename (VIN only in the tooltip) and Depart-by step result lines."""

from __future__ import annotations

import pytest

from socks.core import depart
from socks.core.backend import CommandError, NotAvailable
from socks.gui.tabs.depart import error_text, format_steps
from tests.fakes import make_state

pytestmark = pytest.mark.usefixtures("qapp")


def test_title_shows_name_not_vin_and_renames() -> None:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from socks.gui.tabs.status import StatusTab

    tab = StatusTab()
    s = make_state(car_name="Model 3 Long Range")
    tab.update_state(s)
    assert tab.title._plain == "Model 3 Long Range"
    assert s.vin not in tab.title._plain and s.vin in tab.title.toolTip()
    got: list[str] = []
    tab.rename_requested.connect(got.append)
    tab.show()
    tab._start_rename()
    tab.rename.setText("  Blue Bolt ")
    enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    tab.eventFilter(tab.rename, enter)
    tab._start_rename()
    tab.rename.setText("")
    tab.eventFilter(tab.rename, enter)
    tab._start_rename()
    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    tab.eventFilter(tab.rename, esc)  # cancelled: nothing emitted
    assert got == ["Blue Bolt", ""]
    tab.set_name_override("Blue Bolt")
    assert tab.title._plain == "Blue Bolt"
    tab.set_name_override("")
    assert tab.title._plain == "Model 3 Long Range"
    tab.close()


def test_format_steps_ok_already_and_failed() -> None:
    ok = [
        f"{depart.OK}: Set charge limit 75 % (already set)",
        f"{depart.OK}: Set temperature 21 °C",
        f"{depart.OK}: Scheduled departure 07:30 with preconditioning and off-peak charging",
    ]
    text, failed = format_steps(ok)
    lines = text.splitlines()
    assert not failed and len(lines) == 3
    assert lines[0] == "Charge limit 75 % — already set"
    assert lines[1] == "Cabin 21 °C — ok"
    assert lines[2].startswith("Departure 07:30") and lines[2].endswith("— ok")
    bad = [
        f"{depart.OK}: Set charge limit 75 %",
        f"{depart.FAILED}: Set temperature 21 °C: vehicle did not respond",
    ]
    text, failed = format_steps(bad)
    assert failed and text.splitlines()[1] == "Cabin 21 °C — failed: vehicle did not respond"
    assert "car not reachable" not in text


def test_error_text_only_says_unreachable_for_not_available() -> None:
    assert error_text(NotAvailable("car not reachable. ble: out of range")) == "car not reachable"
    assert "car not reachable" not in error_text(CommandError("timeout"))
