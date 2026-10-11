# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Main window: Status · Controls · History · Settings, status bar, tray icon.

All car I/O is started here and only here: ``refresh`` and ``_on_command``
(through the single ``confirm`` gate). Backend calls run on the bridge's I/O
thread; results come back to GUI-side coroutines that update widgets.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QSplitter,
    QSystemTrayIcon,
    QTabWidget,
    QToolButton,
)

from socks.gui import fmt
from socks.gui._deps import (
    Deps,
    history_db_path,
    load_history,
    make_fake_backend,
    make_real_backend,
    seed_fake_history,
)
from socks.gui.async_bridge import AsyncBridge, describe
from socks.gui.tabs.controls import ControlsTab
from socks.gui.tabs.history import HistoryTab
from socks.gui.tabs.settings import SettingsTab
from socks.gui.tabs.status import StatusTab
from socks.gui.widgets.confirm_dialog import confirm
from socks.gui.widgets.log_view import LogView, QtLogHandler

log = logging.getLogger("socks.gui")

#: Defence in depth: these always need typed confirmation, whatever core says.
ALWAYS_DANGEROUS = frozenset({"unlock", "frunk", "trunk", "remote_start"})
FAKE_POLL_SECONDS = 5


@dataclass
class AppContext:
    deps: Deps
    settings: Any
    fake: bool
    debug: bool
    icon: QIcon
    log_handler: QtLogHandler
    gui_prefs: QSettings
    extras: dict[str, Any] = field(default_factory=dict)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.bridge = AsyncBridge(self)
        self._backend: Any = None
        self._recorder: Any = None
        self._history_path: Path | None = None
        self._fake_dir: str | None = None
        self._seeded = False
        self._history_dirty = True
        self._vin: str = getattr(ctx.settings, "vin", "") or ""
        self._last: Any = None
        self._quitting = False

        # Qt appends the application display name ("— SOCKS").
        self.setWindowTitle("fake car" if ctx.fake else "")
        self.setWindowIcon(ctx.icon)
        self.resize(980, 700)

        commands = ctx.deps.commands.COMMANDS
        self.status_tab = StatusTab()
        self.controls_tab = ControlsTab(commands)
        self.history_tab = HistoryTab()
        self.settings_tab = SettingsTab()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        for w, name in (
            (self.status_tab, "&Status"),
            (self.controls_tab, "&Controls"),
            (self.history_tab, "&History"),
            (self.settings_tab, "Se&ttings"),
        ):
            self.tabs.addTab(w, name)
        self.tabs.currentChanged.connect(self._tab_changed)

        self.log_view = LogView()
        self.log_view.hide()
        ctx.log_handler.emitter.line.connect(self._log_line)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tabs)
        split.addWidget(self.log_view)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

        sb = self.statusBar()
        self.log_btn = QToolButton()
        self.log_btn.setText("Log")
        self.log_btn.setCheckable(True)
        self.log_btn.setToolTip("Show the application log (Ctrl+L)")
        self.log_btn.toggled.connect(self.log_view.setVisible)
        self.log_btn.toggled.connect(lambda on: on and self.log_btn.setText("Log"))
        sb.addPermanentWidget(self.log_btn)
        sb.showMessage("Starting…")

        self._actions()
        self.status_tab.refresh_requested.connect(self.refresh)
        self.controls_tab.command_requested.connect(self._on_command)
        self.history_tab.reload_requested.connect(self.reload_history)
        self.settings_tab.save_requested.connect(self._save_settings)
        self.settings_tab.load(ctx.settings, self._close_to_tray())
        self._apply_units()

        self.age_timer = QTimer(self)
        self.age_timer.setInterval(10_000)
        self.age_timer.timeout.connect(self._tick_age)
        self.age_timer.start()
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll)
        self._apply_poll()

        self.tray: QSystemTrayIcon | None = None
        self._setup_tray()

        for name in ctx.deps.shimmed:
            log.warning("using development shim for %s", name)

    # ------------------------------------------------------------- set-up

    def _actions(self) -> None:
        def act(text: str, keys: QKeySequence | str, slot: Any) -> QAction:
            a = QAction(text, self)
            a.setShortcut(QKeySequence(keys))
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            a.triggered.connect(slot)
            self.addAction(a)
            return a

        act("Refresh", QKeySequence.StandardKey.Refresh, self.refresh)
        act("Refresh", "Ctrl+R", self.refresh)
        act("Toggle log", "Ctrl+L", self.log_btn.toggle)
        act("Quit", QKeySequence.StandardKey.Quit, self.quit_app)
        for i in range(4):
            act(f"Tab {i + 1}", f"Ctrl+{i + 1}", lambda _c=False, i=i: self.tabs.setCurrentIndex(i))

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.debug("no system tray available")
            return
        tray = QSystemTrayIcon(self.ctx.icon, self)
        menu = QMenu(self)
        menu.addAction("Show SOCKS", self._show_from_tray)
        menu.addAction("Refresh", self.refresh)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_app)
        tray.setContextMenu(menu)
        tray.setToolTip("SOCKS")
        tray.activated.connect(self._tray_activated)
        tray.show()
        self.tray = tray

    def _close_to_tray(self) -> bool:
        return bool(self.ctx.gui_prefs.value("close_to_tray", False, type=bool))

    # -------------------------------------------------------------- state

    def _get_backend(self) -> Any:
        if self._backend is None:
            if self.ctx.fake:
                self._backend = make_fake_backend(self._vin)
            else:
                self._backend = make_real_backend(self.ctx.settings)
        return self._backend

    def refresh(self) -> None:
        if self.bridge.busy(self.status_tab):
            return
        self.bridge.spawn(self.status_tab, self._refresh(), "refresh")

    async def _refresh(self) -> None:
        self.status_tab.set_busy(True)
        try:
            backend = self._get_backend()
            if backend is None:
                raise RuntimeError("no backend available (core.backend missing)")
            state = await self.bridge.io(backend.read_state())
        except Exception as exc:  # noqa: BLE001 - shown inline, never a modal
            msg = describe(exc)
            log.warning("refresh failed: %s", msg, exc_info=exc if self.ctx.debug else None)
            self.statusBar().showMessage(f"Refresh failed: {msg}")
            return
        finally:
            self.status_tab.set_busy(False)
        self._show_state(state)
        await self._record(state)

    def _show_state(self, s: Any) -> None:
        self._last = s
        if s.vin and s.vin != self._vin:
            self._vin = s.vin
            self._history_dirty = True
        self.status_tab.update_state(s)
        self.controls_tab.update_state(s)
        self._tick_age()
        if self.tray is not None:
            soc = "–" if s.soc is None else f"{s.soc} %"
            rng = fmt.dist(s.rated_range_km, self._units())
            self.tray.setToolTip(f"SOCKS — {s.car_name or 'car'}: {soc} · {rng}")

    def _tick_age(self) -> None:
        s = self._last
        if s is None:
            return
        self.status_tab.update_age()
        src = getattr(s.source, "value", str(s.source)).upper()
        self.statusBar().showMessage(
            f"Last update {fmt.local_time(s.ts)} ({fmt.age(s.ts)}) via {src}"
        )

    def _poll(self) -> None:
        if self.isVisible() or self.tray is not None:
            self.refresh()

    def _poll_seconds(self) -> int:
        n = int(getattr(self.ctx.settings, "poll_seconds", 0) or 0)
        return n or (FAKE_POLL_SECONDS if self.ctx.fake else 0)

    def _apply_poll(self) -> None:
        n = self._poll_seconds()
        if n > 0:
            self.poll_timer.start(n * 1000)
        else:
            self.poll_timer.stop()

    def _units(self) -> str:
        return getattr(self.ctx.settings, "units", "metric") or "metric"

    def _apply_units(self) -> None:
        u = self._units()
        self.status_tab.set_units(u)
        self.controls_tab.set_units(u)
        self.history_tab.set_units(u, getattr(self.ctx.settings, "currency", "EUR"))

    # ------------------------------------------------------------ history

    async def _ensure_history(self) -> Path | None:
        """Path of the DB to use (None when the history module is shimmed)."""
        d = self.ctx.deps
        if d.history_db is None:
            return None
        if self._history_path is None:
            if self.ctx.fake:
                # Never pollute the real history with fake data.
                self._fake_dir = tempfile.mkdtemp(prefix="socks-fake-")
                self._history_path = Path(self._fake_dir) / "history.sqlite"
            else:
                self._history_path = history_db_path(self.ctx.settings)
        if self.ctx.fake and not self._seeded and self._vin:
            self._seeded = True
            await self.bridge.run_sync(seed_fake_history, self._history_path, self._vin)
            log.info("seeded throwaway history DB with synthetic data")
        return self._history_path

    async def _record(self, state: Any) -> None:
        d = self.ctx.deps
        if d.recorder_mod is None:
            return
        try:
            path = await self._ensure_history()
            if path is None:
                return
            if self._recorder is None:
                self._recorder = await self.bridge.run_sync(d.recorder_mod.Recorder, path)
            await self.bridge.run_sync(self._recorder.record, state)
            self._history_dirty = True
            if self.tabs.currentWidget() is self.history_tab:
                self.reload_history()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "recording history failed: %s",
                describe(exc),
                exc_info=exc if self.ctx.debug else None,
            )

    def _tab_changed(self, _i: int) -> None:
        if self.tabs.currentWidget() is self.history_tab and self._history_dirty:
            self.reload_history()

    def reload_history(self) -> None:
        if self.bridge.busy(self.history_tab):
            return
        self.bridge.spawn(self.history_tab, self._reload_history(), "load history")

    async def _reload_history(self) -> None:
        if not self._vin:
            self.history_tab.note.setText("Set a VIN in Settings (or refresh once) to see history.")
            return
        self.history_tab.set_busy(True)
        try:
            path = await self._ensure_history()
            days = self.history_tab.period_days()
            since = datetime.now(UTC) - timedelta(days=days) if days else None
            tariff = float(getattr(self.ctx.settings, "tariff_eur_per_kwh", 0.0) or 0.0)
            data = await self.bridge.run_sync(
                load_history, path, self._vin, since, days or 365, tariff, self._units()
            )
        except Exception as exc:  # noqa: BLE001
            msg = describe(exc)
            log.warning("loading history failed: %s", msg, exc_info=exc if self.ctx.debug else None)
            self.history_tab.set_busy(False)
            self.history_tab.note.setText(f"Could not load history: {msg}")
            return
        self.history_tab.set_busy(False)
        note = "synthetic data (fake mode)" if self.ctx.fake or path is None else ""
        self.history_tab.show_data(data, note)
        self._history_dirty = False

    # ----------------------------------------------------------- commands

    def _on_command(self, cmd: Any, arg: Any) -> None:
        """The single gate: every command that changes the car passes here."""
        if self.bridge.busy(self.controls_tab):
            return
        info = self.ctx.deps.commands.COMMANDS[cmd]
        dangerous = bool(info.dangerous) or getattr(cmd, "value", "") in ALWAYS_DANGEROUS
        detail = [self.controls_tab.describe_arg(cmd, arg)]
        if info.needs_wake:
            detail.append("The car will be woken up if it is asleep.")
        if self.ctx.fake:
            detail.append("Fake mode: nothing is sent to a real car.")
        if not confirm(self, info.label, "\n".join(x for x in detail if x), dangerous):
            self.controls_tab.show_result(f"{info.label}: cancelled, nothing sent.", False)
            return
        self.bridge.spawn(
            self.controls_tab, self._send(cmd, arg, info.label), f"command {info.label}"
        )

    async def _send(self, cmd: Any, arg: Any, label: str) -> None:
        self.controls_tab.set_busy(cmd)
        try:
            backend = self._get_backend()
            if backend is None:
                raise RuntimeError("no backend available (core.backend missing)")
            await self.bridge.io(backend.send(cmd, arg))
        except Exception as exc:  # noqa: BLE001 - shown inline, never a modal
            msg = describe(exc)
            if getattr(exc, "maybe_executed", False):
                msg += " (the car may have executed it; check before retrying)"
            log.warning("%s failed: %s", label, msg, exc_info=exc if self.ctx.debug else None)
            self.controls_tab.show_result(f"{label} failed: {msg}", False)
            return
        finally:
            self.controls_tab.set_busy(None)
        log.info("%s: done", label)
        self.controls_tab.show_result(f"{label}: done.", True)
        self.refresh()

    # ----------------------------------------------------------- settings

    def _save_settings(self) -> None:
        s = self.ctx.settings
        path = self.ctx.extras.get("settings_path")
        try:
            if path is not None:
                s.save(path)
            else:
                s.save()
        except Exception as exc:  # noqa: BLE001
            msg = describe(exc)
            log.warning("saving settings failed: %s", msg, exc_info=exc if self.ctx.debug else None)
            self.settings_tab.show_result(f"Could not save: {msg}", False)
            return
        self.ctx.gui_prefs.setValue("close_to_tray", self.settings_tab.close_to_tray.isChecked())
        self.ctx.gui_prefs.sync()
        with contextlib.suppress(OSError):
            Path(self.ctx.gui_prefs.fileName()).chmod(0o600)
        if not self.ctx.fake and self._backend is not None:
            old, self._backend = self._backend, None  # rebuilt from new settings on next use
            closer = getattr(old, "close", None)
            if closer is not None:
                self.bridge.spawn(self, self.bridge.io(closer()), "close old backend")
        if s.vin and s.vin != self._vin and not self.ctx.fake:
            self._vin = s.vin
            self._history_dirty = True
        self._apply_units()
        self._apply_poll()
        self.settings_tab.show_result(f"Saved at {datetime.now().strftime('%H:%M:%S')}.", True)
        log.info("settings saved")

    # ---------------------------------------------------- log / tray / quit

    def _log_line(self, text: str, level: int) -> None:
        self.log_view.append_line(text, level)
        if level >= logging.WARNING and not self.log_view.isVisible():
            self.log_btn.setText("Log •")

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e: QCloseEvent) -> None:
        if not self._quitting and self.tray is not None and self._close_to_tray():
            e.ignore()
            self.hide()
            return
        e.accept()
        self.quit_app()

    def quit_app(self) -> None:
        """Cancel tasks, close backend/recorder on the I/O thread, then quit."""
        if self._quitting:
            return
        self._quitting = True
        self.poll_timer.stop()
        self.age_timer.stop()
        rec, backend = self._recorder, self._backend
        self._recorder = self._backend = None
        self.bridge.shutdown(finalizers=[f for f in (_closer(backend), _sync_closer(rec)) if f])
        for tmp in (self._fake_dir, self.ctx.extras.get("fake_cfg_dir")):
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
        if self.tray is not None:
            self.tray.hide()
        QTimer.singleShot(0, QApplication.quit)


def _closer(obj: Any) -> Any:
    return getattr(obj, "close", None)


def _sync_closer(obj: Any) -> Any:
    fn = getattr(obj, "close", None)
    if fn is None:
        return None

    async def run() -> None:
        fn()

    return run
