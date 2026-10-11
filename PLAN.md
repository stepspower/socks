# SOCKS — State Of Charge, Keys & Stats

A lean Linux desktop companion for a Tesla: local Bluetooth control and status, optional
Tesla Fleet API for remote access, and a local history/statistics store (km driven, charging,
efficiency, battery health). GPL-3.0. Published under github.com/stepspower/socks, later Flathub.

This file is the master plan. Workers own the subtrees listed in §7 and build against the
interfaces in §4. Anything not specified here: choose the simplest correct option and note it in
`docs/DECISIONS.md` (append-only, one line per decision, prefixed with your area).

## 1. Hard rules (from the owner)

1. **Fast and lean.** Cold start < 1 s to a usable window on this laptop. No splash screens,
   no background daemons unless the user turns polling on. Lazy-import heavy modules
   (PySide6 submodules, tesla_fleet_api) inside functions that need them. Dependencies are
   exactly: `tesla-fleet-api` (brings aiohttp, cryptography, bleak, protobuf), `PySide6-Essentials`
   (NOT the full PySide6 / Addons), `platformdirs`. Nothing else at runtime. No numpy, no
   pandas, no pyqtgraph: charts are a small QPainter widget. Dev-only: ruff, pytest,
   pytest-asyncio, pip-audit, bandit.
2. **Secure.** Private key and tokens live under `$XDG_CONFIG_HOME/socks/` with mode 0600 (dir
   0700) and are never logged, never put in exceptions, never in the DB. All SQL parameterised.
   OAuth uses PKCE + loopback redirect bound to 127.0.0.1 with a random port and state. Every
   command that changes the car (lock/unlock, climate, charge port, charge limit, …) goes through
   one `confirm()` gate in the GUI; `unlock`, `open trunk/frunk`, `remote start` additionally
   require typing the first letters of the action. No telemetry, no update checks, no network
   except Tesla endpoints the user enabled. A security audit happens before the first public
   release; write code expecting it (small surface, obvious data flow).
3. **Universal Linux.** Qt 6 via PySide6-Essentials; must look and behave the same on KDE,
   GNOME, XFCE, X11 and Wayland. Use the platform theme (don't hardcode colours; derive from
   `QPalette`). Respect dark mode automatically. Keyboard-navigable. HiDPI via Qt defaults.
4. **Reuse Tesla's open source.** All protocol work goes through `tesla-fleet-api` (Apache-2.0,
   GPL-compatible), which wraps Tesla's own vehicle-command protocol for BLE and signed cloud
   commands. Do not reimplement crypto or protobufs.
5. **Units:** metric by default (km, kWh, °C, kW), switchable to imperial. Store SI-ish raw values
   (km, kWh, °C) in the DB; convert only for display. Tesla's APIs return miles and °C: convert
   at the boundary in `core`.
6. **Never touch the real car or Tesla's cloud from tests or during development.** Everything is
   developed against fakes (`tests/fakes.py`). The owner runs live tests, explicitly.
7. Python 3.13 (`.venv` already created with `uv`; `uv pip install --python .venv/bin/python …`).
   Type hints everywhere, `ruff check` and `ruff format` clean, `pytest` green. Module docstring
   with the GPL header in every file:
   `# SPDX-License-Identifier: GPL-3.0-or-later` + `# Copyright (C) 2026 stepspower`.

## 2. Existing assets on this machine (owner's — read, don't modify)

- `~/.config/tesla/private_key.pem` — EC P-256 key **already paired with the car as a driver key
  over BLE** (via Tesla's `tesla-control`). SOCKS must be able to use this same key (import, not
  regenerate) so the car trusts it immediately. Public key: `~/.config/tesla/public_key.pem`.
  VIN: `~/.config/tesla/vin`. **Never print the private key.**
- `~/.local/bin/tesla` — the owner's working BLE CLI; useful as a behavioural reference
  (`tesla -h`), not as a dependency.
- Reference knowledge: `~/claude/knowledge/tesla-ble.md`.
- Bluetooth adapter: Intel AX200, BlueZ. `tesla-fleet-api` uses bleak (BlueZ D-Bus), which
  does not need CAP_NET_ADMIN — good, no setcap for SOCKS.

