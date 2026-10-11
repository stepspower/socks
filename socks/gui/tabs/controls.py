# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Controls tab: a state-aware panel.

Toggles are slide switches showing what the car last reported; the risky direction
(unlock, sentry off, open charge port / frunk / trunk) needs a full slide or Enter/Space
held ~1 s, which *is* the confirmation. Numeric values are inline editors prefilled from
the state (✓/Enter applies, Esc cancels). One-shot actions are buttons with a result line
next to them. Nothing here talks to the car: it emits ``command_requested`` and the main
window sends it.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget

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
from socks.gui.labels import command_label
from socks.gui.widgets.colors import muted, ok, warn
from socks.gui.widgets.inline import InlineEdit
from socks.gui.widgets.slide_switch import SlideSwitch
from socks.gui.widgets.status_card import Section

RESULT_MS = 8000

# (section title, [("switch", field) | ("edit", command, kind, label) | ("action", command)])
LAYOUT: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] = (
    (
        "Security",
        (("switch", "locked"), ("switch", "sentry_on")),
    ),
    (
        "Climate",
        (("switch", "climate_on"), ("edit", "set_temp", "spin", "Cabin temperature")),
    ),
    (
        "Charging",
        (
            ("switch", "charging"),
            ("edit", "set_charge_limit", "slider", "Charge limit"),
            ("edit", "set_charge_amps", "spin", "Charging current"),
            ("edit", "set_scheduled_charging", "time", "Start charging at"),
            ("edit", "set_scheduled_departure", "time", "Departure time"),
        ),
    ),
    (
        "Access",
        (("switch", "charge_port_open"), ("switch", "frunk"), ("switch", "trunk")),
    ),
    (
        "Misc",
        (("action", "flash_lights"), ("action", "honk"), ("action", "wake")),
    ),
)
_TR = (  # for lupdate
    QT_TRANSLATE_NOOP("ControlsTab", "Security"),
    QT_TRANSLATE_NOOP("ControlsTab", "Climate"),
    QT_TRANSLATE_NOOP("ControlsTab", "Charging"),
    QT_TRANSLATE_NOOP("ControlsTab", "Access"),
    QT_TRANSLATE_NOOP("ControlsTab", "Misc"),
    QT_TRANSLATE_NOOP("ControlsTab", "Cabin temperature"),
    QT_TRANSLATE_NOOP("ControlsTab", "Charge limit"),
    QT_TRANSLATE_NOOP("ControlsTab", "Charging current"),
    QT_TRANSLATE_NOOP("ControlsTab", "Start charging at"),
    QT_TRANSLATE_NOOP("ControlsTab", "Departure time"),
)


def _tr(text: str) -> str:
    return QCoreApplication.translate("ControlsTab", text)


def used_commands() -> set[str]:
    """Command values the fixed layout covers (switch directions included)."""
    out: set[str] = set()
    for _title, items in LAYOUT:
        for item in items:
            if item[0] == "switch":
                sp = SWITCHES[item[1]]
                out |= {sp.on_cmd} | ({sp.off_cmd} if sp.off_cmd else set())
            else:
                out.add(item[1])
    return out


