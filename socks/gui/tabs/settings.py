# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Settings tab. Edits a ``core.config.Settings`` in place; the window saves it.

The private key is handled by *path only*: this tab never opens or reads the
file, it only shows whether the path exists.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
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
    QVBoxLayout,
    QWidget,
)

from socks.gui.widgets.colors import ok, warn
from socks.gui.widgets.poll_spinbox import PollSpinBox

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
DEFAULT_KEY = Path.home() / ".config/tesla/private_key.pem"
PRICE_NOTE = (
    "The Tesla Fleet API is billed per request: data read $0.002, command $0.001, "
    "wake $0.02. SOCKS uses Bluetooth first; keep polling off unless you need it."
)


CLOUD_READ_USD = 0.002
FALLBACK_MIN_POLL = (60, 600)  # (BLE only, cloud enabled) until core provides min_poll_seconds


def min_poll_seconds(settings: Any, cloud_enabled: bool) -> int:
    """Minimum non-zero poll interval; prefers core's ``Settings.min_poll_seconds()``."""
    fn = getattr(settings, "min_poll_seconds", None)
    if callable(fn) and dataclasses.is_dataclass(settings):
        try:
            return int(
                dataclasses.replace(settings, cloud_enabled=cloud_enabled).min_poll_seconds()
            )
        except (TypeError, ValueError):
            pass
    return FALLBACK_MIN_POLL[1] if cloud_enabled else FALLBACK_MIN_POLL[0]


