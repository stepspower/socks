# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""History tab: summary cards, SoC / km charts, charge sessions, battery health.

Rendering is driven by the field names of the dataclasses returned by
``socks.history.stats`` so it keeps working as that module grows fields.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from socks.gui import fmt
from socks.gui.widgets.chart import Chart
from socks.gui.widgets.status_card import StatusCard

PERIODS: tuple[tuple[str, int | None], ...] = (
    ("Last 7 days", 7),
    ("Last 30 days", 30),
    ("Last 90 days", 90),
    ("Last year", 365),
    ("All time", None),
)
CARD_KEYS = (("km",), ("kwh",), ("cost",), ("wh_per_km",))
SKIP_COLS = {"id", "vin", "raw"}


def fields_of(obj: Any) -> list[tuple[str, Any]]:
    if obj is None:
        return []
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return [(f.name, getattr(obj, f.name)) for f in dataclasses.fields(obj)]
    if hasattr(obj, "_asdict"):
        return list(obj._asdict().items())
    if isinstance(obj, dict):
        return list(obj.items())
    return [(k, v) for k, v in vars(obj).items() if not k.startswith("_")]


def xy(obj: Any) -> tuple[date | datetime, float] | None:
    """Extract (x, y) from a tuple or a DayKm-like dataclass."""
    if isinstance(obj, tuple) and len(obj) == 2:
        return obj[0], float(obj[1])
    x = y = None
    for name, v in fields_of(obj):
        if x is None and isinstance(v, date | datetime):
            x = v
        elif y is None and isinstance(v, int | float) and not isinstance(v, bool) and "km" in name:
            y = float(v)
    if y is None:
        y = next(
            (
                float(v)
                for _n, v in fields_of(obj)
                if isinstance(v, int | float) and not isinstance(v, bool)
            ),
            None,
        )
    return (x, y) if x is not None and y is not None else None


def _readonly_table(name: str) -> QTableWidget:
    t = QTableWidget(0, 0)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.verticalHeader().setVisible(False)
    t.setMinimumHeight(180)
    t.setAccessibleName(name)
    t.setTabKeyNavigation(False)
    return t


DRIVE_COLS = ("Date", "Start", "End", "Distance", "Duration", "Avg speed", "Start SoC", "End SoC")


