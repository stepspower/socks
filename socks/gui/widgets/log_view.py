# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""In-app log view and the logging handler that feeds it (thread-safe).

Records are shown as one line each; tracebacks are never rendered here.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QPlainTextEdit, QWidget


class _Emitter(QObject):
    line = Signal(str, int)


class QtLogHandler(logging.Handler):
    """Forwards records to the GUI thread via a queued signal."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.emitter = _Emitter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage().splitlines()[0] if record.getMessage() else ""
            ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            self.emitter.line.emit(f"{ts}  {record.levelname:<7} {msg}", record.levelno)
        except Exception:  # noqa: BLE001 - a log handler must never raise
            self.handleError(record)


class LogView(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(1000)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setAccessibleName(self.tr("Application log"))
        self.setPlaceholderText(self.tr("Nothing logged yet."))

    def append_line(self, text: str, _level: int = logging.INFO) -> None:
        self.appendPlainText(text)
