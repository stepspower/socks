# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""BLE transport retries for user actions (fake backend; offscreen, real AsyncBridge)."""

from __future__ import annotations

import time

import pytest

from socks.core.backend import ASLEEP, REFUSED, CommandError, NotAvailable
from socks.gui import retry
from socks.gui.retry import is_transport_failure, weak_signal, with_retries

pytestmark = pytest.mark.usefixtures("qapp")

TRANSPORT = "car not reachable. ble: BluetoothTransportError: The Bluetooth transport failed"


class Flaky:
    def __init__(self, failures: int, exc: Exception) -> None:
        self.calls, self.failures, self.exc = 0, failures, exc

    async def read_state(self) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return "state"


def _run(qapp: object, coro_factory) -> object:  # noqa: ANN001
    from socks.gui.async_bridge import AsyncBridge

    bridge = AsyncBridge()
    box: dict[str, object] = {}

    async def go() -> None:
        try:
            box["result"] = await coro_factory(bridge)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    task = bridge.spawn(bridge, go(), "test")
    deadline = time.monotonic() + 5
    while not task.done() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    bridge.shutdown()
    assert task.done()
    return box


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry, "DELAYS_S", (0.01, 0.02))


def test_two_transport_failures_then_success(qapp: object) -> None:
    fake = Flaky(2, NotAvailable(TRANSPORT, kind="BluetoothTransportError"))
    shown: list[tuple[int, int]] = []
    box = _run(
        qapp,
        lambda b: with_retries(
            b, fake.read_state, retry=True, on_retry=lambda n, of: shown.append((n, of))
        ),
    )
    assert box == {"result": "state"} and fake.calls == 3
    assert shown == [(2, 3), (3, 3)]


def test_polls_do_not_retry(qapp: object) -> None:
    fake = Flaky(1, NotAvailable(TRANSPORT))
    box = _run(qapp, lambda b: with_retries(b, fake.read_state, retry=False))
    assert isinstance(box["error"], NotAvailable) and fake.calls == 1


def test_gives_up_after_three(qapp: object) -> None:
    fake = Flaky(5, NotAvailable(TRANSPORT))
    box = _run(qapp, lambda b: with_retries(b, fake.read_state, retry=True))
    assert isinstance(box["error"], NotAvailable) and fake.calls == 3


@pytest.mark.parametrize(
    "exc",
    [
        NotAvailable("Bluetooth: car is asleep (not woken for a poll)", kind=ASLEEP),
        CommandError("car refused the command: transport busy", kind=REFUSED),
        CommandError("ble: connect lost after sending", maybe_executed=True),
    ],
)
def test_never_retried(qapp: object, exc: Exception) -> None:
    assert not is_transport_failure(exc)
    fake = Flaky(1, exc)
    box = _run(qapp, lambda b: with_retries(b, fake.read_state, retry=True))
    assert box["error"] is exc and fake.calls == 1


def test_weak_signal_hint() -> None:
    assert weak_signal(None) and weak_signal(-85) and weak_signal(-80)
    assert not weak_signal(-70)
