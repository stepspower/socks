# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Run coroutines from Qt safely, without QtAsyncio.

* The GUI thread runs the plain Qt event loop. GUI-side coroutines started with
  ``spawn`` are driven by a tiny trampoline (``GuiTask``) on that thread, so
  they may touch widgets directly. They are tied to an owner object: when the
  owner is destroyed or the bridge shuts down they are cancelled
  (``asyncio.CancelledError`` is thrown into them, so ``finally`` runs).
* The only thing a GUI coroutine may await is ``bridge.io(coro)`` /
  ``bridge.run_sync(fn, ...)``. Those run on a plain asyncio loop in one daemon
  thread (started lazily, never before the window shows) — the place for
  aiohttp, bleak and SQLite. Completion is delivered back to the GUI thread by
  a queued Qt signal; cancelling the GUI task cancels the I/O task too.

Exceptions from spawned tasks are logged on the ``socks.gui`` logger as one
line (type + message). The in-app log view never shows tracebacks; under
``--debug`` the stderr handler includes them.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Awaitable, Callable, Coroutine, Generator
from typing import Any, TypeVar

from PySide6.QtCore import QObject, Qt, Signal

T = TypeVar("T")
log = logging.getLogger("socks.gui")


class IoAwaitable[T]:
    """Awaitable for a concurrent future; only GuiTask knows how to wait on it."""

    def __init__(self, cf: concurrent.futures.Future[T]) -> None:
        self.cf = cf

    def __await__(self) -> Generator[concurrent.futures.Future[T], None, T]:
        yield self.cf
        return self.cf.result()


class GuiTask:
    """A coroutine stepped on the GUI thread; suspends only on IoAwaitable."""

    def __init__(
        self, bridge: AsyncBridge, coro: Coroutine[Any, Any, Any], key: int, name: str
    ) -> None:
        self._bridge, self._coro, self.key, self.name = bridge, coro, key, name
        self._waiting: concurrent.futures.Future[Any] | None = None
        self._done = False
        self._cancelled = False

    def done(self) -> bool:
        return self._done

    def cancelled(self) -> bool:
        return self._cancelled

    def start(self) -> None:
        self._step(lambda: self._coro.send(None))

    def resume(self, cf: concurrent.futures.Future[Any]) -> None:
        if self._done or cf is not self._waiting:
            return
        self._waiting = None
        if cf.cancelled():
            self._step(lambda: self._coro.throw(asyncio.CancelledError()))
        else:
            # IoAwaitable.__await__ re-raises the I/O exception, if any.
            self._step(lambda: self._coro.send(None))

    def cancel(self) -> None:
        if self._done:
            return
        if self._waiting is not None:
            self._bridge._by_future.pop(self._waiting, None)
            self._waiting.cancel()
            self._waiting = None
        self._step(lambda: self._coro.throw(asyncio.CancelledError()))

    def _step(self, advance: Callable[[], Any]) -> None:
        try:
            yielded = advance()
        except StopIteration:
            self._finish(None)
            return
        except asyncio.CancelledError:
            self._cancelled = True
            self._finish(None)
            return
        except Exception as exc:  # noqa: BLE001 - routed to the log
            self._finish(exc)
            return
        if not isinstance(yielded, concurrent.futures.Future):
            self._coro.close()
            self._finish(TypeError("GUI coroutines may only await bridge.io()/run_sync()"))
            return
        self._waiting = yielded
        self._bridge._by_future[yielded] = self
        yielded.add_done_callback(self._bridge._notify)

    def _finish(self, exc: BaseException | None) -> None:
        self._done = True
        self._bridge._task_done(self, exc)


