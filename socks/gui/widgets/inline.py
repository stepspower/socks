# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Click-to-change widgets for the Status tab.

``EditableValue`` looks like text, gets an underline + pencil on hover/focus and the
tooltip "Click to change"; Enter/Space activates it. ``InlineEdit`` swaps it for an
editor (spin box, slider or time) with ✓ / ✗; Enter confirms, Esc cancels.
``ToggleChip`` shows a state and requests the opposite command on click. None of these
send anything themselves: they emit, and the window runs its confirm gate.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTime, Signal
from PySide6.QtGui import QEnterEvent, QFocusEvent, QKeyEvent, QMouseEvent, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QTimeEdit,
    QToolButton,
    QWidget,
)

TIP = "Click to change"


class EditableValue(QLabel):
    activated = Signal()

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(TIP)
        self.setForegroundRole(QPalette.ColorRole.Link)  # reads as clickable, theme colour
        self._plain = text

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        self._plain = text
        self._decorate(self.underMouse() or self.hasFocus())

    def _decorate(self, hot: bool) -> None:
        f = self.font()
        f.setUnderline(hot)
        super().setFont(f)
        super().setText(f"{self._plain} ✎" if hot else self._plain)
        self.setAccessibleName(f"{self._plain}. {TIP}")

    def enterEvent(self, e: QEnterEvent) -> None:
        self._decorate(True)
        super().enterEvent(e)

    def leaveEvent(self, e: QEvent) -> None:
        self._decorate(self.hasFocus())
        super().leaveEvent(e)

    def focusInEvent(self, e: QFocusEvent) -> None:
        self._decorate(True)
        super().focusInEvent(e)

    def focusOutEvent(self, e: QFocusEvent) -> None:
        self._decorate(self.underMouse())
        super().focusOutEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.activated.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit()
            return
        super().keyPressEvent(e)


class ToggleChip(EditableValue):
    """A state shown as text; clicking requests the opposite (tooltip says which)."""

    def set_state(self, text: str, action: str) -> None:
        self.setText(text)
        self.setToolTip(f"Click to {action}")


class _Keys(QObject):
    def __init__(self, ok: Callable[[], None], cancel: Callable[[], None]) -> None:
        super().__init__()
        self.ok, self.cancel = ok, cancel

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.KeyPress:
            key = ev.key()  # type: ignore[attr-defined]
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.ok()
                return True
            if key == Qt.Key.Key_Escape:
                self.cancel()
                return True
        return False


class InlineEdit(QWidget):
    """Display value that turns into an editor in place. ``committed`` carries the value
    in display units (number), minutes after midnight (time) or None (time "off")."""

    committed = Signal(object)

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if kind not in ("spin", "slider", "time"):
            raise ValueError(kind)
        self.kind = kind
        self.display = EditableValue()
        self.display.activated.connect(self.start)
        self.editor: Any
        self.off: QCheckBox | None = None
        if kind == "spin":
            self.editor = QDoubleSpinBox()
        elif kind == "slider":
            self.editor = QSlider(Qt.Orientation.Horizontal)
            self.editor.setMinimumWidth(140)
            self.value_label = QLabel()
            self.editor.valueChanged.connect(lambda v: self.value_label.setText(f"{v} %"))
        else:
            self.editor = QTimeEdit()
            self.editor.setDisplayFormat("HH:mm")
            self.off = QCheckBox("Off")
            self.off.toggled.connect(lambda on: self.editor.setEnabled(not on))
        self.ok = QToolButton()
        self.ok.setText("✓")
        self.ok.setToolTip("Apply (Enter)")
        self.ok.clicked.connect(self._commit)
        self.cancel = QToolButton()
        self.cancel.setText("✗")
        self.cancel.setToolTip("Cancel (Esc)")
        self.cancel.clicked.connect(self.stop)
        self._keys = _Keys(self._commit, self.stop)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.display)
        self._edit_widgets: list[QWidget] = [self.editor]
        if kind == "slider":
            self._edit_widgets.append(self.value_label)
        if self.off is not None:
            self._edit_widgets.append(self.off)
        self._edit_widgets += [self.ok, self.cancel]
        for w in self._edit_widgets:
            lay.addWidget(w)
            w.installEventFilter(self._keys)
            w.hide()
        lay.addStretch(1)
        self._current: Any = None
        self._editing = False

    def configure(self, lo: float, hi: float, step: float, suffix: str = "") -> None:
        if self.kind == "spin":
            self.editor.setDecimals(1 if step < 1 else 0)
            self.editor.setRange(lo, hi)
            self.editor.setSingleStep(step)
            self.editor.setSuffix(f" {suffix}" if suffix else "")
        elif self.kind == "slider":
            self.editor.setRange(int(lo), int(hi))
            self.editor.setSingleStep(int(step) or 1)
            self.editor.setPageStep(5)

    def set_value(self, text: str, current: Any) -> None:
        self.display.setText(text)
        self._current = current

    def editing(self) -> bool:
        return self._editing

    def start(self) -> None:
        cur = self._current
        if self.kind == "time":
            self.off.setChecked(cur is None)
            m = 0 if cur is None else int(cur)
            self.editor.setTime(QTime(m // 60, m % 60))
        elif cur is not None:
            self.editor.setValue(cur)
        self._editing = True
        self.display.hide()
        for w in self._edit_widgets:
            w.show()
        self.editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def stop(self) -> None:
        self._editing = False
        for w in self._edit_widgets:
            w.hide()
        self.display.show()
        self.display.setFocus(Qt.FocusReason.OtherFocusReason)

    def value(self) -> Any:
        if self.kind == "time":
            if self.off is not None and self.off.isChecked():
                return None
            t = self.editor.time()
            return t.hour() * 60 + t.minute()
        return self.editor.value()

    def _commit(self) -> None:
        value = self.value()
        self.stop()
        self.committed.emit(value)
