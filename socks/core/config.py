# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Settings: XDG paths and a small TOML settings file (read with tomllib, tiny writer).

The settings file holds no secrets, but it lives in the 0700 config dir next to the key and
is written 0600 anyway.
"""

from __future__ import annotations

import logging
import math
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from socks.core.keys import validate_vin, write_secret_file

log = logging.getLogger(__name__)

APP_NAME = "socks"
UNITS = ("metric", "imperial")
SOURCES = ("ble", "cloud")
REGIONS = ("eu", "na", "cn")
MIN_POLL_BLE = 60  # seconds, polling over Bluetooth only
MIN_POLL_CLOUD = 600  # seconds, when the (paid) cloud is enabled
MIN_TRIP_WATCH_INTERVAL = 300  # seconds between cloud polls while a trip is watched


@dataclass(frozen=True)
class AppPaths:
    config: Path  # $XDG_CONFIG_HOME/socks  (0700): settings, key, tokens
    data: Path  # $XDG_DATA_HOME/socks: history.sqlite
    cache: Path  # $XDG_CACHE_HOME/socks

    @property
    def settings_file(self) -> Path:
        return self.config / "settings.toml"

    @property
    def key_file(self) -> Path:
        return self.config / "private_key.pem"

    @property
    def public_key_file(self) -> Path:
        return self.config / "public_key.pem"

    @property
    def history_db(self) -> Path:
        return self.data / "history.sqlite"


def paths() -> AppPaths:
    """Resolve the XDG directories (not created here)."""
    import platformdirs

    return AppPaths(
        config=platformdirs.user_config_path(APP_NAME, appauthor=False),
        data=platformdirs.user_data_path(APP_NAME, appauthor=False),
        cache=platformdirs.user_cache_path(APP_NAME, appauthor=False),
    )


@dataclass
class Settings:
    vin: str = ""
    key_path: Path = field(default_factory=lambda: paths().key_file)
    units: str = "metric"  # metric | imperial
    prefer: str = "ble"  # ble | cloud
    cloud_enabled: bool = False
    client_id: str = ""
    region: str = "eu"  # eu | na | cn
    poll_seconds: int = 0  # 0 = off, else >= min_poll_seconds() (60 s; 600 s with cloud)
    tariff_eur_per_kwh: float = 0.0
    currency: str = "EUR"
    location_enabled: bool = True  # read the GPS fix (shown only where the user opts in)
    trip_watch_enabled: bool = False  # cloud-poll while the car is away (costs money)
    trip_watch_interval_seconds: int = 600  # >= MIN_TRIP_WATCH_INTERVAL
    geocode_enabled: bool = False  # opt-in: sends coordinates to openstreetmap.org

    @staticmethod
    def paths() -> AppPaths:
        return paths()

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        """Load settings; a missing file gives defaults, bad values fall back per field."""
        path = path or paths().settings_file
        defaults = cls()
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except FileNotFoundError:
            return defaults
        except (OSError, tomllib.TOMLDecodeError) as e:
            log.warning("ignoring unreadable settings file %s: %s", path, e)
            return defaults
        values: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            try:
                values[f.name] = _coerce(f.name, data[f.name], getattr(defaults, f.name))
            except (TypeError, ValueError) as e:
                log.warning("settings: ignoring %s: %s", f.name, e)
        settings = cls(**{**{f.name: getattr(defaults, f.name) for f in fields(cls)}, **values})
        settings.clamp_poll()
        return settings

    def min_poll_seconds(self) -> int:
        """Smallest allowed non-zero poll interval: 60 s (BLE only) or 600 s (cloud on)."""
        return MIN_POLL_CLOUD if self.cloud_enabled else MIN_POLL_BLE

    def clamp_poll(self) -> None:
        """Raise a too-short poll interval to the floor (0 = off stays 0). Audit M2: polling
        must neither keep the car awake nor run up Fleet API bills."""
        floor = self.min_poll_seconds()
        if 0 < self.poll_seconds < floor:
            log.warning("settings: poll_seconds %d raised to %d", self.poll_seconds, floor)
            self.poll_seconds = floor
        if self.trip_watch_interval_seconds < MIN_TRIP_WATCH_INTERVAL:
            log.warning(
                "settings: trip_watch_interval_seconds %d raised to %d",
                self.trip_watch_interval_seconds,
                MIN_TRIP_WATCH_INTERVAL,
            )
            self.trip_watch_interval_seconds = MIN_TRIP_WATCH_INTERVAL

    def save(self, path: Path | None = None) -> None:
        """Write the settings file atomically (0600, dir 0700)."""
        path = path or paths().settings_file
        self.clamp_poll()
        lines = ["# SOCKS settings (written by SOCKS; comments are not preserved)"]
        for f in fields(self):
            lines.append(f"{f.name} = {_toml_value(getattr(self, f.name))}")
        write_secret_file(path, ("\n".join(lines) + "\n").encode("utf-8"))


def _coerce(name: str, value: Any, default: Any) -> Any:
    """Type-check one loaded value against the default's type and the allowed choices."""
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise TypeError("expected true/false")
        return value
    if isinstance(default, Path):
        if not isinstance(value, str) or not value:
            raise TypeError("expected a path string")
        return Path(value).expanduser()
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("expected an integer")
        if value < 0:
            raise ValueError("must be >= 0")
        return value
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError("expected a number")
        if not math.isfinite(value) or value < 0:
            raise ValueError("must be a finite number >= 0")
        return float(value)
    if not isinstance(value, str):
        raise TypeError("expected a string")
    if name == "vin" and value:
        return validate_vin(value)
    choices = {"units": UNITS, "prefer": SOURCES, "region": REGIONS}.get(name)
    if choices and value not in choices:
        raise ValueError(f"must be one of {', '.join(choices)}")
    return value


def _toml_value(value: Any) -> str:
    """Serialise the few scalar types Settings uses as TOML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    named = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}
    out = []
    for ch in str(value):
        if ch in named:
            out.append(named[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'
