# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Location features for the GUI: places, drive purposes, trip watch, geocoding.

The blocking helpers open the history DB themselves and run on the bridge's I/O
thread. ``TripWatch`` wires core.tripwatch.TripWatcher to a Qt timer that drives
cloud reads while a trip is being followed. Coordinates are never logged.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from socks.core import tripwatch

log = logging.getLogger("socks.gui")

PURPOSE_CYCLE: tuple[str | None, ...] = (None, "business", "private", "commute")
PURPOSE_KEYS = {"b": "business", "p": "private", "c": "commute"}
GEOCODE_MIN_INTERVAL = 60.0
MIN_TRIP_WATCH = 300


def next_purpose(current: str | None) -> str | None:
    """One click: — → business → private → commute → —."""
    try:
        i = PURPOSE_CYCLE.index(current)
    except ValueError:
        i = 0
    return PURPOSE_CYCLE[(i + 1) % len(PURPOSE_CYCLE)]


def purpose_for_key(key_text: str, is_delete: bool) -> tuple[bool, str | None]:
    """(handled, purpose) for B/P/C/Del pressed on a drive row."""
    if is_delete:
        return True, None
    p = PURPOSE_KEYS.get(key_text.lower())
    return (p is not None), p


# ----------------------------------------------------------- I/O-thread helpers


def set_purpose(db_path: Path, drive_id: int, purpose: str | None) -> None:
    from socks.gui.history_io import with_db
    from socks.history import trips

    with_db(db_path, lambda db: trips.set_drive_purpose(db, drive_id, purpose))


def rename_place(db_path: Path, place_id: int, name: str, kind: str | None) -> None:
    from socks.gui.history_io import with_db
    from socks.history import places

    with_db(db_path, lambda db: places.name_place(db, place_id, name, kind))


def rebuild_labels(db_path: Path, vin: str) -> tuple[int, int]:
    """learn_places + relabel_drives; returns (places, drives changed)."""
    from socks.gui.history_io import with_db
    from socks.history import places

    def go(db: Any) -> tuple[int, int]:
        found = places.learn_places(db, vin)
        return len(found), int(places.relabel_drives(db, vin))

    return with_db(db_path, go)


def place_label(
    db_path: Path | None, vin: str, lat: float, lon: float
) -> tuple[str | None, str | None]:
    """(learned place name, cached geocode address) for a position."""
    if db_path is None:
        return None, None
    from socks.gui.history_io import with_db
    from socks.history import places

    def go(db: Any) -> tuple[str | None, str | None]:
        p = places.place_at(db, vin, lat, lon)
        return (p.display_name if p is not None else None), places.cached_geocode(db, lat, lon)

    return with_db(db_path, go)


async def geocode_once(settings: Any, db_path: Path, vin: str) -> int:
    """Runs on the I/O loop: label pending drive endpoints via OpenStreetMap (opt-in)."""
    import aiohttp

    from socks.core.geocode import GeocodeRunner
    from socks.history import db as dbm

    db = dbm.open(db_path)
    try:
        async with aiohttp.ClientSession() as session:
            return int(await GeocodeRunner(settings, session).run_once(db, vin))
    finally:
        db.close()


class GeocodeGate:
    """At most one geocoding run per minute, only when enabled and not in fake mode."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: float | None = None

    def due(self, settings: Any, fake: bool) -> bool:
        if fake or not settings.geocode_enabled:
            return False
        now = self._clock()
        if self._last is not None and now - self._last < GEOCODE_MIN_INTERVAL:
            return False
        self._last = now
        return True


# ------------------------------------------------------------------ trip watch


class TripWatch(QObject):
    """Feeds reads to TripWatcher; while it says so, polls the cloud on a timer."""

    #: (polling, reads, usd) for the status-bar chip
    changed = Signal(bool, int, float)
    #: ask the window to run one cloud read (non-waking, with location)
    poll_requested = Signal()

    def __init__(self, settings: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_requested)
        self._tw = tripwatch.TripWatcher(settings)

    @property
    def polling(self) -> bool:
        return self._tw.polling

    def interval_s(self) -> int:
        return max(MIN_TRIP_WATCH, self.settings.trip_watch_interval_seconds)

    def on_state(self, state: Any) -> None:
        self._apply(self._tw.on_state(state))

    def on_error(self, exc: BaseException, now: datetime | None = None) -> None:
        asleep = tripwatch.is_asleep_error(exc)
        self._apply(self._tw.on_unavailable(now or datetime.now(UTC), asleep=asleep))

    def stop(self) -> None:
        self.timer.stop()

    def _apply(self, action: Any) -> None:
        name = action.name
        if name == "START_CLOUD_POLL":
            self.timer.start(self.interval_s() * 1000)
            log.info("trip watch: car left, following via cloud every %d s", self.interval_s())
        elif name == "STOP_CLOUD_POLL":
            self.timer.stop()
            log.info("trip watch: stopped after %d cloud reads", self._tw.polls)
        self.changed.emit(self.polling, self._tw.polls, tripwatch.estimate_cost(self._tw.polls))
