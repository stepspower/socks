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
from socks.gui.widgets.gauge import Gauge
from socks.gui.widgets.status_card import Chip, StatusCard

STALE_SECONDS = 15 * 60


def _names(items: tuple[str, ...]) -> str:
    return ", ".join(i.replace("_", " ") for i in items)


class StatusTab(QWidget):
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state: Any = None
        self._units = "metric"

        self.title = QLabel("No vehicle data yet")
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

        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addLayout(body, 1)

    def set_busy(self, busy: bool) -> None:
        self.refresh_btn.setEnabled(not busy)
        self.refresh_btn.setText("Refreshing…" if busy else "&Refresh")

    def set_units(self, units: str) -> None:
        self._units = units
        if self._state is not None:
            self.update_state(self._state)

    def update_state(self, s: Any) -> None:
        self._state = s
        u = self._units
        self.title.setText(s.car_name or s.vin or "Vehicle")
        charging_name = getattr(s.charging, "name", str(s.charging))
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

        state_text = getattr(s.charging, "value", str(s.charging))
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

        climate = "climate on" if s.climate_on else "climate off"
        if s.climate_on and s.target_temp_c is not None:
            climate += f" → {fmt.temp(s.target_temp_c, u)}"
        self.climate_card.set(
            f"{fmt.temp(s.inside_temp_c, u)} inside",
            f"{fmt.temp(s.outside_temp_c, u)} outside · {climate}",
        )

        lock = {True: "Locked", False: "UNLOCKED", None: "Lock state unknown"}[s.locked]
        open_bits = []
        if s.doors_open:
            open_bits.append(f"open: {_names(s.doors_open)}")
        if s.windows_open:
            open_bits.append(f"windows: {_names(s.windows_open)}")
        if not open_bits:
            open_bits.append("all doors and windows closed")
        if s.sentry_on is not None:
            open_bits.append("sentry on" if s.sentry_on else "sentry off")
        self.security_card.set(lock, " · ".join(open_bits))

        self.odo_card.set(fmt.dist(s.odometer_km, u), "")
        shift = getattr(s.shift, "value", "?")
        veh = f"Shift {shift}"
        if s.speed_kmh:
            veh += f" · {fmt.speed(s.speed_kmh, u)}"
        self.vehicle_card.set(veh, f"firmware {s.firmware}" if s.firmware else "")
        self.update_age()

    def update_age(self) -> None:
        s = self._state
        if s is None:
            return
        ts = s.ts if s.ts.tzinfo else s.ts.replace(tzinfo=UTC)
        old = (datetime.now(UTC) - ts).total_seconds() > STALE_SECONDS
        src = getattr(s.source, "value", str(s.source)).upper()
        self.chip.set_chip(f"{src} · {fmt.age(ts)}", "warn" if old else "normal")
        self.chip.setToolTip(f"Read at {fmt.local_time(ts, with_date=True)}")
        self.chip.setAccessibleName(f"Data source {src}, {fmt.age(ts)}")

    def focus_default(self) -> None:
        self.refresh_btn.setFocus(Qt.FocusReason.OtherFocusReason)
