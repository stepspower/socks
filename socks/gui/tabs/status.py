# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Status tab: SoC gauge plus cards for charging, climate, closures, odometer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, Signal
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from socks.gui import fmt
from socks.gui.inline_logic import (
    SWITCHES,
    arg_for,
    display_range,
    editor_value,
    switch_command,
    switch_enabled,
    switch_state,
    tr_switch,
)
from socks.gui.tabs.depart import DepartCard
from socks.gui.widgets.gauge import Gauge
from socks.gui.widgets.inline import EditableValue, InlineEdit
from socks.gui.widgets.slide_switch import SlideSwitch
from socks.gui.widgets.status_card import Chip, StatusCard

STALE_SECONDS = 15 * 60


def ble_signal(rssi: int) -> tuple[str, str]:
    """(signal label strong/ok/weak, rounded distance) from core.ble, or a local fallback."""
    try:
        from socks.core.ble import estimate_distance_m, signal_label
    except ImportError:  # until core ships socks/core/ble.py
        label = "strong" if rssi >= -65 else "ok" if rssi >= -80 else "weak"
        dist = 10 ** ((-59 - rssi) / 20)  # log-distance model, 1 m reference −59 dBm, n = 2
    else:
        label, dist = signal_label(rssi), estimate_distance_m(rssi)
    if dist is None:
        return label, "?"
    return label, f"{dist:.0f}" if dist >= 2 else f"{dist:.1f}"


def osm_url(lat: float, lon: float) -> str:
    return (
        f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=16/{lat:.5f}/{lon:.5f}"
    )


def _names(items: tuple[str, ...]) -> str:
    return ", ".join(i.replace("_", " ") for i in items)


