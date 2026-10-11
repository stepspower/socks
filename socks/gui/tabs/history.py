# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""History tab: summary cards, SoC / km charts, charge sessions, battery health.

Rendering is driven by the field names of the dataclasses returned by
``socks.history.stats`` so it keeps working as that module grows fields.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, Signal
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
    (QT_TRANSLATE_NOOP("HistoryTab", "Last 7 days"), 7),
    (QT_TRANSLATE_NOOP("HistoryTab", "Last 30 days"), 30),
    (QT_TRANSLATE_NOOP("HistoryTab", "Last 90 days"), 90),
    (QT_TRANSLATE_NOOP("HistoryTab", "Last year"), 365),
    (QT_TRANSLATE_NOOP("HistoryTab", "All time"), None),
)
CARD_KEYS = (("km",), ("kwh",), ("cost",), ("wh_per_km",))
SKIP_COLS = {"id", "vin", "raw"}


def fields_of(obj: Any) -> list[tuple[str, Any]]:
    """(name, value) pairs of a stats dataclass; [] for None."""
    if obj is None:
        return []
    return [(f.name, getattr(obj, f.name)) for f in dataclasses.fields(obj)]


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
    QT_TRANSLATE_NOOP("HistoryTab", "Date"),
    QT_TRANSLATE_NOOP("HistoryTab", "Start"),
    QT_TRANSLATE_NOOP("HistoryTab", "End"),
    QT_TRANSLATE_NOOP("HistoryTab", "Distance"),
    QT_TRANSLATE_NOOP("HistoryTab", "Duration"),
    QT_TRANSLATE_NOOP("HistoryTab", "Avg speed"),
    QT_TRANSLATE_NOOP("HistoryTab", "Start SoC"),
    QT_TRANSLATE_NOOP("HistoryTab", "End SoC"),
    QT_TRANSLATE_NOOP("HistoryTab", "From → To"),
    QT_TRANSLATE_NOOP("HistoryTab", "Purpose"),
)  # fmt: skip
PURPOSE_COL = len(DRIVE_COLS) - 1  # "Purpose"
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
    item.setToolTip(
        QCoreApplication.translate("history", "Click to change, or press B / P / C / Del")
    )


