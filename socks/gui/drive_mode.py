# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Drive mode (fast BLE polling while the car is in gear) and the optional
"keep this computer awake while driving" logind inhibitor.

``DriveMode`` is pure logic: feed it every successful read. It turns on when a *BLE*
read shows the car in gear and off after two consecutive reads in P, or as soon as a
read arrives over the cloud (fast polling is only ever free Bluetooth).

``SleepInhibitor`` takes ``org.freedesktop.login1.Manager.Inhibit("sleep:handle-lid-switch",
"SOCKS", "Recording a drive", "block")`` over the system bus and holds the returned file
descriptor until ``release()``. A refused call (polkit, no logind) is reported, not raised.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

log = logging.getLogger("socks.gui")

DRIVE_POLL_SECONDS = 15
PARKED_READS_TO_EXIT = 2

INHIBIT_WHAT = "sleep:handle-lid-switch"
INHIBIT_WHO = "SOCKS"
INHIBIT_WHY = "Recording a drive"
INHIBIT_MODE = "block"


class DriveMode:
    def __init__(self) -> None:
        self.active = False
        self._parked = 0

    def on_state(self, state: Any) -> bool:
        """Feed a successful read; returns True when ``active`` changed."""
        source = getattr(state.source, "value", str(state.source))
        shift = getattr(state.shift, "value", str(state.shift))
        before = self.active
        if source != "ble":
            self.active, self._parked = False, 0
        elif shift in ("D", "R", "N"):
            self.active, self._parked = True, 0
        elif self.active:
            self._parked += 1
            if self._parked >= PARKED_READS_TO_EXIT:
                self.active, self._parked = False, 0
        return self.active != before


def _system_manager() -> Any:
    from PySide6.QtDBus import QDBusConnection, QDBusInterface

    return QDBusInterface(
        "org.freedesktop.login1",
        "/org/freedesktop/login1",
        "org.freedesktop.login1.Manager",
        QDBusConnection.systemBus(),
    )


def _fd_of(arg: Any) -> int | None:
    """The int fd inside a QDBusUnixFileDescriptor (or a plain int in tests)."""
    if isinstance(arg, int):
        return arg
    getter = getattr(arg, "fileDescriptor", None)
    if getter is None:
        return None
    fd = int(getter())
    return fd if fd >= 0 else None


class SleepInhibitor:
    def __init__(
        self,
        manager_factory: Callable[[], Any] = _system_manager,
        dup: Callable[[int], int] = os.dup,
        close: Callable[[int], None] = os.close,
    ) -> None:
        self._factory, self._dup, self._close = manager_factory, dup, close
        self.fd: int | None = None
        self.error: str | None = None

    @property
    def held(self) -> bool:
        return self.fd is not None

    def acquire(self) -> bool:
        """Take the lock (idempotent). False, with ``error`` set, if refused."""
        if self.fd is not None:
            return True
        self.error = None
        try:
            reply = self._factory().call(
                "Inhibit", INHIBIT_WHAT, INHIBIT_WHO, INHIBIT_WHY, INHIBIT_MODE
            )
        except Exception as exc:  # noqa: BLE001 - no bus, no logind: just report it
            self.error = str(exc) or type(exc).__name__
            return False
        err = reply.errorName() if hasattr(reply, "errorName") else ""
        if err:
            msg = reply.errorMessage() if hasattr(reply, "errorMessage") else ""
            self.error = f"{err}: {msg}".strip(": ")
            return False
        args = reply.arguments() if hasattr(reply, "arguments") else []
        fd = _fd_of(args[0]) if args else None
        if fd is None:
            self.error = "logind returned no lock"
            return False
        # The QDBusUnixFileDescriptor closes its own copy when collected: keep a dup.
        self.fd = self._dup(fd)
        log.info("keeping the computer awake while driving (logind inhibitor)")
        return True

    def release(self) -> None:
        if self.fd is not None:
            try:
                self._close(self.fd)
            finally:
                self.fd = None
                log.info("released the keep-awake lock")
