# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""First-run setup wizard: Car · Key · Pair · Cloud (optional) · Done.

Every action runs through the async bridge when its button is clicked; results
show inline under the button (never a modal error box). The Client Secret lives
only in a password field and is cleared the moment it is used.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from socks.gui import fmt
from socks.gui.async_bridge import AsyncBridge, describe
from socks.gui.setup_services import (
    DEFAULT_IMPORT,
    PAIR_ROLES,
    PRICE_NOTE,
    ROLE_HELP,
    KeyResult,
    import_path_feedback,
    vin_feedback,
)
from socks.gui.widgets.colors import muted, ok, warn
from socks.gui.widgets.qr import encode


def _label(text: str = "", *, wrap: bool = True, rich: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    lab.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


def _tint(lab: QLabel, good: bool | None) -> None:
    pal = lab.palette()
    col = muted(pal) if good is None else (ok(pal) if good else warn(pal))
    pal.setColor(lab.foregroundRole(), col)
    lab.setPalette(pal)


class QrView(QWidget):
    """Paints a QR code dark-on-white with a quiet zone (scanners need the contrast)."""

    QUIET = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._modules: list[list[bool]] = []
        self.setFixedSize(176, 176)

    def set_text(self, text: str) -> None:
        self._modules = encode(text, "M").modules
        self.setAccessibleName("QR code of the pairing link")
        self.setAccessibleDescription(text)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(176, 176)

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 255, 255))
        n = len(self._modules)
        if n:
            cell = min(self.width(), self.height()) / (n + 2 * self.QUIET)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0))
            for y, row in enumerate(self._modules):
                for x, dark in enumerate(row):
                    if dark:
                        rx, ry = (x + self.QUIET) * cell, (y + self.QUIET) * cell
                        p.drawRect(QRectF(rx, ry, cell + 0.3, cell + 0.3))
        p.end()


