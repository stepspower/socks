# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""“Depart by” card on the Status tab, and its dialog.

Built on ``socks.core.depart`` (DeparturePlan, make_plan, arm, disarm, maintain); the
active plan lives in ``Settings.depart_plan``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QFont, QPaintEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from socks.core import depart
from socks.gui import fmt
from socks.gui.widgets.inline import EditableValue
from socks.gui.widgets.status_card import paint_card

# ---------------------------------------------------------------------- plan


def make_plan(
    t: time, charge_to: int, cabin_display: float, units: str, now: datetime | None = None
) -> depart.DeparturePlan:
    """Dialog values → a validated core DeparturePlan (raises ValueError if not armable)."""
    now = now or datetime.now(UTC)
    at = depart.next_occurrence(f"{t.hour:02d}:{t.minute:02d}", now)
    cabin_c = round(fmt.temp_from_display(cabin_display, units) * 2) / 2
    return depart.make_plan(
        at, int(min(100, max(50, charge_to))), float(min(28.0, max(15.0, cabin_c))), now
    )


def plan_text(plan: depart.DeparturePlan, units: str) -> tuple[str, str, str]:
    local = plan.depart_at.astimezone()
    return (
        QCoreApplication.translate("DepartCard", "Depart by {0}").format(local.strftime("%H:%M")),
        f"{plan.charge_limit} %",
        fmt.temp(plan.temp_c, units),
    )


_STEP_NAMES = {
    "set_charge_limit": QT_TRANSLATE_NOOP("DepartCard", "Charge limit"),
    "set_temp": QT_TRANSLATE_NOOP("DepartCard", "Cabin"),
    "set_scheduled_departure": QT_TRANSLATE_NOOP("DepartCard", "Departure"),
}


def _step_name(rest: str) -> tuple[str, str]:
    """Split "Set charge limit 75 %" into ("Charge limit", "75 %") using core's labels."""
    from socks.core.commands import COMMANDS, Command

    for value, name in _STEP_NAMES.items():
        label = COMMANDS[Command(value)].label
        if rest.startswith(label):
            short = QCoreApplication.translate("DepartCard", name)
            return short, rest[len(label) :].strip()
    return "", rest


def format_steps(lines: list[str]) -> tuple[str, bool]:
    """core.depart.arm result lines → one short line per step; (text, any step failed).

    "ok: Set charge limit 75 %" → "Charge limit 75 % — ok"; an "ok" line mentioning
    "already" → "— already set"; "failed: Set temperature 21 °C: <reason>" → "Cabin 21 °C —
    failed: <reason>". Notes are kept as they are. Only failed lines make it an error.
    """
    out: list[str] = []
    failed = False
    for line in lines:
        kind, _sep, rest = line.partition(":")
        rest = rest.strip()
        if kind == depart.NOTE:
            out.append(rest)
            continue
        if kind == depart.FAILED:
            failed = True
            head, _s, reason = rest.partition(": ")
            name, what = _step_name(head)
            if not name:  # plan validation / transport error without a step
                out.append(QCoreApplication.translate("DepartCard", "Not set: {0}").format(rest))
            else:
                out.append(
                    QCoreApplication.translate("DepartCard", "{0} {1} — failed: {2}").format(
                        name, what, reason
                    )
                )
            continue
        name, what = _step_name(rest)
        if "already" in what.lower():
            what = what.replace("(already set)", "").strip()
            out.append(
                QCoreApplication.translate("DepartCard", "{0} {1} — already set").format(
                    name or rest, what
                )
            )
        else:
            out.append(
                QCoreApplication.translate("DepartCard", "{0} {1} — ok")
                .format(name or rest, what)
                .strip()
            )
    return "\n".join(out), failed


def error_text(exc: BaseException) -> str:
    """A transport exception → short text; "car not reachable" only for NotAvailable."""
    from socks.core.backend import NotAvailable
    from socks.gui.async_bridge import describe

    if isinstance(exc, NotAvailable):
        return QCoreApplication.translate("DepartCard", "car not reachable")
    return describe(exc)


def result_failed(lines: list[str] | str | None) -> str | None:
    """core.depart returns "ok: …"/"failed: …" lines; the failure text, if any."""
    if lines is None:
        return None
    for line in [lines] if isinstance(lines, str) else lines:
        if line.startswith(depart.FAILED):
            return line.split(":", 1)[1].strip()
    return None


# -------------------------------------------------------------------- dialog


PRESETS_MIN = (10, 20, 30, 45, 60, 120)
PRESET_KEY = "depart_last_preset"


