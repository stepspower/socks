# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Rotating log file: installed with private modes, levels, and secret redaction."""

from __future__ import annotations

import logging
import stat
from pathlib import Path

from socks.gui import logfile
from socks.gui.logfile import REDACTED, redact


def test_install_creates_private_file_and_attaches(tmp_path: Path) -> None:
    d = tmp_path / "state" / "socks"
    logging.getLogger("socks").setLevel(logging.INFO)  # as app._setup_logging does
    h = logfile.install(debug=False, log_dir=d)
    try:
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
        path = Path(h.baseFilename)
        assert path == d / "socks.log" and stat.S_IMODE(path.stat().st_mode) == 0o600
        assert h.maxBytes == 1_000_000 and h.backupCount == 3 and h.level == logging.INFO
        assert h in logging.getLogger("socks").handlers
        for name in ("tesla_fleet_api", "bleak"):
            assert h in logging.getLogger(name).handlers
            assert logging.getLogger(name).level == logging.WARNING
        logging.getLogger("socks.gui").info("client_secret=hunter2 for login")
        h.flush()
        text = path.read_text()
        assert "client_secret=" + REDACTED in text and "hunter2" not in text
    finally:
        logfile.uninstall(h)


def test_debug_levels(tmp_path: Path) -> None:
    h = logfile.install(debug=True, log_dir=tmp_path)
    try:
        assert h.level == logging.DEBUG
        assert logging.getLogger("bleak").level == logging.DEBUG
    finally:
        logfile.uninstall(h)
        for name in ("tesla_fleet_api", "bleak"):
            logging.getLogger(name).setLevel(logging.NOTSET)


def test_redaction_patterns() -> None:
    pem = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIabc\n-----END EC PRIVATE KEY-----"
    assert redact(f"key: {pem} done") == f"key: {REDACTED} done"
    assert "MHcC" not in redact("-----BEGIN PRIVATE KEY-----\nMHcCAQEE truncated")
    assert "abcdefghijklmnop" not in redact("Authorization: Bearer abcdefghijklmnop")
    assert redact('{"access_token": "eyJabc"}') == '{"access_token": "' + REDACTED + '"}'
    assert redact("refresh_token=abc123&x=1") == f"refresh_token={REDACTED}&x=1"
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJl"
    assert redact(f"got {jwt}") == f"got {REDACTED}"
    assert redact("SoC 70 %, VIN 5YJ3E7EB0KF000001") == "SoC 70 %, VIN 5YJ3E7EB0KF000001"


def test_filter_folds_args_and_formatter_redacts_tracebacks() -> None:
    rec = logging.LogRecord("socks", logging.INFO, __file__, 1, "token=%s", ("s3cr3t",), None)
    assert logfile.RedactFilter().filter(rec)
    assert rec.getMessage() == f"token={REDACTED}" and rec.args is None
    try:
        raise RuntimeError("client_secret=topsecret")
    except RuntimeError:
        import sys

        rec2 = logging.LogRecord("socks", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
    out = logfile.RedactingFormatter("%(message)s").format(rec2)
    assert "topsecret" not in out and REDACTED in out