class Action(QWidget):
    """Button + spinner + inline result. ``run`` executes one I/O coroutine."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.button = QPushButton(text)
        self.spin = QProgressBar()
        self.spin.setRange(0, 0)
        self.spin.setMaximumWidth(90)
        self.spin.setTextVisible(False)
        self.spin.hide()
        self.result = _label()
        self.result.setAccessibleName(f"{text.replace('&', '')} result")
        self.result.hide()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.button)
        row.addWidget(self.spin)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(row)
        lay.addWidget(self.result)

    def show_result(self, text: str, good: bool | None) -> None:
        self.result.setText(text)
        self.result.setVisible(bool(text))
        _tint(self.result, good)

    def run(
        self,
        bridge: AsyncBridge,
        work: Callable[[], Any],
        done: Callable[[Any], tuple[str, bool]],
        busy_text: str = "Working…",
    ) -> None:
        """``work`` returns the awaitable for bridge.io/run_sync; ``done`` maps result → text."""
        if bridge.busy(self):
            return

        async def go() -> None:
            self.button.setEnabled(False)
            self.spin.show()
            self.show_result(busy_text, None)
            try:
                result = await work()
            except Exception as exc:  # noqa: BLE001 - shown inline
                self.show_result(describe(exc), False)
            else:
                text, good = done(result)
                self.show_result(text, good)
            finally:
                self.spin.hide()
                self.button.setEnabled(True)

        bridge.spawn(self, go(), self.button.text().replace("&", ""))


class _Page(QWizardPage):
    def __init__(self, wiz: SetupWizard, title: str, subtitle: str) -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(title)
        self.setSubTitle(subtitle)
        self.lay = QVBoxLayout(self)


# ----------------------------------------------------------------------- pages


class CarPage(_Page):
    def __init__(self, wiz: SetupWizard) -> None:
        super().__init__(wiz, "Your car", "SOCKS needs the car's VIN to talk to it.")
        self.vin = QLineEdit(wiz.vin)
        self.vin.setMaxLength(17)
        self.vin.setPlaceholderText("17 characters, e.g. 5YJ3E7EB…")
        self.vin.setAccessibleName("VIN")
        self.feedback = _label()
        self.vin.textEdited.connect(lambda t: self.vin.setText(t.upper()))
        self.vin.textChanged.connect(self._changed)
        form = QFormLayout()
        form.addRow(QLabel("&VIN:", buddy=self.vin), self.vin)
        form.addRow("", self.feedback)
        self.lay.addLayout(form)
        self.lay.addWidget(
            _label(
                "Find the VIN on the touchscreen under Controls → Software, in the Tesla app "
                "under your car's Specs, or through the windscreen at the bottom left."
            )
        )
        self.lay.addStretch(1)
        self._changed(self.vin.text())

    def _changed(self, text: str) -> None:
        good, msg = vin_feedback(text)
        self.feedback.setText(msg)
        _tint(self.feedback, good if text else None)
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return vin_feedback(self.vin.text())[0]

    def validatePage(self) -> bool:
        self.wiz.vin = self.vin.text().strip().upper()
        return True


class KeyPage(_Page):
    def __init__(self, wiz: SetupWizard) -> None:
        super().__init__(
            wiz,
            "Key",
            "The car only accepts commands signed with a key it knows. "
            "The private key never leaves this computer and is never shown.",
        )
        self.group = QButtonGroup(self)
        self.keep = QRadioButton("")
        self.imp = QRadioButton("&Import an existing key (already paired, e.g. from tesla-control)")
        self.gen = QRadioButton("&Generate a new key (must then be paired with a key card)")
        current = wiz.current_key
        if current is not None:
            self.keep.setText(f"&Keep the current key: {current}")
            self.lay.addWidget(self.keep)
        else:
            self.keep.hide()
        for b in (self.keep, self.imp, self.gen):
            self.group.addButton(b)
        self.path = QLineEdit(str(DEFAULT_IMPORT) if DEFAULT_IMPORT.is_file() else "")
        self.path.setPlaceholderText(str(DEFAULT_IMPORT))
        self.path.setAccessibleName("Key file to import")
        self.browse = QPushButton("&Browse…")
        self.browse.clicked.connect(self._browse)
        self.path_state = _label()
        self.path.textChanged.connect(self._path_changed)
        self.import_action = Action("&Use this key")
        self.import_action.button.clicked.connect(self._import)
        self.gen_action = Action("Ge&nerate key")
        self.gen_action.button.clicked.connect(self._generate)

        imp_box = QWidget()
        il = QVBoxLayout(imp_box)
        il.setContentsMargins(24, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(self.browse)
        il.addLayout(row)
        il.addWidget(self.path_state)
        il.addWidget(self.import_action)
        gen_box = QWidget()
        gl = QVBoxLayout(gen_box)
        gl.setContentsMargins(24, 0, 0, 0)
        gl.addWidget(_label(f"Stored as {wiz.services.key_dest} (mode 0600)."))
        gl.addWidget(self.gen_action)
        self.imp_box, self.gen_box = imp_box, gen_box

        self.lay.addWidget(self.imp)
        self.lay.addWidget(imp_box)
        self.lay.addWidget(self.gen)
        self.lay.addWidget(gen_box)
        self.lay.addStretch(1)
        self.group.buttonToggled.connect(lambda *_a: self._mode_changed())
        (self.keep if current is not None else self.imp).setChecked(True)
        self._path_changed(self.path.text())

    def _mode_changed(self) -> None:
        self.imp_box.setEnabled(self.imp.isChecked())
        self.gen_box.setEnabled(self.gen.isChecked())
        self.completeChanged.emit()

    def _browse(self) -> None:
        start = self.path.text().strip() or str(DEFAULT_IMPORT.parent)
        path, _f = QFileDialog.getOpenFileName(
            self, "Import existing private key", start, "PEM key (*.pem);;All files (*)"
        )
        if path:
            self.path.setText(path)

    def _path_changed(self, text: str) -> None:
        good, msg = import_path_feedback(text)
        self.path_state.setText(msg)
        _tint(self.path_state, good if text.strip() else None)
        self.import_action.button.setEnabled(good)

    def _key_done(self, res: KeyResult) -> tuple[str, bool]:
        self.wiz.key_result = res
        self.completeChanged.emit()
        verb = "Generated" if res.created else "Using"
        return f"{verb} key {res.path}\nFingerprint {res.fingerprint}", True

    def _import(self) -> None:
        src = Path(self.path.text().strip()).expanduser()
        s = self.wiz.services
        self.import_action.run(
            self.wiz.bridge, lambda: self.wiz.bridge.run_sync(s.import_key, src), self._key_done
        )

    def _generate(self) -> None:
        s = self.wiz.services
        self.gen_action.run(
            self.wiz.bridge, lambda: self.wiz.bridge.run_sync(s.generate_key), self._key_done
        )

    def isComplete(self) -> bool:
        if self.keep.isChecked():
            return True
        return self.wiz.key_result is not None

    def validatePage(self) -> bool:
        if self.keep.isChecked() and self.wiz.current_key is not None:
            self.wiz.key_path = self.wiz.current_key
        elif self.wiz.key_result is not None:
            self.wiz.key_path = self.wiz.key_result.path
        return True


class PairPage(_Page):
    def __init__(self, wiz: SetupWizard) -> None:
        super().__init__(
            wiz, "Pair with the car", "Optional if the key is already paired. Uses Bluetooth only."
        )
        self.role = QComboBox()
        for r in PAIR_ROLES:
            self.role.addItem(r.capitalize(), r)
        self.role_help = _label(ROLE_HELP["driver"])
        self.role.currentIndexChanged.connect(
            lambda _i: self.role_help.setText(ROLE_HELP[self.role.currentData()])
        )
        form = QFormLayout()
        form.addRow(QLabel("&Role:", buddy=self.role), self.role)
        form.addRow("", self.role_help)
        self.lay.addLayout(form)
        self.lay.addWidget(
            _label(
                "Sit in the car with this laptop. After sending the request, tap your key card "
                "on the centre console when the screen asks, and confirm on the screen."
            )
        )
        self.pair_action = Action("&Send pairing request")
        self.pair_action.button.clicked.connect(self._pair)
        self.test_action = Action("&Test connection")
        self.test_action.button.clicked.connect(self._test)
        self.lay.addWidget(self.pair_action)
        self.lay.addWidget(self.test_action)
        self.lay.addStretch(1)

    def _pair(self) -> None:
        w, role = self.wiz, self.role.currentData()

        def done(paired: bool) -> tuple[str, bool]:
            if paired:
                return f"Paired: the car accepted the key as {role}.", True
            return (
                "No confirmation yet. Approval may still be pending: check the car's Locks "
                "screen, then use Test connection.",
                False,
            )

        self.pair_action.run(
            w.bridge,
            lambda: w.bridge.io(w.services.pair(w.vin, w.key_path, role)),
            done,
            "Waiting for the key card tap (up to a minute)…",
        )

    def _test(self) -> None:
        w = self.wiz

        def done(s: Any) -> tuple[str, bool]:
            soc = "–" if s.soc is None else f"{s.soc} %"
            return f"Connected: {soc}, range {fmt.dist(s.rated_range_km, w.units)}.", True

        self.test_action.run(
            w.bridge,
            lambda: w.bridge.io(w.services.test_connection(w.vin, w.key_path)),
            done,
            "Connecting over Bluetooth (wakes the car if needed)…",
        )


class CloudPage(_Page):
    def __init__(self, wiz: SetupWizard) -> None:
        super().__init__(
            wiz,
            "Cloud access (optional)",
            "Remote access through Tesla's Fleet API. Skip this page to use Bluetooth only.",
        )
        self.enable = QCheckBox("Set up &cloud access now")
        self.enable.setChecked(wiz.cloud_enabled)
        self.lay.addWidget(self.enable)
        self.box = QWidget()
        bl = QVBoxLayout(self.box)
        bl.setContentsMargins(24, 0, 0, 0)
        bl.addWidget(_label(PRICE_NOTE))
        self.client_id = QLineEdit(wiz.client_id)
        self.client_id.setPlaceholderText("from developer.tesla.com")
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText("used once, never stored")
        self.secret.setAccessibleName("Client secret")
        form = QFormLayout()
        form.addRow(QLabel("Client &ID:", buddy=self.client_id), self.client_id)
        form.addRow(QLabel("Client s&ecret:", buddy=self.secret), self.secret)
        bl.addLayout(form)
        self.register_action = Action("Check key && &register domain")
        self.register_action.button.clicked.connect(self._register)
        self.login_action = Action("&Log in with Tesla")
        self.login_action.button.clicked.connect(self._login)
        bl.addWidget(self.register_action)
        bl.addWidget(self.login_action)

        link = wiz.services.pairing_link()
        self.link = _label(link, wrap=False)
        copy = QPushButton("Cop&y link")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(link))
        self.qr = QrView()
        self.qr.set_text(link)
        link_col = QVBoxLayout()
        link_col.addWidget(_label("Then add the virtual key: open this link on your phone."))
        link_col.addWidget(self.link)
        link_col.addWidget(copy, 0, Qt.AlignmentFlag.AlignLeft)
        link_col.addStretch(1)
        link_row = QHBoxLayout()
        link_row.addLayout(link_col, 1)
        link_row.addWidget(self.qr)
        bl.addLayout(link_row)
        self.lay.addWidget(self.box)
        self.lay.addStretch(1)
        self.box.setVisible(self.enable.isChecked())
        self.enable.toggled.connect(self.box.setVisible)
        self.box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def _fleet_settings(self) -> Any:
        s = self.wiz.settings
        cid = self.client_id.text().strip()
        if dataclasses.is_dataclass(s) and not isinstance(s, type):
            return dataclasses.replace(s, client_id=cid)
        return SimpleNamespace(client_id=cid, region=getattr(s, "region", "eu"))

    def _take_secret(self) -> str:
        """Read and immediately clear the secret field (memory only, single use)."""
        secret = self.secret.text()
        self.secret.clear()
        return secret

    def _register(self) -> None:
        w, svc = self.wiz, self.wiz.services
        if not self.client_id.text().strip():
            self.register_action.show_result("Enter the Client ID first.", False)
            return
        secret = self._take_secret()
        if not secret:
            self.register_action.show_result("Registration needs the Client Secret.", False)
            return
        settings = self._fleet_settings()

        async def work() -> str:
            check = await w.bridge.io(svc.check_key())
            if not check.ok:
                raise RuntimeError(f"public key check failed: {check.reason} ({check.url})")
            self.register_action.show_result(f"Key check OK: {check.reason}. Registering…", None)
            await w.bridge.io(svc.register(settings, secret))
            return check.reason

        self.register_action.run(
            w.bridge,
            work,
            lambda reason: (f"Domain {svc.domain} registered (key check: {reason}).", True),
            "Checking the public key on the domain…",
        )

    def _login(self) -> None:
        w, svc = self.wiz, self.wiz.services
        if not self.client_id.text().strip():
            self.login_action.show_result("Enter the Client ID first.", False)
            return
        secret = self._take_secret() or None
        settings = self._fleet_settings()

        async def work() -> Any:
            await w.bridge.io(svc.login(settings, secret))
            return await w.bridge.io(svc.token_status(settings))

        def done(st: Any) -> tuple[str, bool]:
            if not getattr(st, "logged_in", False):
                return "Not logged in.", False
            self.wiz.logged_in = True
            scopes = ", ".join(getattr(st, "scopes", ()) or ()) or "–"
            return f"Logged in (region {getattr(st, 'region', '?')}; scopes {scopes}).", True

        self.login_action.run(
            w.bridge, work, done, "Waiting for the browser login (complete it in the browser)…"
        )

    def validatePage(self) -> bool:
        self.wiz.cloud_enabled = self.enable.isChecked()
        self.wiz.client_id = self.client_id.text().strip()
        self.secret.clear()
        return True


class DonePage(_Page):
    def __init__(self, wiz: SetupWizard) -> None:
        super().__init__(wiz, "Done", "SOCKS is ready.")
        self.summary = _label()
        self.lay.addWidget(self.summary)
        self.lay.addStretch(1)

    def initializePage(self) -> None:
        w = self.wiz
        lines = [
            f"VIN: {w.vin}",
            f"Key: {w.key_path}",
            "Cloud: "
            + ("enabled" + (", logged in" if w.logged_in else "") if w.cloud_enabled else "off"),
            "Polling stays off; turn it on in Settings if you want history recorded automatically.",
        ]
        self.summary.setText("\n".join(lines))


# ---------------------------------------------------------------------- wizard


class SetupWizard(QWizard):
    #: emitted after Finish with the wizard (fields vin, key_path, cloud_enabled, client_id)
    applied = Signal(object)

    def __init__(
        self, parent: QWidget | None, bridge: AsyncBridge, services: Any, settings: Any, units: str
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set up")
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.bridge, self.services, self.settings, self.units = bridge, services, settings, units
        self.vin: str = getattr(settings, "vin", "") or ""
        kp = getattr(settings, "key_path", None)
        self.current_key: Path | None = Path(kp) if kp and Path(kp).is_file() else None
        self.key_path: Path = self.current_key or services.key_dest
        self.key_result: KeyResult | None = None
        self.cloud_enabled = bool(getattr(settings, "cloud_enabled", False))
        self.client_id: str = getattr(settings, "client_id", "") or ""
        self.logged_in = False
        for page in (CarPage, KeyPage, PairPage, CloudPage, DonePage):
            self.addPage(page(self))
        self.setOption(QWizard.WizardOption.HaveCustomButton1, True)
        self.setButtonText(QWizard.WizardButton.CustomButton1, "S&kip setup")
        self.setButtonText(QWizard.WizardButton.FinishButton, "&Open SOCKS")
        self.customButtonClicked.connect(lambda _w: self.reject())
        self.resize(720, 560)

    def accept(self) -> None:
        self.applied.emit(self)
        super().accept()

    def done(self, result: int) -> None:
        # Cancel anything still running (pairing, login) when the wizard closes.
        for page_id in self.pageIds():
            for action in self.page(page_id).findChildren(Action):
                self.bridge.cancel_owner(action)
        super().done(result)


def needs_setup(settings: Any) -> bool:
    kp = getattr(settings, "key_path", None)
    return not getattr(settings, "vin", "") or not (kp and Path(kp).is_file())