def relative_target(now: datetime, minutes: int) -> datetime:
    """``now`` + ``minutes``, rounded up to the next whole minute (local time)."""
    t = now + timedelta(minutes=minutes)
    if t.second or t.microsecond:
        t = t.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return t


def day_hint(hh: int, mm: int, now: datetime) -> str:
    """ "= 07:42 today" / "= 00:10 tomorrow" — the same next-occurrence rule as core.depart:
    a time of day at or before now means tomorrow."""
    local = now.astimezone()
    cand = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    tomorrow = cand <= local
    text = (
        QCoreApplication.translate("DepartDialog", "= {0} tomorrow")
        if tomorrow
        else QCoreApplication.translate("DepartDialog", "= {0} today")
    )
    return text.format(f"{hh:02d}:{mm:02d}")


class DepartDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        *,
        units: str,
        charge_to: int | None,
        plan: Any = None,
        focus: str = "time",
        prefs: Any = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Depart by"))
        self.units = units
        self.prefs = prefs
        self._now = now or (lambda: datetime.now().astimezone())
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        self.hint = QLabel("")
        self.time.timeChanged.connect(lambda _t: self._update_hint())
        # relative presets: they only fill the time field, which stays the source of truth
        self.presets = QButtonGroup(self)
        self.presets.setExclusive(True)
        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        for minutes in PRESETS_MIN:
            if minutes == PRESETS_MIN[0]:
                text = self.tr("in {0} min").format(minutes)
            elif minutes % 60 == 0:
                text = self.tr("{0} h").format(minutes // 60)
            else:
                text = str(minutes)
            b = QPushButton(text)
            b.setCheckable(True)
            b.setFlat(True)
            b.setToolTip(self.tr("Leave in {0} minutes").format(minutes))
            b.clicked.connect(lambda _c=False, m=minutes: self.apply_minutes(m))
            self.presets.addButton(b, minutes)
            preset_row.addWidget(b)
        preset_row.addStretch(1)
        self.custom = QSpinBox()
        self.custom.setRange(5, 720)
        self.custom.setValue(90)
        self.custom.setSuffix(self.tr(" minutes"))
        self.custom.setAccessibleName(self.tr("Leave in minutes"))
        custom_apply = QPushButton(self.tr("Apply"))
        custom_apply.clicked.connect(lambda: self.apply_minutes(self.custom.value()))
        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel(self.tr("in")))
        custom_row.addWidget(self.custom)
        custom_row.addWidget(custom_apply)
        custom_row.addStretch(1)
        self.charge = QSlider(Qt.Orientation.Horizontal)
        self.charge.setRange(50, 100)
        self.charge.setPageStep(5)
        self.charge_label = QLabel()
        self.charge.valueChanged.connect(lambda v: self.charge_label.setText(f"{v} %"))
        self.cabin = QDoubleSpinBox()
        lo, hi = fmt.temp_to_display(15.0, units), fmt.temp_to_display(28.0, units)
        self.cabin.setRange(lo, hi)
        self.cabin.setSingleStep(1.0 if fmt.imperial(units) else 0.5)
        self.cabin.setDecimals(0 if fmt.imperial(units) else 1)
        self.cabin.setSuffix(" °F" if fmt.imperial(units) else " °C")
        if plan is not None:
            local = plan.depart_at.astimezone()
            self.time.setTime(QTime(local.hour, local.minute))
            self.charge.setValue(int(plan.charge_limit))
            self.cabin.setValue(fmt.temp_to_display(plan.temp_c, units))
        else:
            self.time.setTime(QTime(7, 30))
            self.charge.setValue(int(charge_to or 80))
            self.cabin.setValue(fmt.temp_to_display(21.0, units))
        self.charge_label.setText(f"{self.charge.value()} %")
        charge_row = QHBoxLayout()
        charge_row.addWidget(self.charge, 1)
        charge_row.addWidget(self.charge_label)
        form = QFormLayout()
        form.addRow("", preset_row)
        form.addRow("", custom_row)
        time_row = QHBoxLayout()
        time_row.addWidget(self.time)
        time_row.addWidget(self.hint, 1)
        form.addRow(QLabel(self.tr("&Leave at:"), buddy=self.time), time_row)
        form.addRow(QLabel(self.tr("C&harge to:"), buddy=self.charge), charge_row)
        form.addRow(QLabel(self.tr("Ca&bin:"), buddy=self.cabin), self.cabin)
        note = QLabel(
            self.tr(
                "The car charges so it reaches this level and warms or cools the cabin in "
                "time for this departure. It is cleared automatically afterwards."
            )
        )
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.addButton(self.tr("Depart by"), QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(buttons)
        last = int(self.prefs.value(PRESET_KEY, 0, type=int)) if self.prefs is not None else 0
        if last:
            b = self.presets.button(last)
            if b is not None:
                b.setChecked(True)
            else:
                self.custom.setValue(last)
            if plan is None:  # a new plan starts from the remembered offset
                self.apply_minutes(last, remember=False)
        self._update_hint()
        {"time": self.time, "charge": self.charge, "cabin": self.cabin}.get(
            focus, self.time
        ).setFocus()

    def apply_minutes(self, minutes: int, remember: bool = True) -> None:
        """Set the time field to now + ``minutes`` (rounded up to a whole minute)."""
        t = relative_target(self._now(), int(minutes))
        self.time.setTime(QTime(t.hour, t.minute))
        b = self.presets.button(int(minutes))
        if b is not None:
            b.setChecked(True)
        else:
            checked = self.presets.checkedButton()
            if checked is not None:  # exclusive groups need this to clear the selection
                self.presets.setExclusive(False)
                checked.setChecked(False)
                self.presets.setExclusive(True)
        if remember and self.prefs is not None:
            self.prefs.setValue(PRESET_KEY, int(minutes))
        self._update_hint()

    def _update_hint(self) -> None:
        t = self.time.time()
        self.hint.setText(day_hint(t.hour(), t.minute(), self._now()))

    def plan(self, now: datetime | None = None) -> Any:
        t = self.time.time()
        return make_plan(
            time(t.hour(), t.minute()), self.charge.value(), self.cabin.value(), self.units, now
        )


# ---------------------------------------------------------------------- card


class DepartCard(QFrame):
    arm_requested = Signal(object)  # DeparturePlan
    disarm_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)  # flat card (painted)
        self.units = "metric"
        self.plan: Any = None
        self.prefs: Any = None  # gui.ini (QSettings), set by the main window
        self._charge_limit: int | None = None
        title = QLabel(self.tr("Departure"))
        f = QFont(self.font())
        f.setWeight(QFont.Weight.DemiBold)
        f.setPointSizeF(f.pointSizeF() * 1.15)
        title.setFont(f)
        self.button = QPushButton(self.tr("&Depart by…"))
        self.button.setToolTip(self.tr("Have the car charged and the cabin ready at a time"))
        self.button.clicked.connect(lambda: self._open("time"))
        self.when = EditableValue()
        self.when.activated.connect(lambda: self._open("time"))
        self.charge = EditableValue()
        self.charge.activated.connect(lambda: self._open("charge"))
        self.cabin = EditableValue()
        self.cabin.activated.connect(lambda: self._open("cabin"))
        self.cancel = QPushButton(self.tr("Cancel departure"))
        self.cancel.clicked.connect(self.disarm_requested)
        # Result/notes: plain text, wraps, never widens the bar (Ignored horizontal hint).
        self.status = QLabel("")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.hide()
        row = QHBoxLayout()
        row.addWidget(title)
        row.addSpacing(12)
        self._dots = [QLabel("·"), QLabel("·")]
        for w in (self.button, self.when, self._dots[0], self.charge, self._dots[1], self.cabin):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.cancel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.addLayout(row)
        lay.addWidget(self.status)
        self.set_plan(None)

    def paintEvent(self, _e: QPaintEvent) -> None:
        paint_card(self)

    def set_charge_limit(self, limit: int | None) -> None:
        self._charge_limit = limit

    def set_plan(self, plan: Any) -> None:
        self.plan = plan
        has = plan is not None
        self.button.setVisible(not has)
        for w in (self.when, self.charge, self.cabin, self.cancel, *self._dots):
            w.setVisible(has)
        if has:
            when, charge, cabin = plan_text(plan, self.units)
            self.when.setText(when)
            self.charge.setText(charge)
            self.cabin.setText(cabin)

    def set_busy(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def show_cleared(self) -> None:
        self.set_plan(None)
        self.set_busy(self.tr("Departure done — cleared"))
        QTimer.singleShot(8000, lambda: self.set_busy(""))

    def _open(self, focus: str) -> None:
        dlg = DepartDialog(
            self,
            units=self.units,
            charge_to=self._charge_limit,
            plan=self.plan,
            focus=focus,
            prefs=self.prefs,
        )
        try:
            if dlg.exec() == QDialog.DialogCode.Accepted:
                try:
                    plan = dlg.plan()
                except ValueError as exc:
                    self.set_busy(str(exc))
                    return
                self.arm_requested.emit(plan)
        finally:
            dlg.deleteLater()
