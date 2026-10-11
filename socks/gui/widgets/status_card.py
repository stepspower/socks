# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Small titled value card and a source/age chip."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from socks.gui.widgets.colors import accent, muted, ok, warn

SIGNAL_BARS = {"strong": 3, "ok": 2, "weak": 1}


def card_color(pal: QPalette) -> QColor:
    """Flat card fill: the window colour nudged toward the text colour (works in dark mode)."""
    base, text = pal.color(QPalette.ColorRole.Window), pal.color(QPalette.ColorRole.WindowText)
    t = 0.05
    return QColor.fromRgbF(
        base.redF() * (1 - t) + text.redF() * t,
        base.greenF() * (1 - t) + text.greenF() * t,
        base.blueF() * (1 - t) + text.blueF() * t,
    )


def paint_card(w: QWidget) -> None:
    p = QPainter(w)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(card_color(w.palette()))
    p.drawRoundedRect(QRectF(w.rect()), 8, 8)
    p.end()


def _muted(label: QLabel) -> None:
    pal = label.palette()
    pal.setColor(QPalette.ColorRole.WindowText, muted(label.palette()))
    label.setPalette(pal)


class StatusCard(QFrame):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)  # flat card, painted in paintEvent
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(2)
        self._title = QLabel(title)
        f = QFont(self.font())
        f.setPointSizeF(f.pointSizeF() * 0.9)
        self._title.setFont(f)
        _muted(self._title)
        self._value = QLabel("–")
        vf = QFont(self.font())
        vf.setPointSizeF(vf.pointSizeF() * 1.35)
        vf.setWeight(QFont.Weight.DemiBold)
        self._value.setFont(vf)
        self._value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setMinimumHeight(self._detail.fontMetrics().height())
        _muted(self._detail)
        for w in (self._title, self._value, self._detail):
            lay.addWidget(w)
        self.setAccessibleName(title)

    def paintEvent(self, _e: QPaintEvent) -> None:
        paint_card(self)

    def add_row(self, *widgets: QWidget) -> QWidget:
        """Append a row of (interactive) widgets under the value; returns its container."""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(12)
        for w in widgets:
            lay.addWidget(w)
        lay.addStretch(1)
        self.layout().addWidget(row)
        return row

    def value_text(self) -> str:
        return self._value.text()

    def set_title(self, title: str) -> None:
        self._title.setText(title)
        self.setAccessibleName(title)

    def set(self, value: str, detail: str = "") -> None:
        self._value.setText(value)
        self._detail.setText(detail)
        self.setAccessibleDescription(f"{value}. {detail}".strip())

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.PaletteChange:
            _muted(self._title)
            _muted(self._detail)
        super().changeEvent(e)


class Section(QFrame):
    """Flat card with a muted section title; put content into ``body``."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)
        self.title = QLabel(title)
        f = QFont(self.font())
        f.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(f)
        _muted(self.title)
        lay.addWidget(self.title)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        lay.addLayout(self.body)
        self.setAccessibleName(title)

    def paintEvent(self, _e: QPaintEvent) -> None:
        paint_card(self)

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.PaletteChange:
            _muted(self.title)
        super().changeEvent(e)


class Chip(QWidget):
    """Rounded pill (e.g. "BLE · 2 min ago"), painted from the palette."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._level = "normal"
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        return self._text

    def set_chip(self, text: str, level: str = "normal", signal: str | None = None) -> None:
        """``signal``: "strong" | "ok" | "weak" draws a 3-bar glyph before the text."""
        self._text, self._level = text, level
        self._signal = signal if signal in SIGNAL_BARS else None
        self.updateGeometry()
        self.update()

    def signal(self) -> str | None:
        return getattr(self, "_signal", None)

    def _glyph_w(self) -> int:
        return 16 if self.signal() else 0

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._text) + 20 + self._glyph_w(), fm.height() + 6)

    def paintEvent(self, _e: QPaintEvent) -> None:
        pal = self.palette()
        if self._level == "muted":
            bg = pal.color(QPalette.ColorRole.Mid)
            fg = pal.color(QPalette.ColorRole.WindowText)
        else:
            bg = warn(pal) if self._level == "warn" else accent(pal)
            fg = pal.color(QPalette.ColorRole.HighlightedText)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        sig = self.signal()
        if sig:
            # 3 rising bars; filled ones coloured by quality, the rest faint
            filled = SIGNAL_BARS[sig]
            col = {"strong": ok(pal), "ok": QColor(fg), "weak": warn(pal)}[sig]
            if sig != "ok":  # keep contrast on the accent pill
                col = col.lighter(150) if bg.lightness() < 128 else col.darker(110)
            faint = QColor(fg)
            faint.setAlphaF(0.35)
            h = r.height() - 8
            for i in range(3):
                bh = h * (i + 1) / 3
                x = r.left() + 9 + i * 4
                p.setBrush(col if i < filled else faint)
                p.drawRect(QRectF(x, r.bottom() - 4 - bh, 3, bh))
            r = r.adjusted(self._glyph_w(), 0, 0, 0)
        p.setPen(fg)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()
