# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Shared test setup.

* faulthandler is on, so a crash (e.g. in Qt teardown on CI) prints a traceback.
* ``qapp``: ONE QApplication for the whole session. Per-module QApplications let the
  app be collected while widgets/threads still exist, which segfaulted at interpreter
  exit on CI (exit 139). At session end: stop every AsyncBridge I/O thread, close and
  delete all top-level widgets, process the deferred deletes, quit the app.
"""

from __future__ import annotations

import faulthandler
import os
import sys
from collections.abc import Iterator

import pytest

faulthandler.enable(file=sys.stderr, all_threads=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp() -> Iterator[object]:
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QCoreApplication, QEvent

    app = widgets.QApplication.instance() or widgets.QApplication([])
    yield app
    from socks.gui.async_bridge import shutdown_all

    shutdown_all()
    for w in widgets.QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    app.quit()
