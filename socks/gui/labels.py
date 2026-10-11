# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Translated display labels for core commands.

Core's CommandInfo.label is English; the GUI shows ``command_label(info)``. Two labels
are reworded so the UI never talks about "schedules" (the Depart-by card covers that).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

CONTEXT = "Commands"
OVERRIDES = {
    "set_scheduled_charging": QT_TRANSLATE_NOOP("Commands", "Start charging at"),
    "set_scheduled_departure": QT_TRANSLATE_NOOP("Commands", "Departure time"),
}
# Literal copies of core's labels so lupdate extracts them.
_CORE_LABELS = (
    QT_TRANSLATE_NOOP("Commands", "Lock"),
    QT_TRANSLATE_NOOP("Commands", "Unlock"),
    QT_TRANSLATE_NOOP("Commands", "Climate on"),
    QT_TRANSLATE_NOOP("Commands", "Climate off"),
    QT_TRANSLATE_NOOP("Commands", "Set temperature"),
    QT_TRANSLATE_NOOP("Commands", "Start charging"),
    QT_TRANSLATE_NOOP("Commands", "Stop charging"),
    QT_TRANSLATE_NOOP("Commands", "Set charge limit"),
    QT_TRANSLATE_NOOP("Commands", "Set charging current"),
    QT_TRANSLATE_NOOP("Commands", "Open charge port"),
    QT_TRANSLATE_NOOP("Commands", "Close charge port"),
    QT_TRANSLATE_NOOP("Commands", "Flash lights"),
    QT_TRANSLATE_NOOP("Commands", "Honk"),
    QT_TRANSLATE_NOOP("Commands", "Open frunk"),
    QT_TRANSLATE_NOOP("Commands", "Open/close trunk"),
    QT_TRANSLATE_NOOP("Commands", "Sentry mode on"),
    QT_TRANSLATE_NOOP("Commands", "Sentry mode off"),
    QT_TRANSLATE_NOOP("Commands", "Wake up"),
)


def command_label(info: Any) -> str:
    text = OVERRIDES.get(info.command.value, info.label)
    return QCoreApplication.translate(CONTEXT, text)
