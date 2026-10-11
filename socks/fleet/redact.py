# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Strip vehicle location from raw Tesla payloads before they are shown (audit L4).

``redact_location(payload)`` returns a deep copy with every coordinate, heading and
navigation-route field removed, at any nesting depth (cloud ``drive_state`` and the
BLE protobuf-derived dicts alike). The input is not modified.
"""

from __future__ import annotations

from typing import Any

LOCATION_KEYS = frozenset(
    {
        "latitude",
        "longitude",
        "heading",
        "native_latitude",
        "native_longitude",
        "native_type",
        "native_location_supported",
        "corrected_latitude",
        "corrected_longitude",
        "gps_as_of",
        "location",
        "location_state",
        "location_data",
    }
)
_LOCATION_PREFIXES = ("active_route_", "route_")
_LOCATION_SUFFIXES = ("_latitude", "_longitude", "_lat", "_lng", "_lon")


def is_location_key(key: str) -> bool:
    k = key.lower()
    return k in LOCATION_KEYS or k.startswith(_LOCATION_PREFIXES) or k.endswith(_LOCATION_SUFFIXES)


def redact_location(payload: Any) -> Any:
    """Deep copy of ``payload`` (dict/list/scalars) without location fields."""
    if isinstance(payload, dict):
        return {
            k: redact_location(v)
            for k, v in payload.items()
            if not (isinstance(k, str) and is_location_key(k))
        }
    if isinstance(payload, list | tuple):
        return type(payload)(redact_location(v) for v in payload)
    return payload
