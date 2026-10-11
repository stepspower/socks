# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Poll-interval spin box: 0 (= off) or at least a minimum (audit M2)."""

from __future__ import annotations

from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QSpinBox, QWidget

MAX_POLL_SECONDS = 86_400


class PollSpinBox(QSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min = 60
        self.setRange(0, MAX_POLL_SECONDS)
        self.setSingleStep(60)
        self.setSuffix(" s")
        self.setSpecialValueText(self.tr("Off"))

    def minimum_on(self) -> int:
        return self._min

    def set_minimum_on(self, seconds: int) -> None:
        """Smallest allowed non-zero interval; a current value below it is raised."""
        self._min = max(1, int(seconds))
        if 0 < self.value() < self._min:
            self.setValue(self._min)

    def allowed(self, value: int) -> int:
        """Clamp ``value`` to 0 or [minimum, MAX]."""
        if value <= 0:
            return 0
        return min(MAX_POLL_SECONDS, max(self._min, value))

    def stepBy(self, steps: int) -> None:
        v = self.value()
        if v == 0 and steps > 0:
            self.setValue(self._min)
        elif v <= self._min and steps < 0:
            self.setValue(0)
        else:
            self.setValue(self.allowed(v + steps * self.singleStep()))

    def validate(self, text: str, pos: int) -> object:
        state, text, pos = super().validate(text, pos)
        if state == QValidator.State.Acceptable:
            v = self.valueFromText(text)
            if 0 < v < self._min:
                state = QValidator.State.Intermediate
        return state, text, pos

    def fixup(self, text: str) -> str:
        try:
            v = self.valueFromText(text)
        except ValueError:
            return text
        return self.prefix() + self.textFromValue(self.allowed(v)) + self.suffix()
