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
from PySide6.QtGui import QKeyEvent
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
    return _setup_readonly(QTableWidget(0, 0), name)


def _setup_readonly(t: QTableWidget, name: str) -> QTableWidget:
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.verticalHeader().setVisible(False)
    t.setMinimumHeight(180)
    t.setAccessibleName(name)
    t.setTabKeyNavigation(False)
    return t


DRIVE_COLS = (
    "Date", "Start", "End", "Distance", "Duration", "Avg speed", "Start SoC", "End SoC",
    "From → To", "Purpose",
)  # fmt: skip
PURPOSE_COL = DRIVE_COLS.index("Purpose")
PLACE_KINDS = ("home", "work", "other")


class DrivesTable(QTableWidget):
    """Read-only drives; the Purpose cell is the one manual control.

    Click it to cycle — → business → private → commute; or press B/P/C/Del on a row.
    """

    purpose_requested = Signal(int, object)  # drive id, purpose or None

    def __init__(self) -> None:
        super().__init__(0, 0)
        self.cellClicked.connect(self._clicked)

    def _drive(self, row: int) -> tuple[int | None, str | None]:
        item = self.item(row, PURPOSE_COL)
        if item is None:
            return None, None
        return item.data(Qt.ItemDataRole.UserRole), item.data(Qt.ItemDataRole.UserRole + 1)

    def _clicked(self, row: int, col: int) -> None:
        from socks.gui.location import next_purpose

        if col == PURPOSE_COL:
            drive_id, cur = self._drive(row)
            if drive_id is not None:
                self.purpose_requested.emit(drive_id, next_purpose(cur))

    def keyPressEvent(self, e: QKeyEvent) -> None:
        from socks.gui.location import purpose_for_key

        is_del = e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
        handled, purpose = purpose_for_key(e.text(), is_del)
        row = self.currentRow()
        if handled and row >= 0:
            drive_id, _cur = self._drive(row)
            if drive_id is not None:
                self.purpose_requested.emit(drive_id, purpose)
                return
        super().keyPressEvent(e)

    def set_purpose(self, drive_id: int, purpose: str | None) -> None:
        for r in range(self.rowCount()):
            if self._drive(r)[0] == drive_id:
                _fill_purpose(self.item(r, PURPOSE_COL), purpose)


def _fill_purpose(item: QTableWidgetItem, purpose: str | None) -> None:
    item.setText(purpose.capitalize() if purpose else "—")
    item.setData(Qt.ItemDataRole.UserRole + 1, purpose)
    item.setToolTip("Click to change, or press B / P / C / Del")


class HistoryTab(QWidget):
    reload_requested = Signal()
    #: an ExportItem chosen from the Export… menu
    export_requested = Signal(object)
    purpose_requested = Signal(int, object)
    #: place id, name, kind
    place_edited = Signal(int, str, str)
    rebuild_requested = Signal()

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
        self.drives_table = DrivesTable()
        _setup_readonly(self.drives_table, "Drives")
        self.drives_table.purpose_requested.connect(self.purpose_requested)
        self.places_table = QTableWidget(0, 3)
        self.places_table.setAccessibleName("Places")
        self.places_table.verticalHeader().setVisible(False)
        self.places_table.setTabKeyNavigation(False)
        self.places_table.itemChanged.connect(self._place_item_changed)
        self.rebuild_btn = QPushButton("Re&build labels")
        self.rebuild_btn.setToolTip("Learn places from drive endpoints and relabel drives")
        self.rebuild_btn.clicked.connect(self.rebuild_requested)
        places_page = QWidget()
        pl = QVBoxLayout(places_page)
        pl.addWidget(self.places_table)
        pl.addWidget(self.rebuild_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.totals = QLabel("")
        self.totals.setWordWrap(True)
        self.totals.setTextFormat(Qt.TextFormat.PlainText)
        sess_box = QTabWidget()
        sess_box.addTab(self.drives_table, "Drives")
        sess_box.addTab(self.table, "Charges")
        sess_box.addTab(places_page, "Places")

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
        il.addWidget(self.totals)
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
        self._fill_places(getattr(data, "places", []))
        self._fill_totals(getattr(data, "mileage", None), getattr(data, "year", 0))

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
                d["route"],
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if 3 <= c < 8:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                t.setItem(r, c, item)
            pitem = QTableWidgetItem()
            pitem.setData(Qt.ItemDataRole.UserRole, d["id"])
            _fill_purpose(pitem, d["purpose"])
            t.setItem(r, PURPOSE_COL, pitem)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)

    def _fill_places(self, places: list[Any]) -> None:
        t = self.places_table
        t.blockSignals(True)
        t.clear()
        t.setHorizontalHeaderLabels(["Name", "Kind", "Visits"])
        t.setRowCount(len(places))
        for r, p in enumerate(places):
            name = QTableWidgetItem(p.name or "")
            name.setData(Qt.ItemDataRole.UserRole, p.id)
            name.setToolTip("Double-click or F2 to rename")
            t.setItem(r, 0, name)
            kind = QComboBox()
            kind.addItems(PLACE_KINDS)
            kind.setCurrentText(p.kind if p.kind in PLACE_KINDS else "other")
            kind.setAccessibleName(f"Kind of {p.display_name}")
            kind.currentTextChanged.connect(lambda _k, row=r: self._emit_place(row))
            t.setCellWidget(r, 1, kind)
            visits = QTableWidgetItem(str(p.visits))
            visits.setFlags(visits.flags() & ~Qt.ItemFlag.ItemIsEditable)
            visits.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.setItem(r, 2, visits)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        t.blockSignals(False)

    def _place_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self._emit_place(item.row())

    def _emit_place(self, row: int) -> None:
        name_item = self.places_table.item(row, 0)
        kind = self.places_table.cellWidget(row, 1)
        if name_item is None or kind is None:
            return
        place_id = name_item.data(Qt.ItemDataRole.UserRole)
        self.place_edited.emit(int(place_id), name_item.text(), kind.currentText())

    def _fill_totals(self, m: Any, year: int) -> None:
        if m is None or not getattr(m, "drives", 0):
            self.totals.setText("")
            return
        u = self._units
        self.totals.setText(
            f"{year}: {fmt.dist(m.total_km, u)} in {m.drives} drives — "
            f"business {fmt.dist(m.business_km, u)} · private {fmt.dist(m.private_km, u)} · "
            f"commute {fmt.dist(m.commute_km, u)} · unclassified {fmt.dist(m.unclassified_km, u)}"
        )

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
