# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Minimal QPainter chart: a line series or a bar series over time.

No external charting libraries. Colours come from the palette. Hovering shows
the nearest value; the widget also exposes a text summary for screen readers.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import QFileDialog, QMenu, QSizePolicy, QToolTip, QWidget

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


def _decimate(pts: list[QPointF]) -> list[QPointF]:
    """Keep first/min/max/last per pixel column: the drawn line looks the same."""
    out: list[QPointF] = []
    col: list[QPointF] = []
    for pt in pts:
        if col and int(pt.x()) != int(col[0].x()):
            out.extend(_column(col))
            col = []
        col.append(pt)
    out.extend(_column(col))
    return out


def _column(col: list[QPointF]) -> list[QPointF]:
    if len(col) <= 4:
        return col
    lo = min(col, key=QPointF.y)
    hi = max(col, key=QPointF.y)
    mid = [lo, hi] if col.index(lo) < col.index(hi) else [hi, lo]
    return [col[0], *mid, col[-1]]


class Chart(QWidget):
    #: (path, success) after a PNG/SVG export
    saved = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = "line"
        self._pts: list[Point] = []
        self._xs: list[float] = []
        self._xr = (0.0, 1.0)
        self._ymax: float | None = None
        self._top = 1.0
        self._path_cache: tuple[tuple[float, ...], QPainterPath] | None = None
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
        self._top = (
            y_max if y_max is not None else _nice_max(max((p[1] for p in self._pts), default=1.0))
        )
        self._path_cache = None
        if y_fmt is not None:
            self._yfmt = y_fmt
        self._title = title
        if self._pts:
            lo, hi = min(p[1] for p in self._pts), max(p[1] for p in self._pts)
            self.setAccessibleDescription(
                self.tr("{0}: {1} points, from {2} to {3}").format(
                    title, len(self._pts), self._yfmt(lo), self._yfmt(hi)
                )
            )
        else:
            self.setAccessibleDescription(self.tr("{0}: no data").format(title))
        self.setAccessibleName(title)
        self.update()

    # ---------------------------------------------------------------- geometry

    def _plot_rect(self, w: float | None = None, h: float | None = None) -> QRectF:
        w = self.width() if w is None else w
        h = self.height() if h is None else h
        fm = self.fontMetrics()
        left = fm.horizontalAdvance(self._yfmt(self._top)) + 12
        top = fm.height() + 8 if self._title else 8
        return QRectF(
            left,
            top,
            max(10, w - left - 10),
            max(10, h - top - fm.height() - 12),
        )

    def _map(self, r: QRectF, x: float, y: float, top: float | None = None) -> QPointF:
        lo, hi = self._xr
        top = self._top if top is None else top
        return QPointF(
            r.left() + (x - lo) / (hi - lo) * r.width(), r.bottom() - (y / top) * r.height()
        )

    # ------------------------------------------------------------------ paint

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        self._paint(p, self.width(), self.height())
        p.end()

    def save_image(self, path: str, kind: str) -> bool:
        """Render the chart (same QPainter code) to PNG or SVG at the current size."""
        w, h = max(self.width(), 480), max(self.height(), 220)
        bg = self.palette().color(QPalette.ColorRole.Window)
        if kind == "svg":
            from PySide6.QtSvg import QSvgGenerator

            gen = QSvgGenerator()
            gen.setFileName(path)
            gen.setSize(QSize(w, h))
            gen.setViewBox(QRectF(0, 0, w, h))
            gen.setTitle(self._title or "SOCKS chart")
            gen.setDescription("Exported from SOCKS")
            p = QPainter(gen)
            p.fillRect(QRectF(0, 0, w, h), bg)
            self._paint(p, w, h)
            return p.end()
        scale = 2
        img = QImage(w * scale, h * scale, QImage.Format.Format_ARGB32)
        img.fill(bg)
        p = QPainter(img)
        p.scale(scale, scale)
        self._paint(p, w, h)
        p.end()
        return img.save(path, "PNG")

    def _paint(self, p: QPainter, width: float, height: float) -> None:
        pal = self.palette()
        p.setFont(self.font())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = self.fontMetrics()
        if self._title:
            p.setPen(pal.color(QPalette.ColorRole.WindowText))
            p.drawText(
                QRectF(0, 0, width, fm.height() + 4),
                Qt.AlignmentFlag.AlignHCenter,
                self._title,
            )
        r = self._plot_rect(width, height)
        if not self._pts:
            p.setPen(muted(pal))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.tr("No data yet"))
            return

        grid = QColor(pal.color(QPalette.ColorRole.Mid))
        grid.setAlphaF(0.5)
        top = self._top
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
            lo, hi = self._xr
            bw = max(1.0, r.width() * 86400 / (hi - lo) * 0.75)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            for xn, (_x, y) in zip(self._xs, self._pts, strict=True):
                pt = self._map(r, xn, y, top)
                p.drawRect(QRectF(pt.x() + bw * 0.15, pt.y(), bw, r.bottom() - pt.y()))
        else:
            path, fill = self._line_paths(r, top)
            area = QColor(col)
            area.setAlphaF(0.18)
            p.fillPath(fill, area)
            p.setPen(QPen(col, 2))
            p.drawPath(path)

    def _line_paths(self, r: QRectF, top: float) -> tuple[QPainterPath, QPainterPath]:
        """Line and area paths, rebuilt only when the data or the plot rect changes."""
        key = (r.left(), r.top(), r.width(), r.height(), top)
        if self._path_cache is None or self._path_cache[0] != key:
            pts = [
                self._map(r, xn, y, top) for xn, (_x, y) in zip(self._xs, self._pts, strict=True)
            ]
            if len(pts) > 2 * r.width():
                pts = _decimate(pts)
            path = QPainterPath(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            self._path_cache = (key, path)
        path = self._path_cache[1]
        fill = QPainterPath(path)
        fill.lineTo(self._map(r, self._xs[-1], 0, top))
        fill.lineTo(self._map(r, self._xs[0], 0, top))
        fill.closeSubpath()
        return path, fill

    # ----------------------------------------------------------- context menu

    def contextMenuEvent(self, e: QContextMenuEvent) -> None:
        menu = QMenu(self)
        menu.addAction(self.tr("Save as &PNG…"), lambda: self.save_dialog("png"))
        menu.addAction(self.tr("Save as &SVG…"), lambda: self.save_dialog("svg"))
        menu.exec(e.globalPos())

    def save_dialog(self, kind: str) -> None:
        name = (self._title or "chart").split(" (")[0].lower().replace(" ", "-")
        start = str(Path.home() / f"socks-{name}.{kind}")
        flt = "PNG image (*.png)" if kind == "png" else "SVG image (*.svg)"
        path, _f = QFileDialog.getSaveFileName(self, f"Save chart as {kind.upper()}", start, flt)
        if path:
            ok = self.save_image(path, kind)
            self.saved.emit(path, ok)

    # ------------------------------------------------------------------ hover

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if not self._pts:
            return
        best = self._pts[self.nearest_index(e.position().x())]
        label = (
            best[0].strftime("%Y-%m-%d %H:%M")
            if isinstance(best[0], datetime)
            else best[0].isoformat()
        )
        QToolTip.showText(e.globalPosition().toPoint(), f"{label}: {self._yfmt(best[1])}", self)

    def nearest_index(self, px: float) -> int:
        """Index of the point whose x is nearest to widget x ``px`` (binary search)."""
        r = self._plot_rect()
        lo, hi = self._xr
        x = lo + (px - r.left()) / max(r.width(), 1e-9) * (hi - lo)
        i = bisect.bisect_left(self._xs, x)
        if i <= 0:
            return 0
        if i >= len(self._xs):
            return len(self._xs) - 1
        return i if self._xs[i] - x < x - self._xs[i - 1] else i - 1
