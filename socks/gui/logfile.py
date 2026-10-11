# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Rotating log file so failures can be diagnosed afterwards.

``$XDG_STATE_HOME/socks/socks.log`` (dir 0700, file 0600), 1 MB × 3 backups. INFO by
default, DEBUG with ``--debug``; the ``tesla_fleet_api`` and ``bleak`` loggers are added at
WARNING (DEBUG with ``--debug``). Every record and traceback passes a redaction step that
removes tokens, secrets and PEM private keys.
"""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_BYTES = 1_000_000
BACKUPS = 3
LIBRARY_LOGGERS = ("tesla_fleet_api", "bleak")
REDACTED = "[REDACTED]"

_PATTERNS = (
    # PEM private keys (whole block, or a dangling header)
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S
    ),
    # Authorization headers
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    # key=value / "key": "value" for token / secret / password-like names
    re.compile(
        r"(?i)(\b[\w-]*(?:token|secret|password|passwd|api[_-]?key|authorization|code_verifier)"
        r"[\w-]*[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;&}]+)"
    ),
    # bare JWTs (three base64url parts starting with eyJ)
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),
)


def redact(text: str) -> str:
    out = _PATTERNS[0].sub(REDACTED, text)
    out = _PATTERNS[1].sub(lambda m: f"{m.group(1)} {REDACTED}", out)
    out = _PATTERNS[2].sub(lambda m: f"{m.group(1)}{REDACTED}", out)
    return _PATTERNS[3].sub(REDACTED, out)


class RedactFilter(logging.Filter):
    """Rewrites the record's message with secrets removed (args are folded in)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not lose the record
            msg = str(record.msg)
        record.msg, record.args = redact(msg), None
        return True


class RedactingFormatter(logging.Formatter):
    """Also redacts tracebacks and stack info, which the filter cannot see."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def default_dir() -> Path:
    from platformdirs import user_state_path

    return user_state_path("socks", appauthor=False)


def install(debug: bool, log_dir: Path | None = None) -> RotatingFileHandler:
    """Attach the rotating file handler to ``socks`` and the library loggers."""
    log_dir = log_dir or default_dir()
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
    path = log_dir / "socks.log"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)
    handler = _PrivateRotatingHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
    )
    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactFilter())
    logging.getLogger("socks").addHandler(handler)
    for name in LIBRARY_LOGGERS:
        lib = logging.getLogger(name)
        lib.setLevel(logging.DEBUG if debug else logging.WARNING)
        lib.addHandler(handler)
    return handler


def uninstall(handler: logging.Handler) -> None:
    for name in ("socks", *LIBRARY_LOGGERS):
        logging.getLogger(name).removeHandler(handler)
    handler.close()


class _PrivateRotatingHandler(RotatingFileHandler):
    """Rotated files are created 0600 too."""

    def _open(self):  # type: ignore[no-untyped-def]  # noqa: ANN202
        stream = super()._open()
        os.chmod(self.baseFilename, 0o600)
        return stream
