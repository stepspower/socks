# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Controls tab: one button per Command in ``COMMANDS``.

This tab only *requests* commands (``command_requested``); the main window
owns the confirm gate and the execution, so there is exactly one path to the
car.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from socks.gui import fmt
from socks.gui.widgets.colors import ok, warn

GROUPS: dict[str, tuple[str, ...]] = {
    "Security": ("lock", "unlock", "sentry_on", "sentry_off"),
    "Climate": ("climate_on", "climate_off", "set_temp"),
    "Charging": (
        "charge_start",
        "charge_stop",
        "set_charge_limit",
        "charge_port_open",
        "charge_port_close",
    ),
    "Other": ("flash_lights", "honk", "frunk", "trunk", "wake"),
}


class ControlsTab(QWidget):
    #: (command, argument or None)
    command_requested = Signal(object, object)

    def __init__(self, commands: dict[Any, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._units = "metric"
        self._temp_ready = False
        self._buttons: dict[Any, QPushButton] = {}
        self.result = QLabel(
            "Every command asks for confirmation before anything is sent to the car."
        )
        self.result.setWordWrap(True)
        self.result.setTextFormat(Qt.TextFormat.PlainText)  # may hold the car's refusal reason
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.result.setAccessibleName("Last command result")

        self.temp = QDoubleSpinBox()
        self.temp.setDecimals(1)
        self.temp.setSingleStep(0.5)
        self.temp.setAccessibleName("Target temperature")
        self.limit = QSlider(Qt.Orientation.Horizontal)
        self.limit.setRange(50, 100)
        self.limit.setSingleStep(1)
        self.limit.setPageStep(5)
        self.limit.setValue(80)
        self.limit.setAccessibleName("Charge limit")
        self.limit_label = QLabel("80 %")
        self.limit_label.setMinimumWidth(
            self.limit_label.fontMetrics().horizontalAdvance("100 %") + 4
        )
        self.limit.valueChanged.connect(lambda v: self.limit_label.setText(f"{v} %"))
        self.set_units("metric")

        by_value = {getattr(c, "value", str(c)): c for c in commands}
        placed: set[Any] = set()
        lay = QVBoxLayout(self)
        lay.addWidget(self.result)
        grid = QGridLayout()
        lay.addLayout(grid)
        groups = dict(GROUPS)
        leftovers = tuple(v for v in by_value if all(v not in g for g in GROUPS.values()))
        if leftovers:
            groups["More"] = leftovers
        for gi, (title, names) in enumerate(groups.items()):
            box = QGroupBox(title)
            bl = QVBoxLayout(box)
            for name in names:
                cmd = by_value.get(name)
                if cmd is None:
                    continue
                info = commands[cmd]
                placed.add(cmd)
                btn = QPushButton(info.label + ("…" if info.dangerous else ""))
                btn.setAccessibleName(info.label)
                if info.dangerous:
                    btn.setToolTip("Requires typed confirmation")
                btn.clicked.connect(lambda _c=False, c=cmd: self._request(c))
                self._buttons[cmd] = btn
                if info.arg == "temp_c":
                    row = QHBoxLayout()
                    row.addWidget(self.temp)
                    row.addWidget(btn, 1)
                    bl.addLayout(row)
                elif info.arg == "percent":
                    row = QHBoxLayout()
                    row.addWidget(self.limit, 1)
                    row.addWidget(self.limit_label)
                    bl.addLayout(row)
                    bl.addWidget(btn)
                else:
                    bl.addWidget(btn)
            bl.addStretch(1)
            grid.addWidget(box, gi // 2, gi % 2)
        lay.addStretch(1)
        self._commands = commands
        if placed != set(commands):
            raise RuntimeError("every command must have a button")

    def _request(self, cmd: Any) -> None:
        info = self._commands[cmd]
        arg: float | int | None = None
        if info.arg == "temp_c":
            arg = round(fmt.temp_from_display(self.temp.value(), self._units), 1)
        elif info.arg == "percent":
            arg = int(self.limit.value())
        self.command_requested.emit(cmd, arg)

    def describe_arg(self, cmd: Any, arg: float | int | None) -> str:
        info = self._commands[cmd]
        if info.arg == "temp_c" and arg is not None:
            return f"Target: {fmt.temp(float(arg), self._units)}"
        if info.arg == "percent" and arg is not None:
            return f"Charge limit: {arg} %"
        return ""

    def set_units(self, units: str) -> None:
        current_c = (
            fmt.temp_from_display(self.temp.value(), self._units) if self._temp_ready else 20.5
        )
        self._temp_ready = True
        self._units = units
        lo, hi = fmt.temp_to_display(15.0, units), fmt.temp_to_display(28.0, units)
        self.temp.setRange(lo, hi)
        self.temp.setSuffix(" °F" if fmt.imperial(units) else " °C")
        self.temp.setValue(fmt.temp_to_display(current_c, units))

    def update_state(self, s: Any) -> None:
        """Sync the argument widgets with the car (unless the user is editing)."""
        if s.target_temp_c is not None and not self.temp.hasFocus():
            self.temp.setValue(fmt.temp_to_display(s.target_temp_c, self._units))
        if (
            s.charge_limit is not None
            and not self.limit.isSliderDown()
            and not self.limit.hasFocus()
        ):
            self.limit.setValue(int(s.charge_limit))

    def set_busy(self, cmd: Any | None) -> None:
        for c, b in self._buttons.items():
            b.setEnabled(cmd is None)
            if c == cmd:
                b.setText(f"{self._commands[c].label} — sending…")
            else:
                info = self._commands[c]
                b.setText(info.label + ("…" if info.dangerous else ""))

    def show_result(self, text: str, success: bool) -> None:
        pal = self.result.palette()
        pal.setColor(self.result.foregroundRole(), ok(pal) if success else warn(pal))
        self.result.setPalette(pal)
        self.result.setText(("✓ " if success else "✗ ") + text)
