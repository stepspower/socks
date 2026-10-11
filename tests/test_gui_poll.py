# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""GUI polling safety (audit M2): non-waking reads, asleep detection, interval floor."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from socks.gui.main_window import is_asleep  # noqa: E402
from socks.gui.tabs.settings import SettingsTab  # noqa: E402
from socks.gui.widgets.poll_spinbox import PollSpinBox  # noqa: E402

pytestmark = pytest.mark.usefixtures("qapp")


def test_is_asleep() -> None:
    class NotAvailable(Exception):
        pass

    assert is_asleep(NotAvailable("car asleep; not woken"))
    err = NotAvailable("x")
    from socks.core.backend import ASLEEP
    from socks.core.backend import NotAvailable as CoreNA

    assert is_asleep(CoreNA("Bluetooth: car is asleep", kind=ASLEEP))
    assert not is_asleep(CoreNA("Bluetooth: car is asleep?", kind="Timeout"))
    err.kind = ASLEEP  # type: ignore[attr-defined]
    assert is_asleep(err)
    assert not is_asleep(NotAvailable("out of range"))


def test_settings_tab_poll_floor_follows_cloud(qapp: object, tmp_path: object) -> None:
    from socks.core.config import Settings

    s = Settings(vin="", key_path=tmp_path / "k.pem", poll_seconds=120)  # type: ignore[operator]
    tab = SettingsTab()
    tab.load(s, close_to_tray=False)
    assert tab.poll.minimum_on() == 60
    tab.cloud.setChecked(True)
    assert tab.poll.minimum_on() == 600 and tab.poll.value() == 600
    tab._save()
    assert s.cloud_enabled and s.poll_seconds == 600


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