class HistoryTab(QWidget):
    reload_requested = Signal()
    #: an ExportItem chosen from the Export… menu
    export_requested = Signal(object)
    drive_selected = Signal(int)
    gpx_requested = Signal(int)
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
            self.period.addItem(QCoreApplication.translate("HistoryTab", label), days)
        self.period.setCurrentIndex(1)
        self.period.setAccessibleName(self.tr("Period"))
        self.period.currentIndexChanged.connect(lambda _i: self.reload_requested.emit())
        self.reload_btn = QPushButton(self.tr("Re&load"))
        self.reload_btn.clicked.connect(self.reload_requested)
        self.note = QLabel("")
        head = QHBoxLayout()
        head.addWidget(QLabel(self.tr("&Period:"), buddy=self.period))
        head.addWidget(self.period)
        head.addWidget(self.note, 1)
        self.export_btn = QPushButton(self.tr("E&xport…"))
        self.export_btn.setToolTip(self.tr("Export drives, charges, snapshots or charts"))
        self.export_menu = QMenu(self)
        self.export_btn.setMenu(self.export_menu)
        self.export_menu.aboutToShow.connect(self._build_export_menu)
        head.addWidget(self.export_btn)
        head.addWidget(self.reload_btn)

        self.cards = [StatusCard("") for _ in range(4)]
        self.drain_card = StatusCard(self.tr("Vampire drain"))
        self.asleep_card = StatusCard(self.tr("Asleep while parked"))
        cards = QHBoxLayout()
        for c in (*self.cards, self.drain_card, self.asleep_card):
            cards.addWidget(c)

        self.soc_chart = Chart()
        self.km_chart = Chart()
        charts = QGridLayout()
        charts.addWidget(self.soc_chart, 0, 0)
        charts.addWidget(self.km_chart, 0, 1)

        self.health = QLabel(self.tr("Battery health: –"))
        self.health.setWordWrap(True)
        self.health.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.table = _readonly_table(self.tr("Charge sessions"))
        self.drives_table = DrivesTable()
        _setup_readonly(self.drives_table, self.tr("Drives"))
        self.drives_table.purpose_requested.connect(self.purpose_requested)
        self.places_table = QTableWidget(0, 3)
        self.places_table.setAccessibleName(self.tr("Places"))
        self.places_table.verticalHeader().setVisible(False)
        self.places_table.setTabKeyNavigation(False)
        self.places_table.itemChanged.connect(self._place_item_changed)
        self.rebuild_btn = QPushButton(self.tr("Re&build labels"))
        self.rebuild_btn.setToolTip(self.tr("Learn places from drive endpoints and relabel drives"))
        self.rebuild_btn.clicked.connect(self.rebuild_requested)
        places_page = QWidget()
        pl = QVBoxLayout(places_page)
        pl.addWidget(self.places_table)
        pl.addWidget(self.rebuild_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.totals = QLabel("")
        self.totals.setWordWrap(True)
        self.totals.setTextFormat(Qt.TextFormat.PlainText)
        sess_box = QTabWidget()
        self.drives_table.itemSelectionChanged.connect(self._drive_selection)
        self.profile_kind = QComboBox()
        for key, label in (
            ("speed", self.tr("Speed")),
            ("power", self.tr("Power")),
            ("soc", self.tr("State of charge")),
        ):
            self.profile_kind.addItem(label, key)
        self.profile_kind.setAccessibleName(self.tr("Drive profile"))
        self.profile_kind.currentIndexChanged.connect(lambda _i: self._draw_profile())
        self.gpx_btn = QPushButton(self.tr("Export &GPX…"))
        self.gpx_btn.clicked.connect(self._gpx_clicked)
        self.gpx_btn.hide()
        self.profile_chart = Chart()
        self.profile_chart.setMinimumHeight(140)
        self.profile_note = QLabel(self.tr("Select a drive to see its profile."))
        prow = QHBoxLayout()
        prow.addWidget(self.profile_kind)
        prow.addWidget(self.profile_note, 1)
        prow.addWidget(self.gpx_btn)
        drives_page = QWidget()
        dl = QVBoxLayout(drives_page)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(self.drives_table, 3)
        dl.addLayout(prow)
        dl.addWidget(self.profile_chart, 2)
        self._profile: list[Any] = []
        self._profile_drive: int | None = None
        sess_box.addTab(drives_page, self.tr("Drives"))
        sess_box.addTab(self.table, self.tr("Charges"))
        sess_box.addTab(places_page, self.tr("Places"))

        self.nerd = QFormLayout()
        nerd_box = QGroupBox(self.tr("Nerd stats"))
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
        self.note.setText(self.tr("Loading…") if busy else self.note.text().replace("Loading…", ""))

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
            hint = self.tr("set a tariff in Settings") if "cost" in hit[0] and not hit[1] else ""
            card.set(self._fmt(*hit), hint)
        self._fill_parked(data.parked)

        imperial = fmt.imperial(self._units)
        soc_pts = [(t, float(v)) for t, v in data.soc]
        self.soc_chart.set_series(
            "line", soc_pts, self.tr("State of charge"), 100, lambda v: f"{v:.0f}%"
        )
        km_pts = [(d.date, d.km) for d in data.daily]
        if imperial:
            km_pts = [(x, y / fmt.KM_PER_MI) for x, y in km_pts]
        unit = "mi" if imperial else "km"
        self.km_chart.set_series(
            "bars",
            km_pts,
            self.tr("Distance per day ({0})").format(unit),
            None,
            lambda v: f"{v:.0f}",
        )

        self._fill_health(data.health)
        self._fill_sessions(data.sessions)
        self._fill_drives(data.drives)
        self._fill_places(data.places)
        self._fill_totals(data.mileage, data.year)

        while self.nerd.rowCount():
            self.nerd.removeRow(0)
        rows = [
            (fmt.humanize(n), self._fmt(n, v)) for n, v in summary if n not in used and n != "since"
        ]
        seen = {label.lower() for label, _v in rows}
        rows += [(k, v) for k, v in data.nerd.items() if k.lower() not in seen]
        for label, value in rows:
            lab = QLabel(value)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.nerd.addRow(label + ":", lab)

    def _fill_health(self, health: Any) -> None:
        items = fields_of(health)
        if not items or not health.samples:
            self.health.setText(self.tr("Battery health: not enough data yet"))
            return
        parts = []
        for n, v in items:
            if isinstance(v, list | tuple):
                continue
            if "degradation" in n and isinstance(v, int | float):
                parts.insert(0, self.tr("<b>{0:.1f} %</b> of first-seen range").format(100 - v))
            elif "range_at_100" in n:
                parts.append(f"{fmt.humanize(n).lower()} {self._fmt(n, v)} at 100 %")
            else:
                parts.append(f"{fmt.humanize(n).lower()}: {self._fmt(n, v)}")
        self.health.setText(self.tr("Battery health: ") + " · ".join(parts))

    def _build_export_menu(self) -> None:
        from socks.gui.history_export import export_items

        m = self.export_menu
        m.clear()
        for item in export_items(date.today().year):
            if item.kind == "mileage":
                m.addSeparator()
            m.addAction(item.label, lambda i=item: self.export_requested.emit(i))
        m.addSeparator()
        for chart, name in (
            (self.soc_chart, self.tr("State of charge")),
            (self.km_chart, self.tr("Distance")),
        ):
            for kind in ("png", "svg"):
                m.addAction(
                    self.tr("{0} chart ({1})…").format(name, kind.upper()),
                    lambda c=chart, k=kind: c.save_dialog(k),
                )

    def _fill_drives(self, drives: list[Any]) -> None:
        from socks.gui.history_export import drive_rows

        u = self._units
        rows = drive_rows(drives)
        t = self.drives_table
        t.clear()
        t.setColumnCount(len(DRIVE_COLS))
        t.setHorizontalHeaderLabels(
            [QCoreApplication.translate("HistoryTab", c) for c in DRIVE_COLS]
        )
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
        t.setHorizontalHeaderLabels([self.tr("Name"), self.tr("Kind"), self.tr("Visits")])
        t.setRowCount(len(places))
        for r, p in enumerate(places):
            name = QTableWidgetItem(p.name or "")
            name.setData(Qt.ItemDataRole.UserRole, p.id)
            name.setToolTip(self.tr("Double-click or F2 to rename"))
            t.setItem(r, 0, name)
            kind = QComboBox()
            kind.addItems(PLACE_KINDS)
            kind.setCurrentText(p.kind if p.kind in PLACE_KINDS else "other")
            kind.setAccessibleName(self.tr("Kind of {0}").format(p.display_name))
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

    def _fill_parked(self, p: Any) -> None:
        if p is None or not p.hours_parked:
            self.drain_card.set("–", self.tr("no data yet"))
            self.asleep_card.set("–", "")
            return
        self.drain_card.set(
            self.tr("{0:.2f} %/day").format(p.drain_pct_per_day),
            self.tr("{0:.1f} kWh / {1:.0f} h").format(p.kwh_lost, p.hours_parked),
        )
        share = min(100.0, 100.0 * p.hours_asleep_estimate / p.hours_parked)
        self.asleep_card.set(
            f"{share:.0f} %",
            self.tr("≈ {0:.0f} h (est.)").format(p.hours_asleep_estimate),
        )

    # ------------------------------------------------------- drive profile

    def _drive_selection(self) -> None:
        row = self.drives_table.currentRow()
        drive_id, _p = self.drives_table._drive(row) if row >= 0 else (None, None)
        if drive_id is not None and drive_id != self._profile_drive:
            self._profile_drive = drive_id
            self.profile_note.setText(self.tr("Loading…"))
            self.drive_selected.emit(int(drive_id))

    def show_profile(self, drive_id: int, points: list[Any], has_gpx: bool) -> None:
        if drive_id != self._profile_drive:
            return
        self._profile = points
        self.gpx_btn.setVisible(has_gpx)
        self.profile_note.setText(
            ""
            if points
            else self.tr("No detailed track for this drive (recorded before drive mode).")
        )
        self._draw_profile()

    def _draw_profile(self) -> None:
        kind = self.profile_kind.currentData()
        u = self._units
        idx = {"speed": 1, "power": 2, "soc": 3}[kind]
        pts = [(p[0], float(p[idx])) for p in self._profile if p[idx] is not None]
        if kind == "speed":
            if fmt.imperial(u):
                pts = [(t, v / fmt.KM_PER_MI) for t, v in pts]
            title = self.tr("Speed ({0})").format("mph" if fmt.imperial(u) else "km/h")
            self.profile_chart.set_series("line", pts, title, None, lambda v: f"{v:.0f}")
        elif kind == "power":
            # the axis starts at 0: regeneration (negative power) shows as 0
            pts = [(t, max(0.0, v)) for t, v in pts]
            self.profile_chart.set_series(
                "line", pts, self.tr("Power (kW, drive)"), None, lambda v: f"{v:.0f}"
            )
        else:
            self.profile_chart.set_series(
                "line", pts, self.tr("State of charge"), 100, lambda v: f"{v:.0f}%"
            )

    def _gpx_clicked(self) -> None:
        if self._profile_drive is not None:
            self.gpx_requested.emit(int(self._profile_drive))

    def _fill_totals(self, m: Any, year: int) -> None:
        if m is None or not m.drives:
            self.totals.setText("")
            return
        u = self._units
        self.totals.setText(
            self.tr(
                "{0}: {1} in {2} drives — business {3} · private {4} · commute {5} · "
                "unclassified {6}"
            ).format(
                year,
                fmt.dist(m.total_km, u),
                m.drives,
                fmt.dist(m.business_km, u),
                fmt.dist(m.private_km, u),
                fmt.dist(m.commute_km, u),
                fmt.dist(m.unclassified_km, u),
            )
        )
        measured, estimated = getattr(self._data, "energy_split", (0.0, 0.0))
        if measured or estimated:
            self.totals.setText(
                self.totals.text()
                + " · "
                + self.tr("energy {0} measured, {1} estimated").format(
                    fmt.energy(measured), fmt.energy(estimated)
                )
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
