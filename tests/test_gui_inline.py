# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Status tab click-to-change: value → command mapping (offscreen, no car)."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from socks.core.commands import COMMANDS, Command  # noqa: E402
from socks.core.model import ChargingState  # noqa: E402
from socks.gui.inline_logic import arg_for, hhmm, toggle_command, toggle_text  # noqa: E402
from tests.fakes import make_state  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> object:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.parametrize(
    ("field", "value", "cmd"),
    [
        ("locked", True, "unlock"),
        ("locked", False, "lock"),
        ("locked", None, "lock"),
        ("climate_on", True, "climate_off"),
        ("climate_on", False, "climate_on"),
        ("sentry_on", True, "sentry_off"),
        ("sentry_on", False, "sentry_on"),
        ("charge_port_open", True, "charge_port_close"),
        ("charge_port_open", False, "charge_port_open"),
        ("charging", ChargingState.CHARGING, "charge_stop"),
        ("charging", ChargingState.STOPPED, "charge_start"),
    ],
)
def test_toggle_command(field: str, value: object, cmd: str) -> None:
    assert toggle_command(field, value) == cmd
    Command(cmd)  # every mapped command exists in core


def test_toggle_text() -> None:
    assert toggle_text("locked", True) == "Locked"
    assert toggle_text("locked", False) == "Unlocked"
    assert toggle_text("sentry_on", None).endswith("?")


def test_arg_for_ranges() -> None:
    temp, limit = COMMANDS[Command.SET_TEMP], COMMANDS[Command.SET_CHARGE_LIMIT]
    amps, sched = COMMANDS[Command.SET_CHARGE_AMPS], COMMANDS[Command.SET_SCHEDULED_CHARGING]
    assert arg_for(temp, 21.3, "metric") == 21.5
    assert arg_for(temp, 70.0, "imperial") == 21.0  # 70 °F → 21.1 °C → nearest 0.5
    assert arg_for(temp, 40.0, "metric") == 28.0  # clamped to the car's maximum
    assert arg_for(limit, 49.6, "metric") == 50
    assert arg_for(amps, 40, "metric") == 32
    assert arg_for(sched, 450, "metric") == 450
    assert arg_for(sched, None, "metric") is None  # optional: None turns it off
    with pytest.raises(ValueError):
        arg_for(temp, None, "metric")
    assert hhmm(450) == "07:30" and hhmm(None) == "off"


def test_status_tab_emits_through_signal(qapp: object) -> None:
    from socks.gui.tabs.status import StatusTab

    tab = StatusTab()
    tab.set_commands(COMMANDS)
    tab.update_state(make_state(locked=True, sentry_on=True, climate_on=False, target_temp_c=20.0,
                                charge_limit=80))  # fmt: skip
    got: list[tuple[object, object]] = []
    tab.command_requested.connect(lambda c, a: got.append((c, a)))
    tab._chips["locked"].activated.emit()
    tab._chips["sentry_on"].activated.emit()
    tab._chips["climate_on"].activated.emit()
    ed = tab._editors["set_temp"]
    ed.start()
    assert ed.editing() and ed.editor.value() == 20.0
    ed.editor.setValue(22.0)
    ed._commit()
    assert not ed.editing()
    lim = tab._editors["set_charge_limit"]
    lim.start()
    lim.editor.setValue(90)
    lim._commit()
    sched = tab._editors["set_scheduled_departure"]
    sched.start()
    sched.off.setChecked(False)
    from PySide6.QtCore import QTime

    sched.editor.setTime(QTime(7, 15))
    sched._commit()
    assert got == [
        (Command.UNLOCK, None),
        (Command.SENTRY_OFF, None),
        (Command.CLIMATE_ON, None),
        (Command.SET_TEMP, 22.0),
        (Command.SET_CHARGE_LIMIT, 90),
        (Command.SET_SCHEDULED_DEPARTURE, 435),
    ]


def test_escape_cancels(qapp: object) -> None:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from socks.gui.widgets.inline import InlineEdit

    ed = InlineEdit("spin")
    ed.configure(15, 28, 0.5, "°C")
    ed.set_value("Target 20 °C", 20.0)
    got: list[object] = []
    ed.committed.connect(got.append)
    ed.start()
    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    assert ed._keys.eventFilter(ed.editor, esc)
    assert not ed.editing() and got == []


def test_gauge_tick_hit(qapp: object) -> None:
    from socks.gui.widgets.gauge import Gauge

    g = Gauge()
    g.resize(300, 300)
    g.set_state(70, 50, "", False)  # 50 % → tick straight up
    assert g.tick_hit(150, 150 - 140)
    assert not g.tick_hit(150, 150)  # centre is not the tick
    assert not g.tick_hit(150 + 140, 150)


def test_minutes_of_local() -> None:
    from socks.gui.inline_logic import minutes_of

    dt = datetime(2026, 10, 11, 5, 30, tzinfo=UTC)
    local = dt.astimezone()
    assert minutes_of(dt) == local.hour * 60 + local.minute
    assert minutes_of(None) is None
