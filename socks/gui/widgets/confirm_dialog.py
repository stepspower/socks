# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""The single confirmation gate for every command that changes the car.

Normal commands: one confirm. Dangerous commands (unlock, frunk, trunk, …):
the user must also type the first three letters of the thing affected
(``challenge_for``).
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)


def challenge_for(label: str) -> str:
    """First three letters of the label's last word: Unlock → unl, Open frunk → fru.

    The last word names what is affected, so frunk and trunk get different challenges.
    """
    words = [w for w in re.split(r"[^A-Za-z]+", label) if len(w) >= 3]
    return (words[-1] if words else label)[:3].lower()


class ConfirmDialog(QDialog):
    def __init__(self, parent: QWidget | None, label: str, detail: str, dangerous: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Confirm: {label}")
        self.setModal(True)
        self._challenge = challenge_for(label) if dangerous else ""
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{label}</b>")
        head.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(head)
        if detail:
            d = QLabel(detail)
            d.setWordWrap(True)
            lay.addWidget(d)
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._ok = self._buttons.addButton(label, QDialogButtonBox.ButtonRole.AcceptRole)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        self.edit: QLineEdit | None = None
        if dangerous:
            warn = QLabel(
                f"This action affects the car's security. Type <b>{self._challenge}</b> to confirm."
            )
            warn.setWordWrap(True)
            lay.addWidget(warn)
            self.edit = QLineEdit()
            self.edit.setPlaceholderText(self._challenge)
            self.edit.setAccessibleName("Confirmation letters")
            self.edit.textChanged.connect(self._check)
            lay.addWidget(self.edit)
            self._ok.setEnabled(False)
            self._ok.setAutoDefault(False)
            self.edit.setFocus()
        else:
            self._ok.setDefault(True)
            self._ok.setFocus()
        lay.addWidget(self._buttons)

    def _check(self, text: str) -> None:
        self._ok.setEnabled(text.strip().lower() == self._challenge)

    def accept(self) -> None:
        if self.edit is not None and self.edit.text().strip().lower() != self._challenge:
            return
        super().accept()


def confirm(parent: QWidget | None, label: str, detail: str, dangerous: bool) -> bool:
    dlg = ConfirmDialog(parent, label, detail, dangerous)
    try:
        return dlg.exec() == QDialog.DialogCode.Accepted
    finally:
        dlg.deleteLater()