class AsyncBridge(QObject):
    #: emitted from the I/O thread; queued to the GUI thread
    _resume = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._io_loop: asyncio.AbstractEventLoop | None = None
        self._io_thread: threading.Thread | None = None
        self._tasks: dict[int, set[GuiTask]] = {}
        self._by_future: dict[concurrent.futures.Future[Any], GuiTask] = {}
        self._closed = False
        # Always queued, also when the future completes on the GUI thread itself.
        self._resume.connect(self._on_resume, Qt.ConnectionType.QueuedConnection)

    # ----------------------------------------------------------- GUI side

    def spawn(self, owner: QObject, aw: Awaitable[Any], name: str) -> GuiTask | None:
        """Run ``aw`` as a GUI-thread task tied to ``owner``. Starts immediately."""
        coro = aw if isinstance(aw, Coroutine) else _wrap(aw)
        if self._closed:
            coro.close()
            return None
        key = id(owner)
        if key not in self._tasks:
            self._tasks[key] = set()
            owner.destroyed.connect(lambda *_a, k=key: self._cancel_key(k))
        task = GuiTask(self, coro, key, name)
        self._tasks[key].add(task)
        task.start()
        return task

    def busy(self, owner: QObject) -> bool:
        return any(not t.done() for t in self._tasks.get(id(owner), ()))

    def _notify(self, cf: concurrent.futures.Future[Any]) -> None:
        # Any thread. Queued signal hops to the GUI thread.
        if not self._closed:
            self._resume.emit(cf)

    def _on_resume(self, cf: concurrent.futures.Future[Any]) -> None:
        task = self._by_future.pop(cf, None)
        if task is not None:
            task.resume(cf)

    def _task_done(self, task: GuiTask, exc: BaseException | None) -> None:
        self._tasks.get(task.key, set()).discard(task)
        if task.cancelled():
            log.debug("%s cancelled", task.name)
        elif exc is not None:
            log.error("%s failed: %s", task.name, describe(exc), exc_info=exc)

    def _cancel_key(self, key: int) -> None:
        for t in list(self._tasks.pop(key, ())):
            t.cancel()

    # ------------------------------------------------------------ I/O side

    def _ensure_io(self) -> asyncio.AbstractEventLoop:
        if self._io_loop is None:
            loop = asyncio.SelectorEventLoop()
            th = threading.Thread(target=loop.run_forever, name="socks-io", daemon=True)
            th.start()
            self._io_loop, self._io_thread = loop, th
        return self._io_loop

    def io(self, coro: Coroutine[Any, Any, T]) -> IoAwaitable[T]:
        """``await bridge.io(coro)`` from a GUI task: runs ``coro`` on the I/O thread."""
        if self._closed:
            coro.close()
            cf: concurrent.futures.Future[T] = concurrent.futures.Future()
            cf.cancel()
            return IoAwaitable(cf)
        return IoAwaitable(asyncio.run_coroutine_threadsafe(coro, self._ensure_io()))

    def run_sync(self, fn: Callable[..., T], *args: Any) -> IoAwaitable[T]:
        """Run a blocking function on the I/O thread (keeps SQLite on one thread)."""

        async def call() -> T:
            return fn(*args)

        return self.io(call())

    # ------------------------------------------------------------ shutdown

    def shutdown(
        self,
        timeout: float = 2.0,
        finalizers: list[Callable[[], Coroutine[Any, Any, Any]]] | None = None,
    ) -> None:
        """Cancel everything, run ``finalizers`` on the I/O loop, stop the thread. Idempotent."""
        if self._closed:
            return
        for key in list(self._tasks):
            self._cancel_key(key)
        self._closed = True
        loop, th = self._io_loop, self._io_thread
        if loop is None or th is None:
            for fin in finalizers or ():
                fin().close()
            return

        async def drain() -> None:
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for fin in finalizers or ():
                try:
                    await asyncio.wait_for(fin(), timeout / 2)
                except Exception as exc:  # noqa: BLE001 - best effort on the way out
                    log.debug("finalizer failed: %s", describe(exc))
            await loop.shutdown_asyncgens()

        try:
            asyncio.run_coroutine_threadsafe(drain(), loop).result(timeout)
        except (concurrent.futures.TimeoutError, concurrent.futures.CancelledError, RuntimeError):
            log.debug("I/O loop drain timed out")
        loop.call_soon_threadsafe(loop.stop)
        th.join(timeout)
        if not th.is_alive():
            loop.close()


async def _wrap[R](aw: Awaitable[R]) -> R:
    return await aw


def describe(exc: BaseException) -> str:
    """One-line, traceback-free description for the UI."""
    msg = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    name = type(exc).__name__
    return f"{name}: {msg}" if msg else name
