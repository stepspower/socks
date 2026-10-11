# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Status tab: SoC gauge plus cards for charging, climate, closures, odometer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from socks.gui import fmt
from socks.gui.inline_logic import (
    TOGGLES,
    arg_for,
    display_range,
    hhmm,
    minutes_of,
    toggle_command,
    toggle_text,
)
from socks.gui.widgets.gauge import Gauge
from socks.gui.widgets.inline import InlineEdit, ToggleChip
from socks.gui.widgets.status_card import Chip, StatusCard

STALE_SECONDS = 15 * 60


def _names(items: tuple[str, ...]) -> str:
    return ", ".join(i.replace("_", " ") for i in items)


class StatusTab(QWidget):
    refresh_requested = Signal()
    #: (Command, argument in command units or None) — the window runs the confirm gate
    command_requested = Signal(object, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state: Any = None
        self._units = "metric"

        self.title = QLabel("No vehicle data yet")
        self.title.setTextFormat(Qt.TextFormat.PlainText)  # car_name comes from the car/cloud
        tf = QFont(self.font())
        tf.setPointSizeF(tf.pointSizeF() * 1.3)
        tf.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(tf)
        self.chip = Chip()
        self.chip.set_chip("no data", "muted")
        self.refresh_btn = QPushButton("&Refresh")
        self.refresh_btn.setToolTip("Read the current state from the car (F5)")
        self.refresh_btn.clicked.connect(self.refresh_requested)

        head = QHBoxLayout()
        head.addWidget(self.title)
        head.addWidget(self.chip)
        head.addStretch(1)
        head.addWidget(self.refresh_btn)

        self.gauge = Gauge()
        self.range_card = StatusCard("Range")
        self.charge_card = StatusCard("Charging")
        self.climate_card = StatusCard("Temperature")
        self.security_card = StatusCard("Locks & closures")
        self.odo_card = StatusCard("Odometer")
        self.vehicle_card = StatusCard("Vehicle")

        grid = QGridLayout()
        grid.setSpacing(8)
        cards = (
            self.range_card,
            self.charge_card,
            self.climate_card,
            self.security_card,
            self.odo_card,
            self.vehicle_card,
        )
        for i, c in enumerate(cards):
            grid.addWidget(c, i // 2, i % 2)
        grid.setRowStretch(3, 1)

        self.gauge.setMaximumSize(420, 420)
        body = QHBoxLayout()
        body.addWidget(self.gauge, 1, Qt.AlignmentFlag.AlignTop)
        body.addLayout(grid, 2)

        self._cmds: dict[str, Any] = {}
        self._editors: dict[str, InlineEdit] = {}
        self._chips: dict[str, ToggleChip] = {}
        self.gauge.limit_clicked.connect(lambda: self._start_editor("set_charge_limit"))

        self.location = QLabel("")
        self.location.setTextFormat(Qt.TextFormat.PlainText)  # place names are user text
        self.location.setAccessibleName("Location")
        self.location.hide()
        grid.addWidget(self.location, 3, 0, 1, 2)
        grid.setRowStretch(3, 0)
        grid.setRowStretch(4, 1)

        lay = QVBoxLayout(self)
        lay.addLayout(head)
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

        def chip(field: str) -> ToggleChip | None:
            t = TOGGLES[field]
            if t.on_cmd not in self._cmds or t.off_cmd not in self._cmds:
                return None
            ch = ToggleChip()
            ch.activated.connect(lambda f=field: self._toggle(f))
            self._chips[field] = ch
            return ch

        def row(card: StatusCard, *ws: QWidget | None) -> None:
            ws2 = [w for w in ws if w is not None]
            if ws2:
                card.add_row(*ws2)

        row(
            self.charge_card,
            editor("set_charge_limit", "slider"),
            editor("set_charge_amps", "spin"),
        )
        row(self.charge_card, chip("charging"), chip("charge_port_open"))
        row(
            self.charge_card,
            editor("set_scheduled_charging", "time"),
            editor("set_scheduled_departure", "time"),
        )
        row(self.climate_card, editor("set_temp", "spin"), chip("climate_on"))
        row(self.security_card, chip("locked"), chip("sentry_on"))
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

    def _toggle(self, field: str) -> None:
        if self._state is None:
            return
        cmd = toggle_command(field, getattr(self._state, field, None))
        self.command_requested.emit(self._cmds[cmd], None)

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
            f"Limit {s.charge_limit} %",
            s.charge_limit,
            s.charge_limit is not None,
        )
        amps = s.charge_current_request_a
        put("set_charge_amps", f"{amps} A", amps, amps is not None)
        if amps is not None and "set_charge_amps" in self._editors:
            lo, hi, step, unit = display_range(self._info("set_charge_amps"), u)
            hi = s.charge_current_request_max_a or hi
            self._editors["set_charge_amps"].configure(lo, hi, step, unit)
        if s.target_temp_c is not None:
            t = fmt.temp_to_display(s.target_temp_c, u)
            put("set_temp", f"Target {fmt.temp(s.target_temp_c, u)}", t)
        mode = s.scheduled_charging_mode
        start = minutes_of(s.scheduled_charging_start)
        put(
            "set_scheduled_charging",
            f"Charge at {hhmm(start)}" if start is not None else "Scheduled charging off",
            start,
        )
        dep = minutes_of(s.scheduled_departure) if mode != "Off" else None
        put(
            "set_scheduled_departure",
            f"Depart by {hhmm(dep)}" if dep is not None else "Departure off",
            dep,
        )
        for field, ch in self._chips.items():
            value = getattr(s, field, None)
            target = toggle_command(field, value)
            action = self._infos[self._cmds[target]].label.lower()
            ch.set_state(toggle_text(field, value), action)
            ch.setVisible(field != "charge_port_open" or value is not None)
            if field == "charging":
                ch.setVisible(bool(s.plugged_in))

    def set_location(self, text: str | None) -> None:
        """Place name or coordinates; None hides the line (location off or unknown)."""
        self.location.setVisible(bool(text))
        self.location.setText(f"Location: {text}" if text else "")

    def set_busy(self, busy: bool) -> None:
        self.refresh_btn.setEnabled(not busy)
        self.refresh_btn.setText("Refreshing…" if busy else "&Refresh")

    def set_units(self, units: str) -> None:
        self._units = units
        self._configure_editors()
        if self._state is not None:
            self.update_state(self._state)

    def update_state(self, s: Any) -> None:
        self._state = s
        u = self._units
        self.title.setText(s.car_name or s.vin or "Vehicle")
        charging_name = s.charging.name
        is_charging = charging_name == "CHARGING"
        cap = fmt.dist(s.rated_range_km, u)
        if is_charging:
            cap += " · charging"
        self.gauge.set_state(s.soc, s.charge_limit, cap, is_charging)

        usable = f"usable {s.usable_soc} %" if s.usable_soc is not None else ""
        ideal = f"ideal {fmt.dist(s.ideal_range_km, u)}" if s.ideal_range_km is not None else ""
        self.range_card.set(
            fmt.dist(s.rated_range_km, u), " · ".join(x for x in (usable, ideal) if x)
        )

        state_text = s.charging.value
        if s.plugged_in is False:
            value, detail = "Unplugged", ""
        elif is_charging:
            value = f"{state_text} · {fmt.power(s.charger_power_kw)}"
            bits = [
                f"{fmt.minutes(s.minutes_to_limit)} to {s.charge_limit} %" if s.charge_limit else ""
            ]
            if s.charger_voltage and s.charger_current_a:
                bits.append(f"{s.charger_voltage} V × {s.charger_current_a} A")
            if s.charge_energy_added_kwh is not None:
                bits.append(f"+{fmt.energy(s.charge_energy_added_kwh)}")
            detail = " · ".join(b for b in bits if b)
        else:
            value = f"Plugged in · {state_text}" if s.plugged_in else state_text
            detail = f"limit {s.charge_limit} %" if s.charge_limit is not None else ""
            if s.scheduled_charging_start is not None:
                detail += f" · scheduled {fmt.local_time(s.scheduled_charging_start)}"
        self.charge_card.set(value, detail)

        climate = (
            ""
            if "climate_on" in self._chips
            else (" · climate on" if s.climate_on else " · climate off")
        )
        self.climate_card.set(
            f"{fmt.temp(s.inside_temp_c, u)} inside",
            f"{fmt.temp(s.outside_temp_c, u)} outside{climate}",
        )

        lock = {True: "Locked", False: "UNLOCKED", None: "Lock state unknown"}[s.locked]
        open_bits = []
        if s.doors_open:
            open_bits.append(f"open: {_names(s.doors_open)}")
        if s.windows_open:
            open_bits.append(f"windows: {_names(s.windows_open)}")
        if not open_bits:
            open_bits.append("all doors and windows closed")
        if s.sentry_on is not None and "sentry_on" not in self._chips:
            open_bits.append("sentry on" if s.sentry_on else "sentry off")
        self.security_card.set(lock, " · ".join(open_bits))

        self.odo_card.set(fmt.dist(s.odometer_km, u), "")
        shift = s.shift.value
        veh = f"Shift {shift}"
        if s.speed_kmh:
            veh += f" · {fmt.speed(s.speed_kmh, u)}"
        self.vehicle_card.set(veh, f"firmware {s.firmware}" if s.firmware else "")
        self._update_controls(s)
        self.update_age()

    def update_age(self) -> None:
        s = self._state
        if s is None:
            return
        ts = s.ts if s.ts.tzinfo else s.ts.replace(tzinfo=UTC)
        old = (datetime.now(UTC) - ts).total_seconds() > STALE_SECONDS
        src = s.source.value.upper()
        self.chip.set_chip(f"{src} · {fmt.age(ts)}", "warn" if old else "normal")
        self.chip.setToolTip(f"Read at {fmt.local_time(ts, with_date=True)}")
        self.chip.setAccessibleName(f"Data source {src}, {fmt.age(ts)}")

    def focus_default(self) -> None:
        self.refresh_btn.setFocus(Qt.FocusReason.OtherFocusReason)