## 3. Architecture

```
socks/
  __init__.py          version string only
  __main__.py          `python -m socks` → gui.main() ; `socks-cli` → cli.main()
  core/                owned by worker CORE
    config.py          Settings dataclass, XDG paths, load/save (TOML via tomllib + tiny writer)
    keys.py            key import/generation (P-256), public key PEM, VIN validation
    model.py           VehicleState + enums (pure data, no I/O)
    backend.py         Backend protocol; BleBackend; CloudBackend; Router (BLE first, cloud fallback)
    commands.py        Command enum + metadata (dangerous?, needs_wake?, label)
    cli.py             `socks-cli status|json|cmd <name>` for testing and scripting
  history/             owned by worker HISTORY
    db.py              SQLite schema + migrations, open(), parameterised helpers
    recorder.py        record(VehicleState) → snapshots; session detection (charges, drives)
    stats.py           queries → dataclasses for the GUI (per day/week/month, efficiency, degradation)
    units.py           km/mi, kWh, °C/°F conversion + formatting
  fleet/               owned by worker FLEET
    oauth.py           PKCE auth code flow, loopback redirect, token refresh, token storage
    partner.py         partner-account registration call, public-key hosting check
    README.md          exact owner steps for developer.tesla.com (see §6)
  gui/                 owned by worker GUI
    app.py             QApplication setup, QtAsyncio loop, theme/palette handling
    main_window.py     tabs: Status · Controls · History · Settings ; status bar; tray icon
    widgets/           StatusCard, Gauge, Chart (QPainter), ConfirmDialog, LogView
    async_bridge.py    run coroutines from Qt safely (PySide6.QtAsyncio), cancellation on close
  packaging/           owned by worker PACKAGING
    flatpak/net.stepspower.Socks.yml, net.stepspower.Socks.desktop, .metainfo.xml, icons/
tests/                 each worker adds tests for their area; `tests/fakes.py` is shared
docs/                  DECISIONS.md, SECURITY.md, FLEET_API_SETUP.md, ARCHITECTURE.md
```

Concurrency: one asyncio loop (QtAsyncio in the GUI; plain asyncio in the CLI). Backends are
async. The GUI never blocks: every backend call is a task; the UI shows a busy state per
widget, not a modal.

## 4. Interfaces (contract between workers — keep names exactly)