class SettingsTab(QWidget):
    #: emitted after the Settings object was updated from the form
    save_requested = Signal()
    wizard_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings: Any = None

        self.vin = QLineEdit()
        self.vin.setMaxLength(17)
        self.vin.setPlaceholderText("17 characters, e.g. 5YJ3E7EB…")
        self.vin.textEdited.connect(lambda t: self.vin.setText(t.upper()))

        self.key_path = QLineEdit()
        self.key_path.setPlaceholderText(str(DEFAULT_KEY))
        self.key_path.setAccessibleName("Private key path")
        self.key_path.textChanged.connect(self._key_status)
        self.key_btn = QPushButton("Import existing &key…")
        self.key_btn.setToolTip(
            "Choose the PEM file already paired with your car. Only the path is stored."
        )
        self.key_btn.clicked.connect(self._pick_key)
        self.key_state = QLabel("")
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_path, 1)
        key_row.addWidget(self.key_btn)

        self.units = QComboBox()
        self.units.addItem("Metric (km, °C)", "metric")
        self.units.addItem("Imperial (mi, °F)", "imperial")

        self.prefer = QComboBox()
        self.prefer.addItem("Bluetooth first, cloud as fallback", "ble")
        self.prefer.addItem("Cloud first, Bluetooth as fallback", "cloud")

        self.cloud = QCheckBox("Enable Tesla Fleet API (cloud)")
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
        self.client_id.setPlaceholderText("from developer.tesla.com")
        self.cloud.toggled.connect(self._cloud_toggled)

        self.poll = PollSpinBox()
        self.poll.setAccessibleName("Poll interval")
        self.poll.setToolTip(
            "Read the car periodically while the window is open (records history). "
            "Polling never wakes a sleeping car."
        )
        self.poll.valueChanged.connect(self._poll_note_update)
        self.poll_note = QLabel("")
        self.poll_note.setWordWrap(True)
        self.poll_note.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.poll_bg = QCheckBox("Keep polling in background (while hidden in the tray)")

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
        tariff_row.addWidget(QLabel("Currency:"))
        tariff_row.addWidget(self.currency)
        tariff_row.addStretch(1)

        self.close_to_tray = QCheckBox("Closing the window keeps SOCKS running in the system tray")

        self.setup_btn = QPushButton("Set &up…")
        self.setup_btn.setToolTip("Run the setup wizard: VIN, key, pairing, cloud")
        self.setup_btn.clicked.connect(self.wizard_requested)
        self.save_btn = QPushButton("Save")
        self.save_btn.setShortcut(QKeySequence.StandardKey.Save)
        self.save_btn.setToolTip("Save settings (Ctrl+S)")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save)
        self.result = QLabel("")
        self.result.setWordWrap(True)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.addRow("&VIN:", self.vin)
        form.addRow(QLabel("Private key:", buddy=self.key_path), key_row)
        form.addRow("", self.key_state)
        form.addRow("&Units:", self.units)
        form.addRow("Co&nnection:", self.prefer)
        form.addRow("", self.cloud)
        form.addRow("", self.price)
        form.addRow("&Region:", self.region)
        form.addRow("Client &ID:", self.client_id)
        form.addRow("&Poll interval:", self.poll)
        form.addRow("", self.poll_note)
        form.addRow("", self.poll_bg)
        form.addRow(QLabel("Tari&ff:", buddy=self.tariff), tariff_row)
        form.addRow("", self.close_to_tray)

        bottom = QHBoxLayout()
        bottom.addWidget(self.setup_btn)
        bottom.addWidget(self.result, 1)
        bottom.addWidget(self.save_btn)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addStretch(1)
        lay.addLayout(bottom)

    # ----------------------------------------------------------- load/save

    def load(self, s: Any, close_to_tray: bool, poll_in_background: bool = False) -> None:
        self._settings = s
        self.vin.setText(getattr(s, "vin", "") or "")
        kp = getattr(s, "key_path", None)
        self.key_path.setText(str(kp) if kp else "")
        self._select(self.units, getattr(s, "units", "metric"))
        self._select(self.prefer, getattr(s, "prefer", "ble"))
        self.cloud.setChecked(bool(getattr(s, "cloud_enabled", False)))
        self._select(self.region, getattr(s, "region", "eu"))
        self.client_id.setText(getattr(s, "client_id", "") or "")
        self.poll.setValue(int(getattr(s, "poll_seconds", 0) or 0))
        self.tariff.setValue(float(getattr(s, "tariff_eur_per_kwh", 0.0) or 0.0))
        self.currency.setText(getattr(s, "currency", "EUR") or "EUR")
        self.close_to_tray.setChecked(close_to_tray)
        self.poll_bg.setChecked(poll_in_background)
        self._cloud_toggled(self.cloud.isChecked())
        self._key_status(self.key_path.text())

    @staticmethod
    def _select(combo: QComboBox, data: str) -> None:
        i = combo.findData(data)
        combo.setCurrentIndex(max(0, i))

    def _save(self) -> None:
        vin = self.vin.text().strip().upper()
        if vin and not VIN_RE.match(vin):
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
        if hasattr(s, "key_path"):
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
            self, "Import existing private key", start, "PEM key (*.pem);;All files (*)"
        )
        if path:
            self.key_path.setText(path)

    def _key_status(self, text: str) -> None:
        p = Path(text.strip() or str(DEFAULT_KEY)).expanduser()
        pal = self.key_state.palette()
        try:
            exists = p.is_file()
            loose = exists and (p.stat().st_mode & 0o077) != 0
        except OSError:
            exists, loose = False, False
        if not exists:
            msg, good = "Key file not found at this path.", False
        elif loose:
            msg, good = "Key file found, but readable by other users (expected mode 0600).", False
        else:
            msg, good = "Key file found. Its contents are never displayed.", True
        pal.setColor(self.key_state.foregroundRole(), ok(pal) if good else warn(pal))
        self.key_state.setPalette(pal)
        self.key_state.setText(msg)

    def _update_poll_minimum(self) -> None:
        self.poll.set_minimum_on(min_poll_seconds(self._settings, self.cloud.isChecked()))
        self._poll_note_update()

    def _poll_note_update(self, *_a: object) -> None:
        lo = self.poll.minimum_on()
        text = f"0 = off; otherwise at least {lo} s. Polling never wakes a sleeping car."
        v = self.poll.value()
        if self.cloud.isChecked():
            text += " With the cloud enabled, a poll that falls back to the cloud is a paid read"
            if v:
                text += f" (up to {86_400 / v * CLOUD_READ_USD:.2f} $ a day at this interval)"
            text += "."
        self.poll_note.setText(text)

    def _cloud_toggled(self, on: bool) -> None:
        self._update_poll_minimum()
        self.region.setEnabled(on)
        self.client_id.setEnabled(on)
        self.prefer.setEnabled(on)

    def focus_default(self) -> None:
        self.vin.setFocus(Qt.FocusReason.OtherFocusReason)
