# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Bluetooth signal in the Status source chip (offscreen, fake states only)."""

from __future__ import annotations

import pytest

from socks.core.model import Source
from socks.gui.tabs.status import ble_signal
from tests.fakes import make_state

pytestmark = pytest.mark.usefixtures("qapp")


def test_ble_signal_labels() -> None:
    assert ble_signal(-55)[0] == "strong"
    assert ble_signal(-72)[0] == "ok"
    assert ble_signal(-92)[0] == "weak"
    assert float(ble_signal(-59)[1]) == pytest.approx(1.0, abs=1.0)


def _tab(state: object):  # noqa: ANN202
    from socks.gui.tabs.status import StatusTab

    tab = StatusTab()
    tab.update_state(state)
    return tab


def test_chip_shows_rssi_and_glyph_over_ble() -> None:
    tab = _tab(make_state(source=Source.BLE, ble_rssi_dbm=-62))
    text = tab.chip.text()
    assert text.startswith("BLE · ") and "−62 dBm · ~" in text and text.endswith(" m")
    assert tab.chip.signal() in ("strong", "ok")
    assert "rough estimate" in tab.chip.toolTip()


def test_chip_hides_signal_when_unknown_or_cloud() -> None:
    tab = _tab(make_state(source=Source.BLE, ble_rssi_dbm=None))
    assert "dBm" not in tab.chip.text() and tab.chip.signal() is None
    tab = _tab(make_state(source=Source.CLOUD, ble_rssi_dbm=-60))
    assert "dBm" not in tab.chip.text() and tab.chip.signal() is None
