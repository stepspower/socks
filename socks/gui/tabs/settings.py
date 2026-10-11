# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Settings tab. Edits a ``core.config.Settings`` in place; the window saves it.

The private key is handled by *path only*: this tab never opens or reads the
file, it only shows whether the path exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from socks.core.config import MIN_POLL_BLE, MIN_POLL_CLOUD, MIN_TRIP_WATCH_INTERVAL
from socks.core.keys import is_valid_vin, tesla_control_dir
from socks.gui.i18n import LANGUAGES, pick_language
from socks.gui.widgets.colors import ok, warn
from socks.gui.widgets.poll_spinbox import PollSpinBox

DEFAULT_KEY = tesla_control_dir() / "private_key.pem"
PRICE_NOTE = (
    "The Tesla Fleet API is billed per request: data read $0.002, command $0.001, "
    "wake $0.02. SOCKS uses Bluetooth first; keep polling off unless you need it."
)


CLOUD_READ_USD = 0.002


class SettingsTab(QWidget):
    #: emitted after the Settings object was updated from the form
    save_requested = Signal()
    wizard_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings: Any = None

        self.vin = QLineEdit()
        self.vin.setMaxLength(17)
        self.vin.setPlaceholderText(self.tr("17 characters, e.g. 5YJ3E7EB…"))
        self.vin.textEdited.connect(lambda t: self.vin.setText(t.upper()))

        self.key_path = QLineEdit()
        self.key_path.setPlaceholderText(str(DEFAULT_KEY))
        self.key_path.setAccessibleName(self.tr("Private key path"))
        self.key_path.textChanged.connect(self._key_status)
        self.key_btn = QPushButton(self.tr("Import existing &key…"))
        self.key_btn.setToolTip(
            "Choose the PEM file already paired with your car. Only the path is stored."
        )
        self.key_btn.clicked.connect(self._pick_key)
        self.key_state = QLabel("")
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_path, 1)
        key_row.addWidget(self.key_btn)

        self.units = QComboBox()
        self.units.addItem(self.tr("Metric (km, °C)"), "metric")
        self.units.addItem(self.tr("Imperial (mi, °F)"), "imperial")

        self.prefer = QComboBox()
        self.prefer.addItem(self.tr("Bluetooth first, cloud as fallback"), "ble")
        self.prefer.addItem(self.tr("Cloud first, Bluetooth as fallback"), "cloud")

        self.cloud = QCheckBox(self.tr("Enable Tesla Fleet API (cloud)"))
        self.price = QLabel(PRICE_NOTE)
        self.price.setWordWrap(True)
        self.price.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.region = QComboBox()
        for code, label in (
            ("eu", "Europe / Middle East / Africa"),
            ("na", "North America / APAC"),
            ("cn", "China"),
        ):
            self.region.addItem(label, code)
        self.client_id = QLineEdit()
        self.client_id.setPlaceholderText(self.tr("from developer.tesla.com"))
        self.cloud.toggled.connect(self._cloud_toggled)

        self.poll = PollSpinBox()
        self.poll.setAccessibleName(self.tr("Poll interval"))
        self.poll.setToolTip(
            "Read the car periodically while the window is open (records history). "
            "Polling never wakes a sleeping car."
        )
        self.poll.valueChanged.connect(self._poll_note_update)
        self.poll_note = QLabel("")
        self.poll_note.setWordWrap(True)
        self.poll_note.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.poll_bg = QCheckBox(self.tr("Keep polling in background (while hidden in the tray)"))

        self.tariff = QDoubleSpinBox()
        self.tariff.setRange(0, 10)
        self.tariff.setDecimals(3)
        self.tariff.setSingleStep(0.01)
        self.currency = QLineEdit()
        self.currency.setMaxLength(3)
        self.currency.setMaximumWidth(self.currency.fontMetrics().horizontalAdvance("WWWW") + 16)
        self.currency.textEdited.connect(lambda t: self.currency.setText(t.upper()))
        self.currency.textChanged.connect(lambda t: self.tariff.setSuffix(f" {t or 'EUR'}/kWh"))
        tariff_row = QHBoxLayout()
        tariff_row.addWidget(self.tariff)
        tariff_row.addWidget(QLabel(self.tr("Currency:")))
        tariff_row.addWidget(self.currency)
        tariff_row.addStretch(1)

        self.location = QCheckBox(
            self.tr("Read and show the car's &location (stays on this computer)")
        )
        self.trip_watch = QCheckBox(self.tr("Follow trips via cloud while the car is away"))
        self.trip_interval = QSpinBox()
        self.trip_interval.setRange(300, 3600)
        self.trip_interval.setSingleStep(60)
        self.trip_interval.setSuffix(" s")
        self.trip_interval.setAccessibleName(self.tr("Trip watch interval"))
        self.trip_note = QLabel("")
        self.trip_note.setWordWrap(True)
        self.trip_note.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.trip_interval.valueChanged.connect(self._trip_note_update)
        self.trip_watch.toggled.connect(lambda _on: self._trip_note_update())
        self.geocode = QCheckBox(
            "Resolve addresses with OpenStreetMap (sends coordinates to openstreetmap.org)"
        )
        self.awake = QCheckBox(self.tr("Keep this computer awake while driving (lid closed)"))
        self.awake.setToolTip(
            self.tr(
                "While the car is in gear over Bluetooth, SOCKS asks the system not to "
                "suspend on lid close. Costs laptop battery; the laptop must be awake when "
                "the drive starts."
            )
        )
        self.language = QComboBox()
        for code, name in LANGUAGES.items():
            self.language.addItem(self.tr("System language") if code == "auto" else name, code)
        self.language.setAccessibleName(self.tr("Language"))
        self.language_note = QLabel("")
        self.close_to_tray = QCheckBox(
            self.tr("Closing the window keeps SOCKS running in the system tray")
        )

        self.setup_btn = QPushButton(self.tr("Set &up…"))
        self.setup_btn.setToolTip(self.tr("Run the setup wizard: VIN, key, pairing, cloud"))
        self.setup_btn.clicked.connect(self.wizard_requested)
        self.save_btn = QPushButton(self.tr("Save"))
        self.save_btn.setShortcut(QKeySequence.StandardKey.Save)
        self.save_btn.setToolTip(self.tr("Save settings (Ctrl+S)"))
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save)
        self.result = QLabel("")
        self.result.setWordWrap(True)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.addRow(self.tr("&VIN:"), self.vin)
        form.addRow(QLabel(self.tr("Private key:"), buddy=self.key_path), key_row)
        form.addRow("", self.key_state)
        form.addRow(self.tr("&Units:"), self.units)
        form.addRow(self.tr("Co&nnection:"), self.prefer)
        form.addRow("", self.cloud)
        form.addRow("", self.price)
        form.addRow(self.tr("&Region:"), self.region)
        form.addRow(self.tr("Client &ID:"), self.client_id)
        form.addRow(self.tr("&Poll interval:"), self.poll)
        form.addRow("", self.poll_note)
        form.addRow("", self.poll_bg)
        form.addRow(QLabel(self.tr("Tari&ff:"), buddy=self.tariff), tariff_row)
        form.addRow("", self.location)
        trip_row = QHBoxLayout()
        trip_row.addWidget(self.trip_watch)
        trip_row.addWidget(self.trip_interval)
        trip_row.addStretch(1)
        form.addRow("", trip_row)
        form.addRow("", self.trip_note)
        form.addRow("", self.geocode)
        form.addRow("", self.close_to_tray)
        form.addRow("", self.awake)
        lang_row = QHBoxLayout()
        lang_row.addWidget(self.language)
        lang_row.addWidget(self.language_note, 1)
        form.addRow(QLabel(self.tr("Lan&guage:"), buddy=self.language), lang_row)

        bottom = QHBoxLayout()
        bottom.addWidget(self.setup_btn)
        self.log_btn = QPushButton(self.tr("Open log folder"))
        self.log_btn.setFlat(True)
        self.log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_btn.setForegroundRole(QPalette.ColorRole.Link)
        self.log_btn.clicked.connect(self._open_log_folder)
        self.log_btn.hide()
        self._log_file: Path | None = None
        bottom.addWidget(self.log_btn)
        bottom.addWidget(self.result, 1)
        bottom.addWidget(self.save_btn)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addStretch(1)
        lay.addLayout(bottom)

    # ----------------------------------------------------------- load/save

    def set_language(self, code: str, active: str) -> None:
        """``code``: the saved choice; ``active``: what this session runs in."""
        self._active_language = active
        self._select(self.language, code)
        self.language.currentIndexChanged.connect(self._language_changed)

    def _language_changed(self, _i: int) -> None:
        chosen = pick_language(self.language.currentData())
        changed = chosen != getattr(self, "_active_language", chosen)
        self.language_note.setText(self.tr("Restart SOCKS to apply.") if changed else "")

    def load(self, s: Any, close_to_tray: bool, poll_in_background: bool = False) -> None:
        self._settings = s
        self.vin.setText(s.vin)
        self.key_path.setText(str(s.key_path) if s.key_path else "")
        self._select(self.units, s.units)
        self._select(self.prefer, s.prefer)
        self.cloud.setChecked(s.cloud_enabled)
        self._select(self.region, s.region)
        self.client_id.setText(s.client_id)
        self.poll.setValue(s.poll_seconds)
        self.tariff.setValue(s.tariff_eur_per_kwh)
        self.currency.setText(s.currency or "EUR")
        self.close_to_tray.setChecked(close_to_tray)
        self.location.setChecked(s.location_enabled)
        self.trip_watch.setChecked(s.trip_watch_enabled)
        self.trip_interval.setValue(s.trip_watch_interval_seconds)
        self.geocode.setChecked(s.geocode_enabled)
        self.poll_bg.setChecked(poll_in_background)
        self._cloud_toggled(self.cloud.isChecked())
        self._key_status(self.key_path.text())

    @staticmethod
    def _select(combo: QComboBox, data: str) -> None:
        i = combo.findData(data)
        combo.setCurrentIndex(max(0, i))

    def _save(self) -> None:
        vin = self.vin.text().strip().upper()
        if vin and not is_valid_vin(vin):
            self.show_result(
                "VIN must be 17 characters (letters except I, O, Q, and digits).", False
            )
            self.vin.setFocus()
            return
        s = self._settings
        if s is None:
            return
        s.vin = vin
        kp = self.key_path.text().strip()
        s.key_path = Path(kp).expanduser() if kp else type(s)().key_path
        s.units = self.units.currentData()
        s.prefer = self.prefer.currentData()
        s.cloud_enabled = self.cloud.isChecked()
        s.region = self.region.currentData()
        s.client_id = self.client_id.text().strip()
        self._update_poll_minimum()
        s.poll_seconds = self.poll.allowed(int(self.poll.value()))
        self.poll.setValue(s.poll_seconds)
        s.tariff_eur_per_kwh = float(self.tariff.value())
        s.currency = (self.currency.text().strip() or "EUR").upper()
        s.location_enabled = self.location.isChecked()
        s.trip_watch_enabled = self.trip_watch.isChecked() and self.cloud.isChecked()
        s.trip_watch_interval_seconds = max(MIN_TRIP_WATCH_INTERVAL, self.trip_interval.value())
        s.geocode_enabled = self.geocode.isChecked()
        self.save_requested.emit()

    def show_result(self, text: str, success: bool) -> None:
        pal = self.result.palette()
        pal.setColor(self.result.foregroundRole(), ok(pal) if success else warn(pal))
        self.result.setPalette(pal)
        self.result.setText(text)

    # -------------------------------------------------------------- helpers

    def _pick_key(self) -> None:
        start = self.key_path.text().strip() or str(DEFAULT_KEY)
        path, _flt = QFileDialog.getOpenFileName(
            self,
            self.tr("Import existing private key"),
            start,
            self.tr("PEM key (*.pem);;All files (*)"),
        )
        if path:
            self.key_path.setText(path)

    def set_log_file(self, path: Path | None) -> None:
        self._log_file = path
        self.log_btn.setVisible(path is not None)
        if path is not None:
            self.log_btn.setToolTip(str(path))

    def _open_log_folder(self) -> None:
        if self._log_file is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._log_file.parent)))

    def _key_status(self, text: str) -> None:
        p = Path(text.strip() or str(DEFAULT_KEY)).expanduser()
        pal = self.key_state.palette()
        try:
            exists = p.is_file()
            loose = exists and (p.stat().st_mode & 0o077) != 0
        except OSError:
            exists, loose = False, False
        if not exists:
            msg, good = self.tr("Key file not found at this path."), False
        elif loose:
            msg, good = "Key file found, but readable by other users (expected mode 0600).", False
        else:
            msg, good = "Key file found. Its contents are never displayed.", True
        pal.setColor(self.key_state.foregroundRole(), ok(pal) if good else warn(pal))
        self.key_state.setPalette(pal)
        self.key_state.setText(msg)

    def _update_poll_minimum(self) -> None:
        self.poll.set_minimum_on(MIN_POLL_CLOUD if self.cloud.isChecked() else MIN_POLL_BLE)
        self._poll_note_update()

    def _poll_note_update(self, *_a: object) -> None:
        lo = self.poll.minimum_on()
        text = self.tr(
            "0 = off; otherwise at least {0} s. Polling never wakes a sleeping car."
        ).format(lo)
        v = self.poll.value()
        if self.cloud.isChecked():
            text += " " + self.tr(
                "With the cloud enabled, a poll that falls back to the cloud is a paid read."
            )
            if v:
                text += " " + self.tr("Up to ${0:.2f} a day at this interval.").format(
                    86_400 / v * CLOUD_READ_USD
                )
        self.poll_note.setText(text)

    def _trip_note_update(self, *_a: object) -> None:
        v = max(300, self.trip_interval.value())
        per_hour = 3600 / v * CLOUD_READ_USD
        text = self.tr(
            "Starts when the car leaves Bluetooth range moving; stops when it is parked. "
            "Each poll is a paid cloud read ($0.002): about ${0:.3f} per hour of driving."
        ).format(per_hour)
        if not self.cloud.isChecked():
            text = self.tr("Requires cloud access.") + " " + text
        self.trip_note.setText(text)

    def _cloud_toggled(self, on: bool) -> None:
        self.trip_watch.setEnabled(on and self._settings is not None)
        self.trip_interval.setEnabled(on)
        self._trip_note_update()
        self._update_poll_minimum()
        self.region.setEnabled(on)
        self.client_id.setEnabled(on)
        self.prefer.setEnabled(on)

    def focus_default(self) -> None:
        self.vin.setFocus(Qt.FocusReason.OtherFocusReason)
