# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Qt GUI for SOCKS. ``main()`` imports Qt lazily."""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    from socks.gui.app import main as _main

    return _main(argv)
