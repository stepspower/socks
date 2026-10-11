# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Minimal QPainter chart: a line series or a bar series over time.

No external charting libraries. Colours come from the palette. Hovering shows
the nearest value; the widget also exposes a text summary for screen readers.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import date, datetime

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from socks.gui.widgets.colors import accent, muted

X = datetime | date
Point = tuple[X, float]


def _xnum(x: X) -> float:
    if isinstance(x, datetime):
        return x.timestamp()
    return datetime(x.year, x.month, x.day).timestamp()


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


class Chart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = "line"
        self._pts: list[Point] = []
        self._xs: list[float] = []
        self._xr = (0.0, 1.0)
        self._ymax: float | None = None
        self._yfmt: Callable[[float], str] = lambda v: f"{v:g}"
        self._xfmt: Callable[[X], str] = lambda x: x.strftime("%d %b")
        self._title = ""
        self.setMouseTracking(True)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def sizeHint(self) -> QSize:
        return QSize(480, 220)

    def set_series(
        self,
        kind: str,
        points: Sequence[Point],
        title: str = "",
        y_max: float | None = None,
        y_fmt: Callable[[float], str] | None = None,
    ) -> None:
        if kind not in ("line", "bars"):
            raise ValueError(kind)
        self._kind = kind
        self._pts = sorted(points, key=lambda p: _xnum(p[0]))
        self._xs = [_xnum(p[0]) for p in self._pts]
        if self._xs:
            lo, hi = self._xs[0], self._xs[-1] + (86400 if kind == "bars" else 0)
            self._xr = (lo, hi if hi > lo else lo + 1)
        self._ymax = y_max
        if y_fmt is not None:
            self._yfmt = y_fmt
        self._title = title
        if self._pts:
            lo, hi = min(p[1] for p in self._pts), max(p[1] for p in self._pts)
            self.setAccessibleDescription(
                f"{title}: {len(self._pts)} points, from {self._yfmt(lo)} to {self._yfmt(hi)}"
            )
        else:
            self.setAccessibleDescription(f"{title}: no data")
        self.setAccessibleName(title)
        self.update()

    # ---------------------------------------------------------------- geometry

    def _plot_rect(self) -> QRectF:
        fm = self.fontMetrics()
        left = fm.horizontalAdvance(self._yfmt(self._top())) + 12
        top = fm.height() + 8 if self._title else 8
        return QRectF(
            left,
            top,
            max(10, self.width() - left - 10),
            max(10, self.height() - top - fm.height() - 12),
        )

    def _top(self) -> float:
        if self._ymax is not None:
            return self._ymax
        return _nice_max(max((p[1] for p in self._pts), default=1.0))

    def _xrange(self) -> tuple[float, float]:
        return self._xr

    def _map(self, r: QRectF, x: float, y: float, top: float | None = None) -> QPointF:
        lo, hi = self._xr
        top = self._top() if top is None else top
        return QPointF(
            r.left() + (x - lo) / (hi - lo) * r.width(), r.bottom() - (y / top) * r.height()
        )

    # ------------------------------------------------------------------ paint

    def paintEvent(self, _e: QPaintEvent) -> None:
        pal = self.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = self.fontMetrics()
        if self._title:
            p.setPen(pal.color(QPalette.ColorRole.WindowText))
            p.drawText(
                QRectF(0, 0, self.width(), fm.height() + 4),
                Qt.AlignmentFlag.AlignHCenter,
                self._title,
            )
        r = self._plot_rect()
        if not self._pts:
            p.setPen(muted(pal))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "No data yet")
            p.end()
            return

        grid = QColor(pal.color(QPalette.ColorRole.Mid))
        grid.setAlphaF(0.5)
        top = self._top()
        for i in range(5):
            v = top * i / 4
            y = r.bottom() - r.height() * i / 4
            p.setPen(QPen(grid, 1, Qt.PenStyle.DotLine if i else Qt.PenStyle.SolidLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(muted(pal))
            p.drawText(
                QRectF(0, y - fm.height() / 2, r.left() - 6, fm.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self._yfmt(v),
            )

        n = len(self._pts)
        idx = sorted({0, n // 2, n - 1})
        for k in idx:
            x = self._map(r, _xnum(self._pts[k][0]), 0).x()
            text = self._xfmt(self._pts[k][0])
            w = fm.horizontalAdvance(text)
            x = max(r.left(), min(r.right() - w, x - w / 2))
            p.drawText(QPointF(x, r.bottom() + fm.ascent() + 4), text)

        col = accent(pal)
        if self._kind == "bars":
            lo, hi = self._xrange()
            bw = max(1.0, r.width() * 86400 / (hi - lo) * 0.75)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            for xn, (_x, y) in zip(self._xs, self._pts, strict=True):
                pt = self._map(r, xn, y, top)
                p.drawRect(QRectF(pt.x() + bw * 0.15, pt.y(), bw, r.bottom() - pt.y()))
        else:
            path = QPainterPath()
            for i, (xn, (_x, y)) in enumerate(zip(self._xs, self._pts, strict=True)):
                pt = self._map(r, xn, y, top)
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            fill = QPainterPath(path)
            fill.lineTo(self._map(r, _xnum(self._pts[-1][0]), 0))
            fill.lineTo(self._map(r, _xnum(self._pts[0][0]), 0))
            fill.closeSubpath()
            area = QColor(col)
            area.setAlphaF(0.18)
            p.fillPath(fill, area)
            p.setPen(QPen(col, 2))
            p.drawPath(path)
        p.end()

    # ------------------------------------------------------------------ hover

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if not self._pts:
            return
        r = self._plot_rect()
        pos = e.position()
        i = min(range(len(self._xs)), key=lambda k: abs(self._map(r, self._xs[k], 0).x() - pos.x()))
        best = self._pts[i]
        label = (
            best[0].strftime("%Y-%m-%d %H:%M")
            if isinstance(best[0], datetime)
            else best[0].isoformat()
        )
        QToolTip.showText(e.globalPosition().toPoint(), f"{label}: {self._yfmt(best[1])}", self)
