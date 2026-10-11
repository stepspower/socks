# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Retry Bluetooth transport hiccups for user-initiated reads and commands.

Only a ``NotAvailable`` whose message says the transport/connection failed is retried —
the request provably never reached the car. Never retried: an asleep car, a refusal, or
anything flagged ``maybe_executed`` (a command must never run twice). Polls use a single
attempt so a weak link is not hammered.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Coroutine
from typing import Any

from socks.core.backend import ASLEEP, REFUSED, NotAvailable

ATTEMPTS = 3
DELAYS_S = (1.5, 3.0)  # back-off before attempt 2 and 3
WEAK_RSSI_DBM = -80
_TRANSPORT = re.compile(r"transport|connect", re.IGNORECASE)


def is_transport_failure(exc: BaseException) -> bool:
    if not isinstance(exc, NotAvailable):
        return False
    if getattr(exc, "kind", None) in (ASLEEP, REFUSED) or getattr(exc, "maybe_executed", False):
        return False
    return bool(_TRANSPORT.search(f"{getattr(exc, 'kind', '') or ''} {exc}"))


def weak_signal(rssi_dbm: int | None) -> bool:
    """True when a transport failure is likely the radio link (weak or unknown RSSI)."""
    return rssi_dbm is None or rssi_dbm <= WEAK_RSSI_DBM


async def with_retries(
    bridge: Any,
    make: Callable[[], Coroutine[Any, Any, Any]],
    *,
    retry: bool,
    on_retry: Callable[[int, int], None] = lambda _n, _of: None,
) -> Any:
    """Run ``make()`` on the bridge's I/O thread; retry transport failures if ``retry``.

    ``on_retry(attempt, attempts)`` is called before attempt 2 and 3 (GUI thread).
    """
    attempts = ATTEMPTS if retry else 1
    for i in range(attempts):
        try:
            return await bridge.io(make())
        except Exception as exc:
            if i + 1 >= attempts or not is_transport_failure(exc):
                raise
            on_retry(i + 2, attempts)
            await bridge.io(asyncio.sleep(DELAYS_S[min(i, len(DELAYS_S) - 1)]))
    raise RuntimeError("unreachable")  # pragma: no cover
