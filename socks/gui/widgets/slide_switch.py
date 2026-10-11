# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Slide switch: a safe direction is a click, a risky direction needs a full drag.

Keyboard: Enter/Space flips a safe direction at once; a risky direction needs the key
held for ~1 s (the knob travels while held). ``toggled(bool)`` is emitted only after
the gesture completes, with the *requested* state. The knob then shows that state as
pending (dimmed) until ``set_checked`` reports the car's real state, or ``revert()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QElapsedTimer, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import QSizePolicy, QWidget

from socks.gui.widgets.colors import accent, warn

RISKY_DRAG = 0.95  # fraction of the travel a risky drag must reach
SAFE_DRAG = 0.5
HOLD_MS = 1000
CLICK_SLOP = 0.06  # movement (fraction) still counted as a click


@dataclass
class SlideLogic:
    """Pure gesture logic. ``pos`` is 0.0 (off end) .. 1.0 (on end)."""

    checked: bool
    risky_on: bool = False  # turning ON needs the full gesture
    risky_off: bool = False  # turning OFF needs the full gesture
    one_way: bool = False  # only off → on exists (e.g. open frunk)
    pos: float = field(init=False)
    _start: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.pos = 1.0 if self.checked else 0.0

    @property
    def target(self) -> bool:
        return not self.checked

    def allowed(self) -> bool:
        return not (self.one_way and self.checked)

    def risky(self) -> bool:
        return self.risky_on if self.target else self.risky_off

    def progress(self) -> float:
        """How far the knob has travelled toward the target end (0..1)."""
        return self.pos if self.target else 1.0 - self.pos

    def begin(self, frac: float) -> None:
        self._start = frac

    def drag(self, frac: float) -> None:
        if self.allowed():
            self.pos = min(1.0, max(0.0, frac))

    def release(self, frac: float) -> bool | None:
        """End of a mouse gesture: the requested state, or None (snap back)."""
        if not self.allowed():
            return None
        clicked = abs(frac - self._start) <= CLICK_SLOP
        need = RISKY_DRAG if self.risky() else SAFE_DRAG
        done = self.progress() >= need or (clicked and not self.risky())
        self.pos = 1.0 if self.checked else 0.0
        return self.target if done else None

    def hold(self, ms: int) -> bool | None:
        """Keyboard: key held for ``ms``; risky needs HOLD_MS, safe completes at once."""
        if not self.allowed():
            return None
        if not self.risky() or ms >= HOLD_MS:
            return self.target
        frac = ms / HOLD_MS
        self.pos = frac if self.target else 1.0 - frac
        return None

    def reset(self, checked: bool) -> None:
        self.checked = checked
        self.pos = 1.0 if checked else 0.0


class SlideSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(
        self,
        name: str,
        on_text: str,
        off_text: str,
        *,
        risky_on: bool = False,
        risky_off: bool = False,
        one_way: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.logic = SlideLogic(False, risky_on, risky_off, one_way)
        self.on_text, self.off_text, self.name = on_text, off_text, name
        self._known = False
        self._pending: bool | None = None
        self._pressed = False
        self._hold = QElapsedTimer()
        self._hold_timer = QTimer(self)
        self._hold_timer.setInterval(30)
        self._hold_timer.timeout.connect(self._hold_tick)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(name)
        self._describe()

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        w = max(fm.horizontalAdvance(self.on_text), fm.horizontalAdvance(self.off_text))
        return QSize(max(120, w + 48), 32)

    # ------------------------------------------------------------- state

    def set_checked(self, checked: bool | None) -> None:
        """The car's real state (None = unknown: disabled)."""
        self._pending = None
        self._known = checked is not None
        self.logic.reset(bool(checked))
        self.setEnabled(self._known and self.logic.allowed())
        self._describe()
        self.update()

    def revert(self) -> None:
        self._pending = None
        self.logic.reset(self.logic.checked)
        self.update()

    def pending(self) -> bool | None:
        return self._pending

    def _describe(self) -> None:
        state = self.on_text if self.logic.checked else self.off_text
        if not self._known:
            state = "unknown"
        how = "hold Enter or slide fully" if self.logic.risky() else "click or press Space"
        verb = self.on_text if self.logic.target else self.off_text
        self.setAccessibleDescription(
            self.tr("Switch, {0}. To change to {1}: {2}.").format(state, verb, how)
        )
        self.setToolTip(
            self.tr("Slide all the way to change to “{0}”").format(verb)
            if self.logic.risky()
            else self.tr("Click to change to “{0}”").format(verb)
        )

    def _emit(self, value: bool) -> None:
        self._pending = value
        self.logic.pos = 1.0 if value else 0.0
        self.update()
        self.toggled.emit(value)

    # ------------------------------------------------------------- mouse

    def _frac(self, x: float) -> float:
        knob = self.height() - 6
        travel = max(1.0, self.width() - knob - 6)
        return (x - 3 - knob / 2) / travel

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._pending is None:
            self._pressed = True
            self.logic.begin(self._frac(e.position().x()))

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._pressed:
            self.logic.drag(self._frac(e.position().x()))
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if not self._pressed:
            return
        self._pressed = False
        result = self.logic.release(self._frac(e.position().x()))
        self.update()
        if result is not None:
            self._emit(result)

    # ---------------------------------------------------------- keyboard

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not e.isAutoRepeat() and self._pending is None and self.logic.allowed():
                if not self.logic.risky():
                    result = self.logic.hold(0)
                    if result is not None:
                        self._emit(result)
                else:
                    self._hold.start()
                    self._hold_timer.start()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not e.isAutoRepeat() and self._hold_timer.isActive():
                self._hold_timer.stop()
                self.logic.reset(self.logic.checked)  # let go too early: snap back
                self.update()
            return
        super().keyReleaseEvent(e)

    def _hold_tick(self) -> None:
        result = self.logic.hold(int(self._hold.elapsed()))
        self.update()
        if result is not None:
            self._hold_timer.stop()
            self._emit(result)

    # ------------------------------------------------------------ paint

    def paintEvent(self, _e: QPaintEvent) -> None:
        pal = self.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on_col = accent(pal)
        moving_risky = self.logic.risky() and 0.0 < self.logic.progress() < 1.0
        if self.logic.pos >= 0.5:
            track = QColor(on_col)
        else:  # "off" track: text colour at low alpha reads on light and dark themes
            track = QColor(pal.color(QPalette.ColorRole.WindowText))
            track.setAlphaF(0.16)
        if moving_risky or (self._pending is not None and self.logic.risky()):
            track = warn(pal)
        if not self.isEnabled() or self._pending is not None:
            track.setAlphaF(track.alphaF() * 0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        if self.hasFocus():
            p.setPen(pal.color(QPalette.ColorRole.Highlight))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-0.5, -0.5, 0.5, 0.5), r.height() / 2, r.height() / 2)
        knob = r.height() - 4
        x = r.left() + 2 + self.logic.pos * (r.width() - knob - 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(pal.color(QPalette.ColorRole.Base))
        p.drawEllipse(QRectF(x, r.top() + 2, knob, knob))
        text = self.on_text if self.logic.pos >= 0.5 else self.off_text
        if not self._known:
            text = "?"
        fg = (
            pal.color(QPalette.ColorRole.HighlightedText)
            if self.logic.pos >= 0.5
            else pal.color(QPalette.ColorRole.WindowText)
        )
        p.setPen(fg)
        text_r = (
            QRectF(r.left() + 8, r.top(), r.width() - knob - 14, r.height())
            if self.logic.pos >= 0.5
            else QRectF(r.left() + knob + 8, r.top(), r.width() - knob - 14, r.height())
        )
        p.drawText(text_r, Qt.AlignmentFlag.AlignCenter, text)
        p.end()
