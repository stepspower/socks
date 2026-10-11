# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Resolve the core/history interfaces (PLAN §4), falling back to the dev shim.

Everything the GUI needs from other subtrees is reached through this module so
the fallback logic lives in one place. ``shimmed`` lists what came from
``_devshim`` so the log view can say so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

log = logging.getLogger("socks.gui")


@dataclass
class Deps:
    model: Any
    commands: Any
    config: Any
    backend_mod: Any | None
    stats: Any
    recorder_mod: Any | None
    history_db: Any | None
    shimmed: list[str] = field(default_factory=list)

    @property
    def enums(self) -> SimpleNamespace:
        m = self.model
        return SimpleNamespace(
            ChargingState=m.ChargingState, ShiftState=m.ShiftState, Source=m.Source
        )

    @property
    def command_error(self) -> type[Exception]:
        if self.backend_mod is not None and hasattr(self.backend_mod, "CommandError"):
            return self.backend_mod.CommandError
        return Exception


def _try(name: str) -> ModuleType | None:
    import importlib

    try:
        return importlib.import_module(name)
    except ImportError as exc:
        log.debug("import %s failed: %s", name, exc)
        return None


_deps: Deps | None = None


def deps() -> Deps:
    """Import lazily, once. Never touches the network or Bluetooth."""
    global _deps
    if _deps is not None:
        return _deps
    shim: ModuleType | None = None
    shimmed: list[str] = []

    def need_shim(what: str) -> ModuleType:
        nonlocal shim
        if shim is None:
            from socks.gui import _devshim as shim_mod

            shim = shim_mod
        shimmed.append(what)
        return shim

    model = _try("socks.core.model") or need_shim("core.model")
    commands = _try("socks.core.commands")
    if commands is None or not hasattr(commands, "COMMANDS"):
        commands = need_shim("core.commands")
    config = _try("socks.core.config")
    if config is None or not hasattr(config, "Settings"):
        config = need_shim("core.config")
    backend_mod = _try("socks.core.backend")
    stats = _try("socks.history.stats")
    history_db = _try("socks.history.db")
    recorder_mod = _try("socks.history.recorder")
    if stats is None or history_db is None or recorder_mod is None:
        stats = need_shim("history")
        history_db = recorder_mod = None
    _deps = Deps(model, commands, config, backend_mod, stats, recorder_mod, history_db, shimmed)
    return _deps


def make_fake_backend(vin: str) -> Any:
    """``core.backend.fake_backend`` (tests/fakes.py, wall-clock stamped); else the shim's."""
    b = deps().backend_mod
    factory = getattr(b, "fake_backend", None) if b is not None else None
    if factory is not None:
        try:
            return factory(start=None, delay=0.3)
        except (RuntimeError, TypeError) as exc:
            log.debug("core fake_backend unavailable: %s", exc)
    mod = _try("tests.fakes")
    cls = getattr(mod, "FakeBackend", None) if mod else None
    if cls is not None:
        try:
            return cls(start=None, delay=0.3)
        except TypeError:
            return cls()
    from socks.gui import _devshim

    deps().shimmed.append("tests.fakes")
    return _devshim.FakeBackend(vin or "5YJ3E7EB0SHIM0001")


def make_real_backend(settings: Any) -> Any | None:
    """Build the Router from settings (BLE, plus cloud when enabled and logged in).

    Called lazily on the first refresh, never before the window shows. Constructing
    it does no I/O; the first ``read_state`` does.
    """
    b = deps().backend_mod
    build = getattr(b, "build_router", None) if b is not None else None
    if build is None:
        return None
    provider = None
    if getattr(settings, "cloud_enabled", False):
        oauth = _try("socks.fleet.oauth")
        if oauth is not None and hasattr(oauth, "access_token"):

            async def provider() -> str | None:
                return await oauth.access_token(settings)

    return build(settings, provider)


def history_db_path(settings: Any) -> Path:
    cfg = deps().config
    paths_fn = getattr(cfg, "paths", None) or getattr(cfg.Settings, "paths", None)
    if paths_fn is not None:
        p = paths_fn()
        hdb = getattr(p, "history_db", None)
        if hdb is not None:
            return Path(hdb)
        for attr in ("data", "data_dir"):
            d = getattr(p, attr, None)
            if d is not None:
                return Path(d) / "history.sqlite"
    from platformdirs import user_data_path

    return user_data_path("socks") / "history.sqlite"


# ------------------------------------------------------------------- history


@dataclass
class HistoryData:
    summary: Any = None
    daily: list[Any] = field(default_factory=list)
    sessions: list[Any] = field(default_factory=list)
    soc: list[tuple[datetime, int]] = field(default_factory=list)
    health: Any = None
    nerd: dict[str, str] = field(default_factory=dict)
    drives: list[Any] = field(default_factory=list)


def open_db(path: Path) -> Any:
    d = deps()
    if d.history_db is None:
        return None
    fn = getattr(d.history_db, "open", None) or getattr(d.history_db, "connect", None)
    return fn(path) if fn else None


def _call(fn: Any, *args: Any, **kw: Any) -> Any:
    """Call with optional keyword args, dropping them if this stats version lacks them."""
    import inspect

    params = inspect.signature(fn).parameters
    return fn(*args, **{k: v for k, v in kw.items() if k in params})


def _drives(db: Any, vin: str, since: datetime | None) -> list[Any]:
    trips = _try("socks.history.trips")
    if db is None or trips is None or not hasattr(trips, "drives"):
        return []
    return list(trips.drives(db, vin, since))[::-1]  # newest first


def load_history(
    path: Path | None, vin: str, since: datetime | None, days: int, tariff: float, units: str
) -> HistoryData:
    """Synchronous; run it on the I/O thread (SQLite)."""
    d = deps()
    db = open_db(path) if path is not None else None
    try:
        st = d.stats
        nerd_fn = getattr(st, "nerd_stats", None)
        return HistoryData(
            summary=_call(st.summary, db, vin, since, tariff=tariff),
            daily=list(st.daily_km(db, vin, days)),
            sessions=list(_call(st.charge_sessions, db, vin, 50, tariff=tariff)),
            soc=list(st.soc_series(db, vin, since)),
            health=st.battery_health(db, vin),
            nerd=dict(_call(nerd_fn, db, vin, units_=units)) if nerd_fn else {},
            drives=_drives(db, vin, since),
        )
    finally:
        close = getattr(db, "close", None)
        if close:
            close()


def seed_fake_history(path: Path, vin: str, days: int = 45) -> None:
    """Fill a throwaway DB with synthetic states through the real Recorder."""
    d = deps()
    if d.recorder_mod is None:
        return
    from socks.gui import _devshim

    rec = d.recorder_mod.Recorder(path)
    try:
        for s in _devshim.synthetic_states(vin, days, d.model.VehicleState, d.enums):
            rec.record(s)
    finally:
        rec.close()
