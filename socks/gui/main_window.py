# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Main window: Status · Controls · History · Settings, status bar, tray icon.

All car I/O is started here and only here: ``refresh`` and ``_on_command``
(through the single ``confirm`` gate). Backend calls run on the bridge's I/O
thread; results come back to GUI-side coroutines that update widgets.
"""

from __future__ import annotations

import contextlib
import html
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
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QSplitter,
    QSystemTrayIcon,
    QTabWidget,
    QToolButton,
)

from socks.core.commands import COMMANDS
from socks.core.config import paths
from socks.core.model import Source
from socks.gui import fmt
from socks.gui.async_bridge import AsyncBridge, describe
from socks.gui.location import (
    GeocodeGate,
    TripWatch,
    geocode_once,
    place_label,
    rebuild_labels,
    rename_place,
    set_purpose,
)
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
        self._vin: str = ctx.settings.vin
        self._last: Any = None
        self._quitting = False
        self._fake_cloud_backend: Any = None
        self._wizard: Any = None

        # Qt appends the application display name ("— SOCKS").
        self.setWindowTitle("fake car" if ctx.fake else "")
        self.setWindowIcon(ctx.icon)
        self.resize(980, 700)

        commands = COMMANDS
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
        self.status_tab.refresh_requested.connect(lambda: self.refresh())
        self.status_tab.set_commands(commands)
        self.status_tab.command_requested.connect(self._on_command)
        self.controls_tab.command_requested.connect(self._on_command)
        self.history_tab.reload_requested.connect(self.reload_history)
        self.history_tab.export_requested.connect(self._export_history)
        for chart in (self.history_tab.soc_chart, self.history_tab.km_chart):
            chart.saved.connect(self._chart_saved)
        self.settings_tab.save_requested.connect(self._save_settings)
        self.settings_tab.wizard_requested.connect(self.open_wizard)
        self._poll_note = ""
        self.settings_tab.load(ctx.settings, self._close_to_tray(), self._poll_in_background())
        self._apply_units()

        self.age_timer = QTimer(self)
        self.age_timer.setInterval(10_000)
        self.age_timer.timeout.connect(self._tick_age)
        self.age_timer.start()
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll)
        self._apply_poll()

        self._geocode_gate = GeocodeGate()
        self.trip_watch = TripWatch(ctx.settings, self)
        self.trip_watch.poll_requested.connect(self._trip_poll)
        self.trip_watch.changed.connect(self._trip_changed)
        self.trip_chip = QLabel("")
        self.trip_chip.hide()
        self.statusBar().addPermanentWidget(self.trip_chip)
        self.history_tab.purpose_requested.connect(self._set_purpose)
        self.history_tab.place_edited.connect(self._rename_place)
        self.history_tab.rebuild_requested.connect(self._rebuild_labels)

        self.tray: QSystemTrayIcon | None = None
        self._setup_tray()

    # ------------------------------------------------------------- set-up

    def _actions(self) -> None:
        def act(text: str, keys: QKeySequence | str, slot: Any) -> QAction:
            a = QAction(text, self)
            a.setShortcut(QKeySequence(keys))
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            a.triggered.connect(slot)
            self.addAction(a)
            return a

        act("Refresh", QKeySequence.StandardKey.Refresh, lambda *_a: self.refresh())
        act("Refresh", "Ctrl+R", lambda *_a: self.refresh())
        act("Toggle log", "Ctrl+L", self.log_btn.toggle)
        act("Quit", QKeySequence.StandardKey.Quit, self.quit_app)
        for i in range(4):
            act(f"Tab {i + 1}", f"Ctrl+{i + 1}", lambda _c=False, i=i: self.tabs.setCurrentIndex(i))

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.debug("no system tray available")
            return
        tray = QSystemTrayIcon(self.ctx.extras.get("tray_icon") or self.ctx.icon, self)
        menu = QMenu(self)
        menu.addAction("Show SOCKS", self._show_from_tray)
        menu.addAction("Refresh", lambda: self.refresh())
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
            from socks.core import backend as be

            if self.ctx.fake:
                self._backend = be.fake_backend(start=None, delay=0.3)
            else:
                from socks.fleet.oauth import token_provider

                s = self.ctx.settings
                self._backend = be.build_router(s, token_provider(s))
        return self._backend

    def refresh(self, *, wake: bool = True, ble_only: bool = False, poll: bool = False) -> None:
        """Read the car. Manual Refresh may wake it; polls never do (audit M2)."""
        if self.bridge.busy(self.status_tab):
            return
        self.bridge.spawn(self.status_tab, self._refresh(wake, ble_only, poll), "refresh")

    def first_read(self) -> None:
        """Automatic read after first paint: Bluetooth only (never a paid cloud
        fallback); may wake the car like a manual Refresh (free over BLE)."""
        self.refresh(wake=True, ble_only=True)

    async def _refresh(self, wake: bool, ble_only: bool, poll: bool) -> None:
        self.status_tab.set_busy(True)
        try:
            backend = self._get_backend()
            if ble_only and not self.ctx.fake and backend.ble is not None:
                backend = backend.ble
            loc = self._location_enabled()
            state = await self.bridge.io(backend.read_state(wake=wake, include_location=loc))
        except Exception as exc:  # noqa: BLE001 - shown inline, never a modal
            self.trip_watch.on_error(exc)
            msg = describe(exc)
            if poll and is_asleep(exc):
                self._poll_note = f"car asleep — poll at {datetime.now():%H:%M} skipped"
                log.info("poll skipped: car asleep (not woken)")
                self._tick_age()
                return
            log.warning("refresh failed: %s", msg, exc_info=exc if self.ctx.debug else None)
            self.statusBar().showMessage(f"Refresh failed: {msg}")
            return
        finally:
            self.status_tab.set_busy(False)
        self._poll_note = ""
        self.trip_watch.on_state(state)
        self._show_state(state)
        await self._record(state)
        await self._update_location(state)

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
            name = html.escape(s.car_name or "car")  # tooltips may render rich text
            self.tray.setToolTip(f"SOCKS — {name}: {soc} · {rng}")

    def _tick_age(self) -> None:
        s = self._last
        if s is None:
            return
        self.status_tab.update_age()
        src = s.source.value.upper()
        msg = f"Last update {fmt.local_time(s.ts)} ({fmt.age(s.ts)}) via {src}"
        if self._poll_note:
            msg += f" · {self._poll_note}"
        self.statusBar().showMessage(msg)

    def _poll(self) -> None:
        if not self.isVisible() and not self._poll_in_background():
            return  # hidden in the tray: poll only if the user opted in
        self.refresh(wake=False, poll=True)

    def _poll_in_background(self) -> bool:
        return bool(self.ctx.gui_prefs.value("poll_in_background", False, type=bool))

    def _poll_seconds(self) -> int:
        s = self.ctx.settings
        n = s.poll_seconds
        if self.ctx.fake:
            return n or FAKE_POLL_SECONDS
        if n <= 0:
            return 0
        # Enforce the floor here too: settings files can be hand-edited.
        return max(n, s.min_poll_seconds())

    def _apply_poll(self) -> None:
        n = self._poll_seconds()
        if n > 0:
            self.poll_timer.start(n * 1000)
        else:
            self.poll_timer.stop()

    def _units(self) -> str:
        return self.ctx.settings.units or "metric"

    def _apply_units(self) -> None:
        u = self._units()
        self.status_tab.set_units(u)
        self.controls_tab.set_units(u)
        self.history_tab.set_units(u, self.ctx.settings.currency)

    # ------------------------------------------------------------ history

    async def _ensure_history(self) -> Path:
        """Path of the DB to use (a throwaway, seeded one in fake mode)."""
        if self._history_path is None:
            if self.ctx.fake:
                # Never pollute the real history with fake data.
                self._fake_dir = tempfile.mkdtemp(prefix="socks-fake-")
                self._history_path = Path(self._fake_dir) / "history.sqlite"
            else:
                self._history_path = paths().history_db
        if self.ctx.fake and not self._seeded and self._vin:
            self._seeded = True
            from socks.gui.history_io import seed_fake_history

            await self.bridge.run_sync(seed_fake_history, self._history_path, self._vin)
            log.info("seeded throwaway history DB with synthetic data")
        return self._history_path

    async def _record(self, state: Any) -> None:
        try:
            path = await self._ensure_history()
            if self._recorder is None:
                from socks.history.recorder import Recorder

                self._recorder = await self.bridge.run_sync(Recorder, path)
            await self.bridge.run_sync(self._recorder.record, state)
            self._history_dirty = True
            if self._geocode_gate.due(self.ctx.settings, self.ctx.fake):
                n = await self.bridge.io(geocode_once(self.ctx.settings, path, self._vin))
                if n:
                    log.info("resolved %d addresses via OpenStreetMap", n)
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
            tariff = self.ctx.settings.tariff_eur_per_kwh
            from socks.gui.history_io import load_history

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
        note = "synthetic data (fake mode)" if self.ctx.fake else ""
        self.history_tab.show_data(data, note)
        self._history_dirty = False

    def _export_history(self, item: Any) -> None:
        from socks.gui.history_export import default_filename, ensure_suffix, run_export

        if not self._vin:
            self.history_tab.note.setText("Nothing to export yet.")
            return
        days = self.history_tab.period_days()
        since = datetime.now(UTC) - timedelta(days=days) if days else None
        year = datetime.now().year
        name = default_filename(item, since, None, year)
        flt = "JSON (*.json)" if item.fmt == "json" else "CSV (*.csv)"
        path, _f = QFileDialog.getSaveFileName(
            self, f"Export {item.label}", str(Path.home() / name), flt
        )
        if not path:
            return
        path = ensure_suffix(path, item.fmt)

        async def go() -> None:
            self.history_tab.note.setText(f"Exporting {item.label}…")
            try:
                db_path = await self._ensure_history()
                n = await self.bridge.run_sync(
                    run_export, db_path, self._vin, item, path, since, None, self._units(), year
                )
            except Exception as exc:  # noqa: BLE001 - inline, never modal
                msg = describe(exc)
                log.warning("export failed: %s", msg, exc_info=exc if self.ctx.debug else None)
                self.history_tab.note.setText(f"Export failed: {msg}")
                return
            self.history_tab.note.setText(f"Exported {n} rows to {path}")
            log.info("exported %s (%d rows)", item.label, n)

        self.bridge.spawn(self.history_tab.export_btn, go(), "export")

    def _chart_saved(self, path: str, ok: bool) -> None:
        self.history_tab.note.setText(f"Chart saved to {path}" if ok else f"Could not save {path}")

    # ----------------------------------------------------------- commands

    def _on_command(self, cmd: Any, arg: Any) -> None:
        """The single gate: every command that changes the car passes here."""
        if self.bridge.busy(self.controls_tab):
            return
        info = COMMANDS[cmd]
        dangerous = info.dangerous or cmd.value in ALWAYS_DANGEROUS
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
            await self.bridge.io(self._get_backend().send(cmd, arg))
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
        self.ctx.gui_prefs.setValue("poll_in_background", self.settings_tab.poll_bg.isChecked())
        self.ctx.gui_prefs.sync()
        with contextlib.suppress(OSError):
            Path(self.ctx.gui_prefs.fileName()).chmod(0o600)
        if not self.ctx.fake and self._backend is not None:
            old, self._backend = self._backend, None  # rebuilt from new settings on next use
            self.bridge.spawn(self, self.bridge.io(old.close()), "close old backend")
        if s.vin and s.vin != self._vin and not self.ctx.fake:
            self._vin = s.vin
            self._history_dirty = True
        self._apply_units()
        self._apply_poll()
        self.settings_tab.show_result(f"Saved at {datetime.now().strftime('%H:%M:%S')}.", True)
        log.info("settings saved")

    # ------------------------------------------------------------ location

    def _location_enabled(self) -> bool:
        return self.ctx.settings.location_enabled

    async def _update_location(self, s: Any) -> None:
        lat, lon = s.latitude, s.longitude
        if not self._location_enabled() or lat is None or lon is None:
            self.status_tab.set_location(None)
            return
        name = None
        try:
            path = await self._ensure_history()
            name = await self.bridge.run_sync(place_label, path, self._vin, lat, lon)
        except Exception as exc:  # noqa: BLE001 - a missing label is not an error
            log.debug("place lookup failed: %s", describe(exc))
        if name:
            self.status_tab.set_location(name)
        elif self.ctx.extras.get("redact_location"):
            self.status_tab.set_location("(coordinates hidden)")
        else:
            self.status_tab.set_location(f"{lat:.5f}, {lon:.5f}")

    def _trip_poll(self) -> None:
        if self.bridge.busy(self.trip_chip):
            return
        self.bridge.spawn(self.trip_chip, self._trip_read(), "trip watch read")

    async def _trip_read(self) -> None:
        backend = self._get_backend()
        cloud = self._fake_cloud() if self.ctx.fake else backend.cloud
        if cloud is None:
            log.warning("trip watch: no cloud backend (enable the cloud and log in)")
            self.trip_watch.stop()
            return
        try:
            state = await self.bridge.io(cloud.read_state(wake=False, include_location=True))
        except Exception as exc:  # noqa: BLE001
            log.info("trip watch read failed: %s", describe(exc))
            self.trip_watch.on_error(exc)
            return
        self.trip_watch.on_state(state)
        self._show_state(state)
        await self._record(state)
        await self._update_location(state)

    def _fake_cloud(self) -> Any:
        if self._fake_cloud_backend is None:
            from socks.core.backend import fake_backend

            self._fake_cloud_backend = fake_backend(start=None, delay=0.3, source=Source.CLOUD)
        return self._fake_cloud_backend

    def _trip_changed(self, polling: bool, reads: int, usd: float) -> None:
        self.trip_chip.setVisible(polling or reads > 0)
        state = "polling" if polling else "stopped"
        self.trip_chip.setText(f"trip watch: {state} ({reads} reads, ≈ ${usd:.3f})")

    def _set_purpose(self, drive_id: int, purpose: Any) -> None:
        async def go() -> None:
            try:
                path = await self._ensure_history()
                await self.bridge.run_sync(set_purpose, path, drive_id, purpose)
            except Exception as exc:  # noqa: BLE001
                self.history_tab.note.setText(f"Could not set purpose: {describe(exc)}")
                return
            self.history_tab.drives_table.set_purpose(drive_id, purpose)
            self._history_dirty = True
            self.reload_history()  # refresh the yearly totals

        self.bridge.spawn(self.history_tab.drives_table, go(), "set purpose")

    def _rename_place(self, place_id: int, name: str, kind: str) -> None:
        async def go() -> None:
            try:
                path = await self._ensure_history()
                await self.bridge.run_sync(rename_place, path, place_id, name, kind)
            except Exception as exc:  # noqa: BLE001
                self.history_tab.note.setText(f"Could not rename place: {describe(exc)}")
                return
            self.history_tab.note.setText("Place saved. Use Rebuild labels to relabel drives.")

        self.bridge.spawn(self.history_tab.places_table, go(), "rename place")

    def _rebuild_labels(self) -> None:
        async def go() -> None:
            self.history_tab.rebuild_btn.setEnabled(False)
            try:
                path = await self._ensure_history()
                n_places, n_drives = await self.bridge.run_sync(rebuild_labels, path, self._vin)
            except Exception as exc:  # noqa: BLE001
                self.history_tab.note.setText(f"Rebuild failed: {describe(exc)}")
                return
            finally:
                self.history_tab.rebuild_btn.setEnabled(True)
            self.history_tab.note.setText(f"{n_places} places, {n_drives} drives relabelled.")
            self._history_dirty = True
            self.reload_history()

        if self._vin:
            self.bridge.spawn(self.history_tab.rebuild_btn, go(), "rebuild labels")

    # -------------------------------------------------------------- wizard

    def open_wizard(self) -> None:
        from socks.gui.setup_services import FakeServices, RealServices
        from socks.gui.wizard import SetupWizard

        if self._wizard is not None:
            self._wizard.raise_()
            return
        key_dest = Path(self.ctx.extras.get("config_dir", Path.home())) / "private_key.pem"
        if self.ctx.fake:
            services: Any = FakeServices(key_dest, self._get_backend)
        else:
            services = RealServices(key_dest)
        wiz = SetupWizard(self, self.bridge, services, self.ctx.settings, self._units())
        wiz.setWindowIcon(self.ctx.icon)
        wiz.applied.connect(self._wizard_applied)
        wiz.finished.connect(lambda _r: setattr(self, "_wizard", None))
        wiz.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._wizard = wiz
        wiz.open()

    def _wizard_applied(self, wiz: Any) -> None:
        s = self.ctx.settings
        s.vin = wiz.vin
        s.key_path = wiz.key_path
        s.cloud_enabled = wiz.cloud_enabled
        s.client_id = wiz.client_id
        self.settings_tab.load(s, self._close_to_tray(), self._poll_in_background())
        self._save_settings()
        log.info("setup wizard finished")
        if not self.ctx.fake:
            self._vin = s.vin
            self._history_dirty = True
        self.first_read()

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
        self.trip_watch.stop()
        rec, backend = self._recorder, self._backend
        self._recorder = self._backend = None
        self.bridge.shutdown(
            finalizers=[f for f in (backend.close if backend else None, _sync_closer(rec)) if f]
        )
        for tmp in (self._fake_dir, self.ctx.extras.get("fake_cfg_dir")):
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
        if self.tray is not None:
            self.tray.hide()
        QTimer.singleShot(0, QApplication.quit)


def is_asleep(exc: BaseException) -> bool:
    """True when a non-waking read reports the car asleep (``CommandError.kind == ASLEEP``)."""
    kind = getattr(exc, "kind", None)
    if kind is not None:
        from socks.core.backend import ASLEEP

        return str(kind).lower() == ASLEEP.lower()
    return "asleep" in str(exc).lower()  # errors that are not CommandError


def _sync_closer(obj: Any) -> Any:
    if obj is None:
        return None

    async def run() -> None:
        obj.close()

    return run
