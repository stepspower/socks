# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Slide-switch gestures, Depart-by dialog → plan, translation loader (offscreen)."""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from socks.gui.widgets.slide_switch import HOLD_MS, SlideLogic

pytestmark = pytest.mark.usefixtures("qapp")


# ------------------------------------------------------------- slide switch


def test_safe_direction_is_a_click() -> None:
    lg = SlideLogic(checked=False)  # climate off → on: safe
    lg.begin(0.02)
    assert lg.release(0.03) is True


def test_risky_direction_needs_full_drag() -> None:
    lg = SlideLogic(checked=True, risky_off=True)  # locked → unlock: risky
    lg.begin(0.98)
    assert lg.release(0.98) is None  # a click does nothing
    lg.begin(0.98)
    lg.drag(0.3)
    assert lg.release(0.3) is None  # 70 % of the way is not enough
    assert lg.pos == 1.0  # snapped back
    lg.begin(0.98)
    lg.drag(0.0)
    assert lg.release(0.0) is False


def test_risky_safe_back_direction() -> None:
    lg = SlideLogic(checked=False, risky_off=True)  # unlocked → lock: safe
    lg.begin(0.0)
    assert lg.release(0.01) is True


def test_keyboard_hold() -> None:
    lg = SlideLogic(checked=False, risky_on=True)  # open charge port: risky
    assert lg.hold(HOLD_MS // 2) is None
    assert 0.4 < lg.pos < 0.6
    assert lg.hold(HOLD_MS) is True
    safe = SlideLogic(checked=True)
    assert safe.hold(0) is False


def test_one_way() -> None:
    lg = SlideLogic(checked=True, risky_on=True, one_way=True)  # frunk already open
    assert not lg.allowed()
    lg.begin(1.0)
    lg.drag(0.0)
    assert lg.release(0.0) is None
    closed = SlideLogic(checked=False, risky_on=True, one_way=True)
    closed.begin(0.0)
    closed.drag(1.0)
    assert closed.release(1.0) is True


def test_widget_emits_only_after_gesture() -> None:
    from socks.gui.widgets.slide_switch import SlideSwitch

    sw = SlideSwitch("Lock", "Locked", "Unlocked", risky_off=True)
    sw.resize(130, 32)
    sw.set_checked(True)
    got: list[bool] = []
    sw.toggled.connect(got.append)
    sw.logic.begin(0.98)
    sw.logic.drag(0.5)
    assert got == []
    sw.set_checked(True)
    assert sw.accessibleName() == "Lock" and "slide fully" in sw.accessibleDescription()


# ------------------------------------------------------------------ depart


def test_depart_dialog_plan_values() -> None:
    from PySide6.QtCore import QTime

    from socks.gui.tabs.depart import DepartDialog

    dlg = DepartDialog(None, units="metric", charge_to=85)
    assert dlg.time.time() == QTime(7, 30) and dlg.charge.value() == 85
    assert dlg.cabin.value() == 21.0
    dlg.time.setTime(QTime(6, 45))
    dlg.charge.setValue(95)
    dlg.cabin.setValue(22.3)
    now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    plan = dlg.plan(now)
    local = plan.depart_at.astimezone()
    assert (local.hour, local.minute) == (6, 45) and plan.depart_at > now
    assert plan.charge_limit == 95 and plan.temp_c == 22.5  # rounded to the car's 0.5 °C


def test_depart_plan_imperial() -> None:
    from socks.gui.tabs.depart import make_plan

    now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    plan = make_plan(time(7, 30), 120, 70.0, "imperial", now)  # 70 °F ≈ 21.1 °C
    assert plan.charge_limit == 100 and plan.temp_c == 21.0


# --------------------------------------------------------------------- i18n


def test_pick_language() -> None:
    from socks.gui.i18n import pick_language

    assert pick_language("de", "nl_NL") == "de"  # explicit override wins
    assert pick_language("auto", "nl_NL") == "nl"
    assert pick_language(None, "nb_NO") == "nb"
    assert pick_language("auto", "nn_NO") == "nb"
    assert pick_language("auto", "fr_FR") == "en"  # not shipped → English
    assert pick_language("xx", "nl_NL") == "en"


def test_install_dutch_catalogue(qapp: object) -> None:
    from socks.gui import i18n

    assert i18n.qm_path("nl").is_file()
    assert i18n.install(qapp, "nl") == "nl"
    from PySide6.QtCore import QCoreApplication

    assert QCoreApplication.translate("MainWindow", "Refresh") == "Vernieuwen"
    assert i18n.install(qapp, "en") == "en"
    assert QCoreApplication.translate("MainWindow", "Refresh") == "Refresh"