class StatusTab(QWidget):
    refresh_requested = Signal()
    wake_requested = Signal()
    #: (Command, argument in command units or None) — the window runs the confirm gate
    command_requested = Signal(object, object)
    #: new display name for the car ("" = back to the name the car/VIN gives)
    rename_requested = Signal(str)
    #: (Command, arg): already confirmed by a completed slide gesture (no dialog)
    confirmed_command_requested = Signal(object, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state: Any = None
        self._units = "metric"

        # Click-to-rename title (PlainText: car_name comes from the car/cloud).
        self.name_override = ""
        self.title = EditableValue(self.tr("No vehicle data yet"))
        self.title.setForegroundRole(QPalette.ColorRole.WindowText)
        tf = QFont(self.font())
        tf.setPointSizeF(tf.pointSizeF() * 1.3)
        tf.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(tf)
        self.title.activated.connect(self._start_rename)
        self.rename = QLineEdit()
        self.rename.setFont(tf)
        self.rename.setMaxLength(60)
        self.rename.setPlaceholderText(self.tr("Name (empty = model name)"))
        self.rename.setAccessibleName(self.tr("Car name"))
        self.rename.hide()
        self.rename.installEventFilter(self)
        self.chip = Chip()
        self.chip.set_chip(self.tr("no data"), "muted")
        self.refresh_btn = QPushButton(self.tr("&Refresh"))
        self.refresh_btn.setToolTip(self.tr("Read the current state from the car (F5)"))
        self.refresh_btn.clicked.connect(self.refresh_requested)
        self.wake_btn = QPushButton(self.tr("&Wake && refresh"))
        self.wake_btn.setToolTip(
            self.tr("Wake the car and read it. A wake keeps it awake for about 15 minutes.")
        )
        self.wake_btn.clicked.connect(self.wake_requested)
        self.wake_btn.hide()
        self._asleep = False

        head = QHBoxLayout()
        head.addWidget(self.title)
        head.addWidget(self.rename)
        head.addWidget(self.chip)
        head.addStretch(1)
        head.addWidget(self.wake_btn)
        head.addWidget(self.refresh_btn)

        self.gauge = Gauge()
        self.range_card = StatusCard(self.tr("Range"))
        self.charge_card = StatusCard(self.tr("Charging"))
        self.climate_card = StatusCard(self.tr("Temperature"))
        self.security_card = StatusCard(self.tr("Locks & closures"))
        self.odo_card = StatusCard(self.tr("Odometer"))
        self.vehicle_card = StatusCard(self.tr("Vehicle"))

        grid = QGridLayout()
        grid.setSpacing(12)
        cards = (
            self.range_card,
            self.charge_card,
            self.climate_card,
            self.security_card,
            self.odo_card,
            self.vehicle_card,
        )
        for i, c in enumerate(cards):
            # Fill the row height so side-by-side cards share top and bottom edges
            # (StatusCard's default Maximum policy would centre the shorter one).
            c.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            grid.addWidget(c, i // 2, i % 2)
        grid.setRowStretch(3, 1)

        self.gauge.setMaximumSize(420, 420)
        body = QHBoxLayout()
        body.addWidget(self.gauge, 1, Qt.AlignmentFlag.AlignTop)
        body.addLayout(grid, 2)

        self._cmds: dict[str, Any] = {}
        self._editors: dict[str, InlineEdit] = {}
        self.gauge.limit_clicked.connect(lambda: self._start_editor("set_charge_limit"))

        self._switches: dict[str, SlideSwitch] = {}
        self.location_card = StatusCard(self.tr("Location"))
        self._coords: tuple[float, float] | None = None
        self.map_btn = QPushButton(self.tr("Open in &map"))
        self.map_btn.setToolTip(self.tr("Opens openstreetmap.org in your browser"))
        self.map_btn.clicked.connect(self._open_map)
        self.copy_btn = QPushButton(self.tr("Cop&y"))
        self.copy_btn.clicked.connect(self._copy_location)
        self.location_card.add_row(self.map_btn, self.copy_btn)
        self.location_card.hide()
        grid.addWidget(self.location_card, 3, 0, 1, 2)
        grid.setRowStretch(3, 0)
        grid.setRowStretch(4, 1)

        self.depart = DepartCard()

        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addWidget(self.depart)
        lay.addLayout(body, 1)

    # ---------------------------------------------------- click-to-change

    def set_commands(self, commands: dict[Any, Any]) -> None:
        """Create inline editors/chips for the commands core offers (others stay hidden)."""
        self._cmds = {c.value: c for c in commands}
        self._infos = commands

        def editor(cmd: str, kind: str) -> InlineEdit | None:
            if cmd not in self._cmds:
                return None
            ed = InlineEdit(kind)
            ed.committed.connect(lambda v, c=cmd: self._commit(c, v))
            self._editors[cmd] = ed
            return ed

        def switch(field: str) -> SlideSwitch | None:
            sp = SWITCHES[field]
            if sp.on_cmd not in self._cmds or (sp.off_cmd and sp.off_cmd not in self._cmds):
                return None
            sw = SlideSwitch(
                tr_switch(sp.name),
                tr_switch(sp.on_text),
                tr_switch(sp.off_text),
                risky_on=sp.risky_on,
                risky_off=sp.risky_off,
                one_way=sp.off_cmd is None,
            )
            sw.toggled.connect(lambda on, f=field: self._slid(f, on))
            self._switches[field] = sw
            return sw

        def row(card: StatusCard, *ws: QWidget | None) -> None:
            ws2 = [w for w in ws if w is not None]
            if ws2:
                card.add_row(*ws2)

        row(
            self.charge_card,
            editor("set_charge_limit", "slider"),
            editor("set_charge_amps", "spin"),
        )
        row(self.charge_card, switch("charging"), switch("charge_port_open"))
        row(
            self.charge_card,
            editor("set_scheduled_charging", "time"),
            editor("set_scheduled_departure", "time"),
        )
        row(self.climate_card, editor("set_temp", "spin"), switch("climate_on"))
        row(self.security_card, switch("locked"), switch("sentry_on"))
        row(self.security_card, switch("frunk"), switch("trunk"))
        self._configure_editors()

    def _info(self, cmd_value: str) -> Any:
        return self._infos[self._cmds[cmd_value]]

    def _configure_editors(self) -> None:
        for cmd, ed in self._editors.items():
            if ed.kind != "time":
                lo, hi, step, unit = display_range(self._info(cmd), self._units)
                ed.configure(lo, hi, step, "" if ed.kind == "slider" else unit)

    def _start_editor(self, cmd: str) -> None:
        ed = self._editors.get(cmd)
        if ed is not None and self._state is not None:
            ed.start()

    def _commit(self, cmd: str, value: Any) -> None:
        try:
            arg = arg_for(self._info(cmd), value, self._units)
        except ValueError:
            return
        self.command_requested.emit(self._cmds[cmd], arg)

    def _slid(self, field: str, on: bool) -> None:
        cmd = switch_command(field, on)
        if cmd is not None and cmd in self._cmds:
            self.confirmed_command_requested.emit(self._cmds[cmd], None)

    def revert_switches(self) -> None:
        """A slide-confirmed command failed: show the car's real state again."""
        for sw in self._switches.values():
            sw.revert()

    def _update_controls(self, s: Any) -> None:
        u = self._units

        def put(cmd: str, text: str, current: Any, show: bool = True) -> None:
            ed = self._editors.get(cmd)
            if ed is None:
                return
            ed.setVisible(show)
            if not ed.editing():
                ed.set_value(text, current)

        put(
            "set_charge_limit",
            QCoreApplication.translate("StatusTab", "Limit {0} %").format(s.charge_limit),
            s.charge_limit,
            s.charge_limit is not None,
        )
        amps = s.charge_current_request_a
        put("set_charge_amps", f"{amps} A", amps, amps is not None)
        start = editor_value("set_scheduled_charging", s, u)
        if start is not None:
            text, minutes = start
            put(
                "set_scheduled_charging",
                self.tr("starts {0}").format(text)
                if minutes is not None
                else self.tr("starts: off"),
                minutes,
            )
        dep = editor_value("set_scheduled_departure", s, u)
        if dep is not None:
            put(
                "set_scheduled_departure",
                self.tr("departure {0}").format(dep[0]),
                dep[1],
                s.scheduled_charging_mode == "DepartBy",
            )
        if amps is not None and "set_charge_amps" in self._editors:
            lo, hi, step, unit = display_range(self._info("set_charge_amps"), u)
            hi = s.charge_current_request_max_a or hi
            self._editors["set_charge_amps"].configure(lo, hi, step, unit)
        if s.target_temp_c is not None:
            t = fmt.temp_to_display(s.target_temp_c, u)
            put(
                "set_temp",
                QCoreApplication.translate("StatusTab", "Target {0}").format(
                    fmt.temp(s.target_temp_c, u)
                ),
                t,
            )
        for field, sw in self._switches.items():
            sw.set_checked(switch_state(field, s))
            if not switch_enabled(field, s):
                sw.setEnabled(False)
                sw.setToolTip(self.tr("Plug in the cable to charge"))
            sw.setVisible(field != "charge_port_open" or s.charge_port_open is not None)

    # ------------------------------------------------------------- rename

    def display_name(self) -> str:
        s = self._state
        car = s.car_name if s is not None else None
        return self.name_override or car or self.tr("Vehicle")

    def _show_title(self) -> None:
        self.title.setText(self.display_name())
        vin = self._state.vin if self._state is not None else ""
        tip = self.tr("Click to rename")
        self.title.setToolTip(f"VIN {vin}\n{tip}" if vin else tip)

    def set_name_override(self, name: str) -> None:
        self.name_override = name or ""
        self._show_title()

    def _start_rename(self) -> None:
        self.rename.setText(self.name_override or self.display_name())
        self.title.hide()
        self.rename.show()
        self.rename.selectAll()
        self.rename.setFocus()

    def _end_rename(self, commit: bool) -> None:
        if not self.rename.isVisible():
            return
        self.rename.hide()
        self.title.show()
        self.title.setFocus()
        if commit:
            text = self.rename.text().strip()
            car = self._state.car_name if self._state is not None else None
            if text == (car or ""):
                text = ""  # same as the car's own name: no override needed
            self.rename_requested.emit(text)

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self.rename:
            if ev.type() == QEvent.Type.KeyPress:
                if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self._end_rename(True)
                    return True
                if ev.key() == Qt.Key.Key_Escape:
                    self._end_rename(False)
                    return True
            elif ev.type() == QEvent.Type.FocusOut:
                self._end_rename(False)
        return super().eventFilter(obj, ev)

    def set_location(
        self,
        text: str | None,
        coords: tuple[float, float] | None = None,
        fix: datetime | None = None,
    ) -> None:
        """Place / address / coordinates with the fix time; None hides the card."""
        self.location_card.setVisible(bool(text))
        self._coords = coords
        if text:
            when = self.tr("GPS fix {0}").format(fmt.local_time(fix)) if fix else ""
            self.location_card.set(text, when)
        self.map_btn.setEnabled(coords is not None)
        self.copy_btn.setEnabled(bool(text))

    def _open_map(self) -> None:
        if self._coords is not None:
            import webbrowser

            webbrowser.open(osm_url(*self._coords))

    def _copy_location(self) -> None:
        from PySide6.QtGui import QGuiApplication

        text = self.location_card.value_text()
        if self._coords is not None:
            text = f"{text} ({self._coords[0]:.5f}, {self._coords[1]:.5f})"
        QGuiApplication.clipboard().setText(text)

    def set_busy(self, busy: bool) -> None:
        self.refresh_btn.setEnabled(not busy)
        self.wake_btn.setEnabled(not busy)
        self.refresh_btn.setText(self.tr("Refreshing…") if busy else "&Refresh")

    def set_units(self, units: str) -> None:
        self._units = units
        self.depart.units = units
        self.depart.set_plan(self.depart.plan)
        self._configure_editors()
        if self._state is not None:
            self.update_state(self._state)

    def update_state(self, s: Any) -> None:
        self._state = s
        self._asleep = False
        self.wake_btn.hide()
        u = self._units
        self._show_title()
        charging_name = s.charging.name
        is_charging = charging_name == "CHARGING"
        cap = fmt.dist(s.rated_range_km, u)
        if is_charging:
            cap += " · charging"
        self.gauge.set_state(s.soc, s.charge_limit, cap, is_charging)

        usable = self.tr("usable {0} %").format(s.usable_soc) if s.usable_soc is not None else ""
        ideal = (
            self.tr("ideal {0}").format(fmt.dist(s.ideal_range_km, u))
            if s.ideal_range_km is not None
            else ""
        )
        self.range_card.set(
            fmt.dist(s.rated_range_km, u), " · ".join(x for x in (usable, ideal) if x)
        )

        state_text = s.charging.value
        if s.plugged_in is False:
            value, detail = self.tr("Unplugged"), ""
        elif is_charging:
            value = f"{state_text} · {fmt.power(s.charger_power_kw)}"
            # the limit itself is the editable "Limit …" line below: don't repeat it here
            bits = [
                self.tr("{0} to limit").format(fmt.minutes(s.minutes_to_limit))
                if s.minutes_to_limit is not None
                else ""
            ]
            if s.charger_voltage and s.charger_current_a:
                bits.append(f"{s.charger_voltage} V × {s.charger_current_a} A")
            if s.charge_energy_added_kwh is not None:
                bits.append(f"+{fmt.energy(s.charge_energy_added_kwh)}")
            detail = " · ".join(b for b in bits if b)
        else:
            value = self.tr("Plugged in · {0}").format(state_text) if s.plugged_in else state_text
            detail = (
                self.tr("limit {0} %").format(s.charge_limit)
                if s.charge_limit is not None and "set_charge_limit" not in self._editors
                else ""
            )
            if (
                s.scheduled_charging_start is not None
                and "set_scheduled_charging" not in self._editors
            ):
                detail += self.tr(" · starts {0}").format(
                    fmt.local_time(s.scheduled_charging_start)
                )
        self.charge_card.set(value, detail)

        climate = (
            ""
            if "climate_on" in self._switches
            else (" · climate on" if s.climate_on else " · climate off")
        )
        self.climate_card.set(
            self.tr("{0} inside").format(fmt.temp(s.inside_temp_c, u)),
            self.tr("{0} outside{1}").format(fmt.temp(s.outside_temp_c, u), climate),
        )

        lock = {
            True: self.tr("Locked"),
            False: self.tr("UNLOCKED"),
            None: self.tr("Lock state unknown"),
        }[s.locked]
        open_bits = []
        if s.doors_open:
            open_bits.append(self.tr("open: {0}").format(_names(s.doors_open)))
        if s.windows_open:
            open_bits.append(self.tr("windows: {0}").format(_names(s.windows_open)))
        if not open_bits:
            open_bits.append("all doors and windows closed")
        if s.sentry_on is not None and "sentry_on" not in self._switches:
            open_bits.append("sentry on" if s.sentry_on else "sentry off")
        self.security_card.set(lock, " · ".join(open_bits))

        self.odo_card.set(fmt.dist(s.odometer_km, u), "")
        shift = s.shift.value
        veh = self.tr("Shift {0}").format(shift)
        if s.speed_kmh:
            veh += f" · {fmt.speed(s.speed_kmh, u)}"
        self.vehicle_card.set(veh, self.tr("firmware {0}").format(s.firmware) if s.firmware else "")
        self._update_controls(s)
        self.depart.set_charge_limit(s.charge_limit)
        self.update_age()

    def show_asleep(self) -> None:
        """A non-waking read found the car asleep: keep the last state, offer a wake."""
        self._asleep = True
        self.wake_btn.show()
        self.update_age()

    def update_age(self) -> None:
        s = self._state
        if self._asleep:
            last = fmt.local_time(s.ts)[:5] if s is not None else "–"
            self.chip.set_chip(self.tr("asleep · last read {0}").format(last), "muted")
            self.chip.setToolTip(self.tr("The car is asleep; it was not woken."))
            return
        if s is None:
            return
        ts = s.ts if s.ts.tzinfo else s.ts.replace(tzinfo=UTC)
        old = (datetime.now(UTC) - ts).total_seconds() > STALE_SECONDS
        src = s.source.value.upper()
        text = f"{src} · {fmt.age(ts)}"
        tip = self.tr("Read at {0}").format(fmt.local_time(ts, with_date=True))
        rssi = s.ble_rssi_dbm if s.source.value == "ble" else None
        label = None
        if rssi is not None:
            label, dist = ble_signal(rssi)
            text += " · " + self.tr("{0} dBm · ~{1} m").format(f"{rssi:d}".replace("-", "−"), dist)
            tip += "\n" + self.tr("Bluetooth signal strength; distance is a rough estimate")
        self.chip.set_chip(text, "warn" if old else "normal", label)
        self.chip.setToolTip(tip)
        self.chip.setAccessibleName(self.tr("Data source {0}, {1}").format(src, fmt.age(ts)))

    def focus_default(self) -> None:
        self.refresh_btn.setFocus(Qt.FocusReason.OtherFocusReason)
