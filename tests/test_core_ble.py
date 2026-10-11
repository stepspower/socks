# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tests for socks.core.ble (signal helpers) and the CLI signal line."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Run from a source checkout without installing: make `socks` and `tests.fakes` importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from socks.core.ble import device_rssi, estimate_distance_m, format_signal, signal_label
from socks.core.cli import format_status
from tests.fakes import make_state


def test_estimate_distance() -> None:
    assert estimate_distance_m(-59) == 1.0
    assert estimate_distance_m(-84) == 10.0
    assert estimate_distance_m(-20) == 0.5  # clamped
    assert estimate_distance_m(-120) == 60.0  # clamped
    assert estimate_distance_m(None) is None
    assert estimate_distance_m(float("nan")) is None


@pytest.mark.parametrize(
    ("rssi", "label"), [(-40, "strong"), (-60, "strong"), (-61, "ok"), (-75, "ok"), (-76, "weak")]
)
def test_signal_label(rssi: int, label: str) -> None:
    assert signal_label(rssi) == label


def test_device_rssi_and_format() -> None:
    class Dev:
        def __init__(self, props: object) -> None:
            self.details = {"props": props}

    assert device_rssi(Dev({"RSSI": -62})) == -62
    assert device_rssi(Dev({})) is None
    assert device_rssi(Dev({"RSSI": True})) is None
    assert device_rssi(Dev({"RSSI": 500})) is None
    assert device_rssi(object()) is None and device_rssi(None) is None
    assert signal_label(None) is None
    assert format_signal(-62) == "-62 dBm (ok, ~1.3 m)"
    assert format_signal(None) is None


def test_status_signal_line() -> None:
    assert "Bluetooth     -62 dBm (ok, ~1.3 m)" in format_status(make_state(ble_rssi_dbm=-62))
    assert "Bluetooth" not in format_status(make_state(ble_rssi_dbm=None))
