# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""State-aware Controls panel: switch/editor mapping, no confirm dialogs left (offscreen)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from socks.core.commands import COMMANDS, Command
from socks.core.model import ChargingState
from tests.fakes import make_state

pytestmark = pytest.mark.usefixtures("qapp")


def _tab():  # noqa: ANN202
    from socks.gui.tabs.controls import ControlsTab

    return ControlsTab(COMMANDS)


def test_waiting_until_first_read() -> None:
    tab = _tab()
    assert not tab.waiting.isHidden()
    assert all(not sw.isEnabled() for sw in tab.switches.values())
    assert all(not ed.isEnabled() for ed in tab.editors.values())
    assert all(not b.isEnabled() for b, _r in tab.actions.values())


def test_switches_reflect_state() -> None:
    tab = _tab()
    s = make_state(locked=True, sentry_on=False, climate_on=True, plugged_in=False,
                   charging=ChargingState.DISCONNECTED, charge_port_open=False,
                   doors_open=("frunk",))  # fmt: skip
    tab.update_state(s)
    assert tab.waiting.isHidden()
    sw = tab.switches
    assert sw["locked"].logic.checked is True and sw["locked"].isEnabled()
    assert sw["sentry_on"].logic.checked is False
    assert sw["climate_on"].logic.checked is True
    assert sw["charge_port_open"].logic.checked is False
    assert not sw["charging"].isEnabled()  # unplugged: cannot start charging
    assert sw["frunk"].logic.checked is True and not sw["frunk"].isEnabled()  # one-way, open
    assert sw["trunk"].logic.checked is False and sw["trunk"].isEnabled()
    assert tab.editors["set_charge_limit"].display._plain == f"{s.charge_limit} %"


def test_gestures_map_to_commands() -> None:
    tab = _tab()
    tab.update_state(make_state(plugged_in=True, charging=ChargingState.STOPPED))
    got: list[tuple[object, object]] = []
    tab.command_requested.connect(lambda c, a: got.append((c, a)))
    tab.switches["locked"].toggled.emit(False)
    tab.switches["sentry_on"].toggled.emit(False)
    tab.switches["climate_on"].toggled.emit(True)
    tab.switches["charging"].toggled.emit(True)
    tab.switches["charge_port_open"].toggled.emit(True)
    tab.switches["trunk"].toggled.emit(True)
    tab.switches["frunk"].toggled.emit(False)  # no "close frunk" command: ignored
    ed = tab.editors["set_charge_limit"]
    ed.start()
    ed.editor.setValue(85)
    ed._commit()
    tab.actions["flash_lights"][0].click()
    assert got == [
        (Command.UNLOCK, None),
        (Command.SENTRY_OFF, None),
        (Command.CLIMATE_ON, None),
        (Command.CHARGE_START, None),
        (Command.CHARGE_PORT_OPEN, None),
        (Command.TRUNK, None),
        (Command.SET_CHARGE_LIMIT, 85),
        (Command.FLASH_LIGHTS, None),
    ]


def test_every_command_has_a_control() -> None:
    from socks.gui.tabs.controls import used_commands

    tab = _tab()
    covered = used_commands() | set(tab.actions)
    assert {c.value for c in COMMANDS} <= covered


def test_results_inline_next_to_action() -> None:
    tab = _tab()
    tab.show_result("Honk: done", True, Command.HONK)
    _btn, res = tab.actions["honk"]
    assert not res.isHidden() and "Honk: done" in res.text()
    assert tab.result.isHidden()
    tab.show_result("Lock failed: x", False)
    assert not tab.result.isHidden()


def test_no_confirmation_dialogs_remain() -> None:
    """Only the file pickers, the Depart-by dialog and the setup wizard may open dialogs."""
    root = Path(__file__).resolve().parents[1] / "socks" / "gui"
    allowed = {"tabs/depart.py": {"DepartDialog"}, "wizard.py": {"SetupWizard"}}
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        text = path.read_text()
        assert "QMessageBox" not in text and "QInputDialog" not in text, rel
        for cls in re.findall(r"class (\w+)\((?:QDialog|QWizard)\)", text):
            assert cls in allowed.get(rel, set()), f"{rel}: {cls}"
    assert not (root / "widgets" / "confirm_dialog.py").exists()
