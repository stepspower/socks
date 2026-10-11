# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Bluetooth signal helpers: RSSI -> rough distance and a human label (pure, no I/O)."""

from __future__ import annotations

import math
from typing import Any

MIN_DISTANCE_M = 0.5
MAX_DISTANCE_M = 60.0


def estimate_distance_m(rssi: float | None, tx_power: float = -59, n: float = 2.5) -> float | None:
    """Log-distance path loss: d = 10 ** ((tx_power - rssi) / (10 n)), clamped 0.5-60 m.

    Very rough (body, walls and car orientation easily change RSSI by 10 dB).
    """
    if rssi is None or isinstance(rssi, bool) or not math.isfinite(rssi) or n <= 0:
        return None
    distance = 10 ** ((tx_power - rssi) / (10 * n))
    return round(min(MAX_DISTANCE_M, max(MIN_DISTANCE_M, distance)), 1)


def signal_label(rssi: float | None) -> str | None:
    """ "strong" (>= -60 dBm), "ok" (>= -75 dBm), "weak", or None if unknown."""
    if rssi is None or isinstance(rssi, bool) or not math.isfinite(rssi):
        return None
    if rssi >= -60:
        return "strong"
    if rssi >= -75:
        return "ok"
    return "weak"


def device_rssi(device: Any) -> int | None:
    """RSSI BlueZ last reported for a bleak BLEDevice, without an extra scan.

    On Linux ``BLEDevice.details["props"]`` is bleak's live view of the BlueZ
    org.bluez.Device1 properties; BlueZ updates ``RSSI`` from advertisements and may drop
    it while connected, so this is often the value from the discovery scan, or None.
    """
    details = getattr(device, "details", None)
    props = details.get("props") if isinstance(details, dict) else None
    value = props.get("RSSI") if isinstance(props, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    value = int(value)
    return value if -127 <= value <= 20 else None


def format_signal(rssi: int | None) -> str | None:
    """ "-62 dBm (strong, ~3 m)" or None."""
    label = signal_label(rssi)
    if rssi is None or label is None:
        return None
    distance = estimate_distance_m(rssi)
    return f"{rssi} dBm ({label}, ~{distance:g} m)"