class ControlsTab(QWidget):
    #: (Command, arg) — the gesture/✓ already confirmed it; the window just sends it
    command_requested = Signal(object, object)

    def __init__(self, commands: dict[Any, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._units = "metric"
        self._state: Any = None
        self._infos = commands
        self._cmds = {c.value: c for c in commands}
        self.switches: dict[str, SlideSwitch] = {}
        self.editors: dict[str, InlineEdit] = {}
        self.actions: dict[str, tuple[QPushButton, QLabel]] = {}

        self.waiting = QLabel(self.tr("Waiting for the first read from the car…"))
        self.result = self._result_label()
        self.result.setAccessibleName(self.tr("Last command result"))

        grid = QGridLayout()
        grid.setSpacing(12)
        extra = sorted(
            v for v, c in self._cmds.items() if v not in used_commands() and commands[c].arg is None
        )
        layout = list(LAYOUT)
        if extra:  # anything core adds later still gets a control
            layout.append(("More", tuple(("action", v) for v in extra)))
        for i, (title, items) in enumerate(layout):
            sec = Section(_tr(title))
            form = QGridLayout()
            form.setHorizontalSpacing(12)
            form.setVerticalSpacing(8)
            row = 0
            for item in items:
                w = self._build(item)
                if w is None:
                    continue
                name, widget = w
                label = QLabel(name)
                label.setBuddy(widget)
                form.addWidget(label, row, 0)
                form.addWidget(widget, row, 1, Qt.AlignmentFlag.AlignLeft)
                row += 1
            form.setColumnStretch(1, 1)
            sec.body.addLayout(form)
            grid.addWidget(sec, i // 2, i % 2)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addWidget(self.waiting)
        lay.addWidget(self.result)
        lay.addLayout(grid)
        lay.addStretch(1)
        self._apply_state()

    # ------------------------------------------------------------- build

    def _result_label(self) -> QLabel:
        lab = QLabel("")
        lab.setTextFormat(Qt.TextFormat.PlainText)  # may hold the car's refusal reason
        lab.setWordWrap(True)
        lab.hide()
        lab._timer = QTimer(lab)  # type: ignore[attr-defined]
        lab._timer.setSingleShot(True)  # type: ignore[attr-defined]
        lab._timer.timeout.connect(lab.hide)  # type: ignore[attr-defined]
        return lab

    def _build(self, item: tuple[str, ...]) -> tuple[str, QWidget] | None:
        kind = item[0]
        if kind == "switch":
            field = item[1]
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
            self.switches[field] = sw
            return tr_switch(sp.name), sw
        cmd = item[1]
        if cmd not in self._cmds:
            return None
        info = self._infos[self._cmds[cmd]]
        if kind == "edit":
            ed = InlineEdit(item[2])
            ed.committed.connect(lambda v, c=cmd: self._commit(c, v))
            self.editors[cmd] = ed
            return _tr(item[3]), ed
        btn = QPushButton(command_label(info))
        res = self._result_label()
        btn.clicked.connect(lambda _c=False, c=cmd: self._action(c))
        box = QWidget()
        row = QGridLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(btn, 0, 0)
        row.addWidget(res, 0, 1)
        row.setColumnStretch(1, 1)
        self.actions[cmd] = (btn, res)
        return "", box

    # ----------------------------------------------------------- signals

    def _slid(self, field: str, on: bool) -> None:
        cmd = switch_command(field, on)
        if cmd is not None and cmd in self._cmds:
            self.command_requested.emit(self._cmds[cmd], None)

    def _commit(self, cmd: str, value: Any) -> None:
        try:
            arg = arg_for(self._infos[self._cmds[cmd]], value, self._units)
        except ValueError as exc:
            self.show_result(str(exc), False)
            return
        self.command_requested.emit(self._cmds[cmd], arg)

    def _action(self, cmd: str) -> None:
        self.command_requested.emit(self._cmds[cmd], None)

    # ------------------------------------------------------------- state

    def update_state(self, s: Any) -> None:
        self._state = s
        self._apply_state()

    def _apply_state(self) -> None:
        s = self._state
        self.waiting.setVisible(s is None)
        for field, sw in self.switches.items():
            sw.set_checked(switch_state(field, s))
            if s is None:
                sw.setToolTip(self.tr("Waiting for the first read"))
            elif not switch_enabled(field, s):
                sw.setEnabled(False)
                sw.setToolTip(self.tr("Plug in the cable to charge"))
        for cmd, ed in self.editors.items():
            if ed.kind != "time":
                lo, hi, step, unit = display_range(self._infos[self._cmds[cmd]], self._units)
                if cmd == "set_charge_amps" and s is not None and s.charge_current_request_max_a:
                    hi = s.charge_current_request_max_a
                ed.configure(lo, hi, step, "" if ed.kind == "slider" else unit)
            val = editor_value(cmd, s, self._units) if s is not None else None
            ed.setEnabled(val is not None)
            if not ed.editing():
                ed.set_value(val[0] if val else "–", val[1] if val else None)
        for btn, _res in self.actions.values():
            btn.setEnabled(s is not None)

    def set_units(self, units: str) -> None:
        self._units = units
        self._apply_state()

    def set_busy(self, cmd: Any | None) -> None:
        busy = cmd is not None
        for w in (*self.switches.values(), *self.editors.values()):
            w.setEnabled(not busy)
        for btn, _res in self.actions.values():
            btn.setEnabled(not busy and self._state is not None)
        if not busy:
            self._apply_state()

    def revert_switches(self) -> None:
        for sw in self.switches.values():
            sw.revert()

    def show_result(self, text: str, success: bool, cmd: Any = None) -> None:
        """Short inline line; next to the action button for one-shot commands. Clears."""
        value = getattr(cmd, "value", None)
        lab = self.actions[value][1] if value in self.actions else self.result
        pal = lab.palette()
        col = muted(pal) if success is None else (ok(pal) if success else warn(pal))
        pal.setColor(lab.foregroundRole(), col)
        lab.setPalette(pal)
        lab.setText(("✓ " if success else "✗ ") + text)
        lab.show()
        lab._timer.start(RESULT_MS)  # type: ignore[attr-defined]