```python
# socks/core/model.py
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

class ChargingState(Enum): DISCONNECTED="Disconnected"; STOPPED="Stopped"; CHARGING="Charging"; COMPLETE="Complete"; NO_POWER="NoPower"; STARTING="Starting"; UNKNOWN="Unknown"
class ShiftState(Enum): P="P"; R="R"; N="N"; D="D"; UNKNOWN="?"
class Source(Enum): BLE="ble"; CLOUD="cloud"; CACHE="cache"

@dataclass(frozen=True)
class VehicleState:
    ts: datetime                    # UTC, when read
    source: Source
    vin: str
    # charge
    soc: int | None                 # %
    usable_soc: int | None
    charge_limit: int | None        # %
    charging: ChargingState
    charger_power_kw: float | None
    charger_voltage: int | None
    charger_current_a: int | None
    charge_energy_added_kwh: float | None
    minutes_to_limit: int | None
    rated_range_km: float | None
    ideal_range_km: float | None
    plugged_in: bool | None
    charge_port_open: bool | None
    scheduled_charging_start: datetime | None
    # drive
    odometer_km: float | None
    shift: ShiftState
    speed_kmh: float | None
    power_kw: float | None
    # climate
    inside_temp_c: float | None
    outside_temp_c: float | None
    climate_on: bool | None
    target_temp_c: float | None
    # closures / security
    locked: bool | None
    doors_open: tuple[str, ...] = ()   # e.g. ("front_left","trunk")
    windows_open: tuple[str, ...] = ()
    sentry_on: bool | None = None
    # misc
    car_name: str | None = None
    firmware: str | None = None
    raw: dict = field(default_factory=dict, compare=False)   # original payload, never persisted

# socks/core/commands.py
class Command(Enum):
    LOCK="lock"; UNLOCK="unlock"; CLIMATE_ON="climate_on"; CLIMATE_OFF="climate_off"
    SET_TEMP="set_temp"; CHARGE_START="charge_start"; CHARGE_STOP="charge_stop"
    SET_CHARGE_LIMIT="set_charge_limit"; CHARGE_PORT_OPEN="charge_port_open"
    CHARGE_PORT_CLOSE="charge_port_close"; FLASH_LIGHTS="flash_lights"; HONK="honk"
    FRUNK="frunk"; TRUNK="trunk"; SENTRY_ON="sentry_on"; SENTRY_OFF="sentry_off"; WAKE="wake"
@dataclass(frozen=True)
class CommandInfo: command: Command; label: str; dangerous: bool; needs_wake: bool; arg: str | None  # arg: "temp_c" | "percent" | None
COMMANDS: dict[Command, CommandInfo]

# socks/core/backend.py
class Backend(Protocol):
    name: Source
    async def available(self) -> bool: ...
    async def read_state(self) -> VehicleState: ...
    async def send(self, cmd: Command, arg: float | int | None = None) -> None: ...   # raises CommandError
class Router(Backend):
    def __init__(self, ble: Backend | None, cloud: Backend | None, prefer: Source = Source.BLE): ...
class CommandError(Exception): ...
class NotAvailable(CommandError): ...

# socks/core/config.py
@dataclass
class Settings:
    vin: str = ""; key_path: Path = ...; units: str = "metric"; prefer: str = "ble"
    cloud_enabled: bool = False; client_id: str = ""; region: str = "eu"   # eu|na|cn
    poll_seconds: int = 0           # 0 = off; history recorder interval when a window is open
    tariff_eur_per_kwh: float = 0.0; currency: str = "EUR"
    def load() -> "Settings"; def save(self) -> None; def paths() -> AppPaths  # config, data, cache dirs

# socks/history/recorder.py
class Recorder:
    def __init__(self, db_path: Path): ...
    def record(self, state: VehicleState) -> None      # idempotent per (vin, ts)
    def close(self) -> None
# socks/history/stats.py  — all return plain dataclasses/lists; see worker HISTORY spec
def summary(db, vin, since: datetime | None) -> Summary
def daily_km(db, vin, days: int) -> list[DayKm]
def charge_sessions(db, vin, limit: int) -> list[ChargeSession]
def soc_series(db, vin, since) -> list[tuple[datetime, int]]
def battery_health(db, vin) -> BatteryHealth   # rated range at 100% over time vs first seen
```

## 5. Data model (SQLite, `$XDG_DATA_HOME/socks/history.sqlite`)

- `snapshot(vin, ts, source, soc, usable_soc, charge_limit, charging, charger_power_kw,
  charge_energy_added_kwh, rated_range_km, odometer_km, shift, speed_kmh, inside_temp_c,
  outside_temp_c, locked, plugged_in)` PK (vin, ts). Insert only when something changed or ≥ 5 min
  since last row (recorder decides).
- `charge_session(id, vin, start_ts, end_ts, start_soc, end_soc, energy_added_kwh, max_power_kw,
  location_hint, source)` — derived: starts when charging goes to CHARGING, ends when it leaves.
- `drive(id, vin, start_ts, end_ts, start_odo_km, end_odo_km, start_soc, end_soc)` — derived from
  shift ≠ P / odometer increasing; a gap > 10 min closes it.
- `meta(key, value)` — schema_version, first_seen_rated_range_at_100.
- Stats: km per day/week/month; kWh charged and cost (tariff); Wh/km from drives (ΔkWh via
  SoC × pack estimate, mark as estimate); battery health = rated_range_km / soc × 100 trend;
  charging curve (power vs soc) from in-session snapshots; "nerd" extras: longest drive, best
  efficiency day, total charge sessions, time plugged in vs charging.

## 6. Fleet API (cloud) — owner-side steps, documented by worker FLEET in docs/FLEET_API_SETUP.md

- Domain: **stepspower.net** (Greenhost, SFTP host alias `greenhost`, directory `stepspower.net/`
  in the SFTP root; find the web root inside it). Public key must be served at
  `https://stepspower.net/.well-known/appspecific/com.tesla.3p.public-key.pem` — upload the
  owner's existing `~/.config/tesla/public_key.pem` there (it's public) and verify with curl.
