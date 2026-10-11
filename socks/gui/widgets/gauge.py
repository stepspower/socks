# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Big state-of-charge gauge (QPainter, palette-derived colours)."""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from socks.gui.widgets.colors import accent, warn

START_DEG = 225.0
SPAN_DEG = 270.0


class Gauge(QWidget):
    #: the charge-limit tick was clicked
    limit_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value: int | None = None
        self._limit: int | None = None
        self._caption = ""
        self._charging = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(160, 160)
        self.setAccessibleName(self.tr("State of charge"))
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:
        return QSize(260, 260)

    def set_state(self, soc: int | None, limit: int | None, caption: str, charging: bool) -> None:
        self._value, self._limit, self._caption, self._charging = soc, limit, caption, charging
        desc = "unknown" if soc is None else self.tr("{0} percent").format(soc)
        if limit is not None:
            desc += self.tr(", limit {0} percent").format(limit)
        self.setAccessibleDescription(f"{desc}. {caption}")
        self.update()

    def tick_hit(self, x: float, y: float) -> bool:
        """True if (x, y) is on the ring within ~12° of the charge-limit tick."""
        if self._limit is None:
            return False
        side = min(self.width(), self.height()) - 8
        r = side / 2
        dx, dy = x - self.width() / 2, self.height() / 2 - y
        dist = math.hypot(dx, dy)
        if not (0.6 * r <= dist <= 1.15 * r):
            return False
        ang = math.degrees(math.atan2(dy, dx))
        tick = START_DEG - SPAN_DEG * max(0, min(100, self._limit)) / 100
        diff = (ang - tick + 180) % 360 - 180
        return abs(diff) <= 12

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        hit = self.tick_hit(e.position().x(), e.position().y())
        self.setCursor(Qt.CursorShape.PointingHandCursor if hit else Qt.CursorShape.ArrowCursor)
        self.setToolTip(self.tr("Click to change the charge limit") if hit else "")
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self.tick_hit(e.position().x(), e.position().y()):
            self.limit_clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e: QPaintEvent) -> None:
        pal = self.palette()
        side = min(self.width(), self.height()) - 8
        if side <= 0:
            return
        thick = max(6.0, side * 0.075)
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side).adjusted(
            thick / 2, thick / 2, -thick / 2, -thick / 2
        )
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        track = QColor(pal.color(QPalette.ColorRole.WindowText))
        track.setAlphaF(0.13)  # visible on light and dark themes alike
        pen = QPen(track, thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, int(START_DEG * 16), int(-SPAN_DEG * 16))

        if self._value is not None:
            v = max(0, min(100, self._value))
            col = warn(pal) if v <= 20 else accent(pal)
            pen.setColor(col)
            p.setPen(pen)
            p.drawArc(rect, int(START_DEG * 16), int(-SPAN_DEG * v / 100 * 16))

        if self._limit is not None:
            ang = START_DEG - SPAN_DEG * max(0, min(100, self._limit)) / 100
            p.save()
            p.translate(rect.center())
            p.rotate(-ang)
            r = rect.width() / 2
            p.setPen(QPen(pal.color(QPalette.ColorRole.WindowText), max(2.0, thick / 4)))
            p.drawLine(int(r - thick * 0.8), 0, int(r + thick * 0.8), 0)
            p.restore()

        p.setPen(pal.color(QPalette.ColorRole.WindowText))
        big = QFont(self.font())
        big.setPixelSize(max(12, int(side * 0.22)))
        big.setWeight(QFont.Weight.DemiBold)
        p.setFont(big)
        text = "–" if self._value is None else f"{self._value}%"
        top = rect.adjusted(0, 0, 0, -rect.height() * 0.12)
        p.drawText(top, Qt.AlignmentFlag.AlignCenter, text)

        small = QFont(self.font())
        small.setPixelSize(max(9, int(side * 0.065)))
        p.setFont(small)
        cap = rect.adjusted(0, rect.height() * 0.30, 0, 0)
        p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
        p.drawText(cap, Qt.AlignmentFlag.AlignCenter, self._caption)
        p.end()
