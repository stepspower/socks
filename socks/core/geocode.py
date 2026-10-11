# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Reverse geocoding via OpenStreetMap Nominatim (opt-in: Settings.geocode_enabled).

Sends coordinates to nominatim.openstreetmap.org, so it only runs when the user enabled it.
Follows the Nominatim usage policy: an identifying User-Agent and at most one request per
second (module-wide limiter, 1.1 s). Data (c) OpenStreetMap contributors, ODbL; the UI shows
the attribution. Never raises into the UI: every failure is ``None``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from socks import __version__

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = f"SOCKS/{__version__} (+https://github.com/stepspower/socks)"
MIN_INTERVAL = 1.1  # seconds between requests (policy: max 1/s)
TIMEOUT = 10.0
MAX_LABEL = 120

_ROAD_KEYS = ("road", "pedestrian", "footway", "path", "cycleway", "square", "place")
_TOWN_KEYS = ("town", "city", "village", "hamlet", "municipality", "suburb")

_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}
_last_request = 0.0


async def _rate_limit() -> None:
    """Wait until MIN_INTERVAL has passed since the previous request (process-wide)."""
    global _last_request
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        _locks.clear()  # old loops (tests) are gone
        lock = _locks[loop] = asyncio.Lock()
    async with lock:
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()


def format_label(data: Any) -> str | None:
    """Compact "Road 12, Town" from a Nominatim jsonv2 reply; display_name as fallback."""
    if not isinstance(data, dict):
        return None
    addr = data.get("address")
    addr = addr if isinstance(addr, dict) else {}

    def first(keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = addr.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    road, town = first(_ROAD_KEYS), first(_TOWN_KEYS)
    number = addr.get("house_number")
    if road:
        street = f"{road} {number.strip()}" if isinstance(number, str) and number.strip() else road
        label = f"{street}, {town}" if town and town != road else street
    else:
        display = data.get("display_name")
        if not isinstance(display, str) or not display.strip():
            return town
        parts = [p.strip() for p in display.split(",") if p.strip()]
        label = ", ".join(parts[:2])
    return label[:MAX_LABEL] or None


async def reverse(
    lat: float,
    lon: float,
    *,
    session: Any,
    lang: str = "en",
    base_url: str = NOMINATIM_URL,
) -> str | None:
    """Reverse-geocode one position; ``None`` on any error (network, HTTP, bad data)."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    import aiohttp

    params = {
        "format": "jsonv2",
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "zoom": "17",
        "accept-language": lang or "en",
    }
    try:
        await _rate_limit()
        async with session.get(
            base_url,
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            allow_redirects=False,
        ) as resp:
            if resp.status != 200:
                log.debug("geocode: HTTP %s", resp.status)
                return None
            data = await resp.json(content_type=None)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - never raise into the UI
        log.debug("geocode failed: %s", type(e).__name__)
        return None
    if isinstance(data, dict) and "error" in data:
        return None
    return format_label(data)


class GeocodeRunner:
    """Bridge to history: label pending drive endpoints (only if the user opted in)."""

    def __init__(
        self, settings: Any, session: Any, *, lang: str = "en", base_url: str = NOMINATIM_URL
    ) -> None:
        self.settings = settings
        self.lang = lang
        self.session = session
        self.base_url = base_url

    async def run_once(self, db: Any, vin: str, max_n: int = 20) -> int:
        """Geocode up to ``max_n`` pending positions; returns how many labels were stored."""
        if not getattr(self.settings, "geocode_enabled", False):
            return 0
        from socks.history import places

        stored = 0
        for lat, lon in places.pending_geocodes(db, vin, limit=max_n):
            label = await reverse(
                lat, lon, session=self.session, lang=self.lang, base_url=self.base_url
            )
            if label:
                places.store_geocode(db, lat, lon, label)
                stored += 1
        if stored:
            places.relabel_drives(db, vin)
        return stored