class HistoryTab(QWidget):
    reload_requested = Signal()
    #: an ExportItem chosen from the Export… menu
    export_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._units = "metric"
        self._currency = "EUR"
        self._data: Any = None

        self.period = QComboBox()
        for label, days in PERIODS:
            self.period.addItem(label, days)
        self.period.setCurrentIndex(1)
        self.period.setAccessibleName("Period")
        self.period.currentIndexChanged.connect(lambda _i: self.reload_requested.emit())
        self.reload_btn = QPushButton("Re&load")
        self.reload_btn.clicked.connect(self.reload_requested)
        self.note = QLabel("")
        head = QHBoxLayout()
        head.addWidget(QLabel("&Period:", buddy=self.period))
        head.addWidget(self.period)
        head.addWidget(self.note, 1)
        self.export_btn = QPushButton("E&xport…")
        self.export_btn.setToolTip("Export drives, charges, snapshots or charts")
        self.export_menu = QMenu(self)
        self.export_btn.setMenu(self.export_menu)
        self.export_menu.aboutToShow.connect(self._build_export_menu)
        head.addWidget(self.export_btn)
        head.addWidget(self.reload_btn)

        self.cards = [StatusCard("") for _ in range(4)]
        cards = QHBoxLayout()
        for c in self.cards:
            cards.addWidget(c)

        self.soc_chart = Chart()
        self.km_chart = Chart()
        charts = QGridLayout()
        charts.addWidget(self.soc_chart, 0, 0)
        charts.addWidget(self.km_chart, 0, 1)

        self.health = QLabel("Battery health: –")
        self.health.setWordWrap(True)
        self.health.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.table = _readonly_table("Charge sessions")
        self.drives_table = _readonly_table("Drives")
        sess_box = QTabWidget()
        sess_box.addTab(self.drives_table, "Drives")
        sess_box.addTab(self.table, "Charges")

        self.nerd = QFormLayout()
        nerd_box = QGroupBox("Nerd stats")
        nerd_box.setLayout(self.nerd)

        lower = QHBoxLayout()
        lower.addWidget(sess_box, 3)
        lower.addWidget(nerd_box, 2)

        inner = QWidget()
        il = QVBoxLayout(inner)
        il.addLayout(cards)
        il.addLayout(charts, 1)
        il.addWidget(self.health)
        il.addLayout(lower, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addWidget(scroll, 1)

    # ------------------------------------------------------------- inputs

    def period_days(self) -> int | None:
        return self.period.currentData()

    def set_busy(self, busy: bool) -> None:
        self.reload_btn.setEnabled(not busy)
        self.note.setText("Loading…" if busy else self.note.text().replace("Loading…", ""))

    def set_units(self, units: str, currency: str) -> None:
        self._units, self._currency = units, currency
        if self._data is not None:
            self.show_data(self._data, self.note.text())

    # ------------------------------------------------------------- render

    def _fmt(self, name: str, value: Any) -> str:
        return fmt.field_value(name, value, self._units, self._currency)

    def show_data(self, data: Any, note: str = "") -> None:
        self._data = data
        self.note.setText(note)
        summary = fields_of(data.summary)
        used: set[str] = set()
        for card, keys in zip(self.cards, CARD_KEYS, strict=True):
            hit = next(
                ((n, v) for n, v in summary if n not in used and all(k in n for k in keys)),
                None,
            )
            if hit is None:
                hit = next(
                    ((n, v) for n, v in summary if n not in used and isinstance(v, int | float)),
                    None,
                )
            if hit is None:
                card.hide()
                continue
            used.add(hit[0])
            card.show()
            card.set_title(fmt.humanize(hit[0]))
            hint = "set a tariff in Settings" if "cost" in hit[0] and not hit[1] else ""
            card.set(self._fmt(*hit), hint)

        imperial = fmt.imperial(self._units)
        soc_pts = [p for p in (xy(o) for o in data.soc) if p]
        self.soc_chart.set_series("line", soc_pts, "State of charge", 100, lambda v: f"{v:.0f}%")
        km_pts = [p for p in (xy(o) for o in data.daily) if p]
        if imperial:
            km_pts = [(x, y / fmt.KM_PER_MI) for x, y in km_pts]
        unit = "mi" if imperial else "km"
        self.km_chart.set_series(
            "bars", km_pts, f"Distance per day ({unit})", None, lambda v: f"{v:.0f}"
        )

        self._fill_health(data.health)
        self._fill_sessions(data.sessions)
        self._fill_drives(getattr(data, "drives", []))

        while self.nerd.rowCount():
            self.nerd.removeRow(0)
        rows = [
            (fmt.humanize(n), self._fmt(n, v)) for n, v in summary if n not in used and n != "since"
        ]
        seen = {label.lower() for label, _v in rows}
        rows += [(k, v) for k, v in getattr(data, "nerd", {}).items() if k.lower() not in seen]
        for label, value in rows:
            lab = QLabel(value)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.nerd.addRow(label + ":", lab)

    def _fill_health(self, health: Any) -> None:
        items = fields_of(health)
        if not items or not getattr(health, "samples", 1):
            self.health.setText("Battery health: not enough data yet")
            return
        parts = []
        for n, v in items:
            if isinstance(v, list | tuple):
                continue
            if "degradation" in n and isinstance(v, int | float):
                parts.insert(0, f"<b>{100 - v:.1f} %</b> of first-seen range")
            elif "range_at_100" in n:
                parts.append(f"{fmt.humanize(n).lower()} {self._fmt(n, v)} at 100 %")
            else:
                parts.append(f"{fmt.humanize(n).lower()}: {self._fmt(n, v)}")
        self.health.setText("Battery health: " + " · ".join(parts))

    def _build_export_menu(self) -> None:
        from socks.gui.history_export import export_items

        m = self.export_menu
        m.clear()
        for item in export_items(date.today().year):
            if item.kind == "mileage":
                m.addSeparator()
            m.addAction(item.label, lambda i=item: self.export_requested.emit(i))
        m.addSeparator()
        for chart, name in ((self.soc_chart, "State of charge"), (self.km_chart, "Distance")):
            for kind in ("png", "svg"):
                m.addAction(
                    f"{name} chart ({kind.upper()})…", lambda c=chart, k=kind: c.save_dialog(k)
                )

    def _fill_drives(self, drives: list[Any]) -> None:
        from socks.gui.history_export import drive_rows

        u = self._units
        rows = drive_rows(drives)
        t = self.drives_table
        t.clear()
        t.setColumnCount(len(DRIVE_COLS))
        t.setHorizontalHeaderLabels(list(DRIVE_COLS))
        t.setRowCount(len(rows))
        for r, d in enumerate(rows):
            soc = ["–" if x is None else f"{x} %" for x in (d["start_soc"], d["end_soc"])]
            cells = [
                d["date"],
                d["start"],
                d["end"],
                fmt.dist(d["km"], u, 1),
                fmt.minutes(d["minutes"]),
                fmt.speed(d["avg_kmh"], u),
                *soc,
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c >= 3:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                t.setItem(r, c, item)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)

    def _fill_sessions(self, sessions: list[Any]) -> None:
        rows = [[(n, v) for n, v in fields_of(s) if n not in SKIP_COLS] for s in sessions]
        cols = [n for n, _v in rows[0]] if rows else ["start_ts", "end_ts", "energy_added_kwh"]
        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels([fmt.humanize(c) for c in cols])
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, (n, v) in enumerate(row):
                item = QTableWidgetItem(self._fmt(n, v))
                if isinstance(v, int | float) and not isinstance(v, bool):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(r, c, item)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