- Owner creates the app at developer.tesla.com (name SOCKS, redirect URI
  `http://localhost/callback` style loopback as Tesla allows, scopes: openid offline_access
  vehicle_device_data vehicle_cmds vehicle_charging_cmds vehicle_location), adds a payment method
  (billing limit is 0 otherwise; $10/month discount applies). Worker writes the exact click path,
  then the one-time `partner_accounts` registration (`socks-cli fleet register`) and the virtual-key
  pairing link `https://tesla.com/_ak/stepspower.net` the owner opens on the phone.
- Prices to show in Settings next to the cloud toggle: data read $0.002, command $0.001,
  wake $0.02; recommend BLE-first and polling off by default.

## 7. Workers and ownership

| Worker | Model | Owns | Deliverable / acceptance |
|---|---|---|---|
| CORE | opus | `socks/core/`, `socks/__init__.py`, `socks/__main__.py`, `tests/fakes.py`, `tests/test_core*.py` | `socks-cli status` works against the fake; `BleBackend`/`CloudBackend` fully implemented on tesla-fleet-api 1.17.3 (read its actual API in `.venv/lib/python3.13/site-packages/tesla_fleet_api/`, the import path for the BLE vehicle class is NOT `VehicleBluetooth` under `tesla.bluetooth` — discover it); miles→km at the boundary; key import from `~/.config/tesla/`; pytest green |
| HISTORY | sonnet | `socks/history/`, `tests/test_history*.py` | schema + recorder + all §4 stats functions with tests on synthetic sequences (a charge session, two drives, a day gap); no I/O other than SQLite |
| GUI | opus | `socks/gui/`, `data/icons/socks.svg` | runs with `python -m socks --fake` showing live fake data; four tabs; confirm gate; palette-derived colours; tray icon; History tab charts from `history.stats`; closes cleanly (tasks cancelled) |
| FLEET | opus | `socks/fleet/`, `docs/FLEET_API_SETUP.md`, uploads public key to stepspower.net | PKCE flow tested with a fake token endpoint; token file 0600; `socks-cli fleet login|register|status`; key reachable at the well-known URL (verify with curl, report the exact URL) |
| PACKAGING | sonnet | `pyproject.toml`, `ruff.toml`, `.github/workflows/ci.yml`, `packaging/`, `README.md`, `CONTRIBUTING.md`, `docs/SECURITY.md`, `docs/ARCHITECTURE.md`, `.gitignore` | `uv pip install -e .` works; `ruff`, `pytest`, `pip-audit`, `bandit -r socks` run in CI; Flatpak manifest validates (`flatpak-builder --show-manifest` if available, else careful review) using org.kde.Platform 6.9+ runtime with PySide6 wheels via pip generator; desktop file + metainfo pass `desktop-file-validate`/`appstreamcli validate` if installed; README with screenshots placeholder, install (pipx/uv/Flatpak), privacy section |

Rules for every worker: stay inside your subtree (shared files: only append to
`docs/DECISIONS.md`); do not `git commit` (the integrator does); do not run anything against the
real car, Bluetooth, or Tesla endpoints; do not read `~/.config/tesla/private_key.pem` contents
(only path handling; tests use a generated throwaway key); finish by running
`.venv/bin/ruff check socks tests && .venv/bin/pytest -q` and reporting results plus a ≤15-line
summary of what exists, what is stubbed, and open questions.

## 8. Integration and release (integrator = planner)

1. Merge check: imports resolve, CLI + `--fake` GUI run, full test suite, ruff.
2. Live test with the owner: BLE status read, then one harmless command (flash lights) after OK.
3. Security audit worker (fresh eyes): threat model, dependency audit, token/key handling,
   command gate, SQL, OAuth; fix findings.
4. First commit, push to `github.com/stepspower/socks`, tag v0.1.0, CI green.
5. Flathub submission PR (`flathub/flathub` new-app process) + post in r/teslamotors,
   r/linux, Tesla Motors Club software forum, Hacker News "Show HN".
