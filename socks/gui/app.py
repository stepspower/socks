# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""QApplication setup and the Qt event loop (no QtAsyncio; see async_bridge).

``socks`` / ``python -m socks`` → :func:`main`. Flags:

``--fake``            use the fake car (tests/fakes.py); never touches BLE or the cloud
``--debug``           log to stderr, with tracebacks
``--measure-startup`` print process-start → first-paint time and exit
``--screenshot PATH`` (development) save a PNG of the window after a few seconds and exit

Startup does no network or Bluetooth work: the backend is created on the
first refresh, which is scheduled after the window has painted.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

APP_ID = "net.stepspower.Socks"
_T0 = time.perf_counter()


def _process_age_ms() -> float | None:
    """Milliseconds since the kernel started this process (Linux)."""
    try:
        with open("/proc/self/stat", encoding="ascii") as f:
            start_ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/uptime", encoding="ascii") as f:
            uptime = float(f.read().split()[0])
        return (uptime - start_ticks / os.sysconf("SC_CLK_TCK")) * 1000
    except (OSError, ValueError, IndexError):
        return None


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="socks", description="SOCKS — State Of Charge, Keys & Stats")
    p.add_argument(
        "--fake", action="store_true", help="use a simulated car (no Bluetooth, no cloud)"
    )
    p.add_argument("--debug", action="store_true", help="log to stderr, including tracebacks")
    p.add_argument(
        "--measure-startup", action="store_true", help="print time to first paint and exit"
    )
    p.add_argument("--screenshot", metavar="PATH", help=argparse.SUPPRESS)
    p.add_argument("--tab", type=int, default=0, help=argparse.SUPPRESS)
    # Qt's own options (-platform, -style …) are consumed by QApplication.
    args, _rest = p.parse_known_args(argv)
    return args


def _setup_logging(debug: bool) -> logging.Handler:
    from socks.gui.widgets.log_view import QtLogHandler

    root = logging.getLogger("socks")
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    handler = QtLogHandler()
    root.addHandler(handler)
    if debug:
        err = logging.StreamHandler(sys.stderr)
        err.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        err.setLevel(logging.DEBUG)
        root.addHandler(err)
    root.propagate = False

    gui_log = logging.getLogger("socks.gui")

    def excepthook(tp: type[BaseException], exc: BaseException, tb: object) -> None:
        from socks.gui.async_bridge import describe

        gui_log.error(
            "unexpected error: %s", describe(exc), exc_info=(tp, exc, tb) if debug else None
        )

    sys.excepthook = excepthook  # Qt slot exceptions land here; no tracebacks in the UI
    return handler


def icon_path() -> Path | None:
    here = Path(__file__).resolve()
    for cand in (here.parents[2] / "data" / "icons" / "socks.svg", here.parent / "socks.svg"):
        if cand.is_file():
            return cand
    return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QTimer
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    QApplication.setApplicationName("socks")
    QApplication.setApplicationDisplayName("SOCKS")
    QApplication.setOrganizationName("stepspower")
    QApplication.setOrganizationDomain("stepspower.net")
    QGuiApplication.setDesktopFileName(APP_ID)  # Wayland app_id / X11 WM_CLASS hint
    from socks import __version__

    QApplication.setApplicationVersion(__version__)
    # argv[0] "socks" → WM_CLASS "socks", whatever launched us (python -m, entry point).
    app = QApplication(["socks", *sys.argv[1:]])
    app.setQuitOnLastWindowClosed(False)  # the window decides: tray or quit

    handler = _setup_logging(args.debug)
    ip = icon_path()
    icon = QIcon(str(ip)) if ip else QIcon.fromTheme(APP_ID)
    app.setWindowIcon(icon)

    from socks.gui._deps import deps
    from socks.gui.main_window import AppContext, MainWindow

    d = deps()
    extras: dict[str, object] = {}
    if args.fake:
        # Fake mode never reads or writes the real settings: throwaway config dir.
        cfg_dir = Path(tempfile.mkdtemp(prefix="socks-fake-cfg-"))
        settings_path = cfg_dir / "settings.toml"
        extras["settings_path"] = settings_path
        extras["fake_cfg_dir"] = cfg_dir
        try:
            settings = d.config.Settings.load(settings_path)
        except TypeError:  # dev shim: load() takes no path and never writes
            settings = d.config.Settings.load()
    else:
        settings = d.config.Settings.load()
        cfg_dir = _config_dir(d)
    prefs = QSettings(str(cfg_dir / "gui.ini"), QSettings.Format.IniFormat)
    ctx = AppContext(d, settings, args.fake, args.debug, icon, handler, prefs, extras)
    win = MainWindow(ctx)
    win.tabs.setCurrentIndex(max(0, min(3, args.tab)))

    class FirstPaint(QObject):
        done = False

        def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
            if ev.type() == QEvent.Type.Paint and not self.done:
                self.done = True
                _first_paint(args, win)
            return False

    fp = FirstPaint(win)
    win.installEventFilter(fp)
    win.tabs.currentWidget().installEventFilter(fp)
    win.show()

    if args.screenshot:
        QTimer.singleShot(3500, lambda: _screenshot(win, args.screenshot))

    app.aboutToQuit.connect(win.quit_app)

    # Ctrl+C in a terminal: let Python see the signal now and then, quit cleanly.
    signal.signal(signal.SIGINT, lambda *_a: QTimer.singleShot(0, win.quit_app))
    tick = QTimer()
    tick.start(300)
    tick.timeout.connect(lambda: None)

    rc = app.exec()
    win.quit_app()  # idempotent; covers quit paths that bypassed it
    return int(rc)


def _config_dir(d: object) -> Path:
    cfg = getattr(d, "config", None)
    paths_fn = getattr(cfg, "paths", None)
    if paths_fn is not None:
        return Path(paths_fn().config)
    from platformdirs import user_config_path

    return user_config_path("socks", appauthor=False)


def _first_paint(args: argparse.Namespace, win: object) -> None:
    from PySide6.QtCore import QTimer

    if args.measure_startup:
        since_main = (time.perf_counter() - _T0) * 1000
        age = _process_age_ms()
        total = f"{age:.0f} ms" if age is not None else "n/a"
        print(f"first paint: {total} after process start ({since_main:.0f} ms after app import)")
        sys.stdout.flush()
        QTimer.singleShot(0, win.quit_app)  # type: ignore[attr-defined]
        return
    # The first read happens only after the window is on screen. With the cloud
    # preferred we wait for an explicit Refresh (each read costs money).
    settings = win.ctx.settings  # type: ignore[attr-defined]
    if args.fake or getattr(settings, "prefer", "ble") == "ble":
        QTimer.singleShot(0, win.refresh)  # type: ignore[attr-defined]


def _screenshot(win: object, path: str) -> None:
    from PySide6.QtCore import QTimer

    pix = win.grab()  # type: ignore[attr-defined]
    ok = pix.save(path, "PNG")
    logging.getLogger("socks.gui").info("screenshot %s: %s", path, "saved" if ok else "FAILED")
    print(f"screenshot {'saved to' if ok else 'FAILED for'} {path}")
    QTimer.singleShot(0, win.quit_app)  # type: ignore[attr-defined]


if __name__ == "__main__":
    sys.exit(main())
