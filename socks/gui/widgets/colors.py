# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Colours derived from the active QPalette (never hard-coded)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette


def accent(pal: QPalette) -> QColor:
    return QColor(pal.color(QPalette.ColorRole.Highlight))


def _hue_shift(pal: QPalette, hue: int) -> QColor:
    base = accent(pal)
    sat = max(140, base.hslSaturation())
    light = base.lightness()
    light = max(90, min(170, light))
    return QColor.fromHsl(hue, sat, light)


def warn(pal: QPalette) -> QColor:
    """Accent rotated to red, keeping the theme's saturation/lightness."""
    return _hue_shift(pal, 4)


def ok(pal: QPalette) -> QColor:
    """Accent rotated to green (charging, success)."""
    return _hue_shift(pal, 130)


def muted(pal: QPalette) -> QColor:
    return QColor(pal.color(QPalette.ColorRole.PlaceholderText))
