# Architecture

SOCKS is a small PySide6 (Qt 6) application. One asyncio loop drives everything: QtAsyncio in
the GUI, plain asyncio in the CLI. Backends are async; the GUI never blocks, so every backend
call is a task and the UI shows a busy state per widget, not a modal.

## Packages

```
socks/
  core/      config (Settings, XDG paths), keys (import/generate P-256, VIN check),
             model (VehicleState and enums, pure data), backend (Backend protocol,
             BleBackend, CloudBackend, Router), commands (Command enum and metadata), cli
  history/   db (SQLite schema, migrations), recorder, stats (queries returning dataclasses),
             units (km/mi, kWh, C/F conversion)
  fleet/     oauth (PKCE, loopback redirect, token refresh and storage), partner registration
  gui/       app, main_window (Status, Controls, History, Settings tabs, tray), widgets
             (cards, gauge, QPainter chart, confirm dialog, log view), async_bridge
```

## Data flow

1. A `Backend` reads a `VehicleState` (frozen dataclass; SI-ish units, Tesla's miles are
   converted to km at the boundary in `core`). `Router` tries BLE first and falls back to the
   cloud.
2. The GUI renders the state and, when recording is on, passes it to `Recorder.record()`.
3. The recorder inserts snapshots when something changed or at least 5 minutes have passed,
   and derives charge sessions and drives (a gap over 10 minutes closes a drive).
4. `history.stats` answers the History tab with plain dataclasses. Conversion to display
   units happens only in the UI.
5. Commands: GUI action, `confirm()` gate, `Backend.send(Command, arg)`; failures raise
   `CommandError` / `NotAvailable`.

## Storage

- Config, key and tokens: `$XDG_CONFIG_HOME/socks/` (0700 dir, 0600 files).
- History: `$XDG_DATA_HOME/socks/history.sqlite`, tables `snapshot`, `charge_session`, `drive`,
  `meta`. Primary key of `snapshot` is `(vin, ts)`, so recording is idempotent.

## Design constraints

Three runtime dependencies, lazy imports for startup speed (target under 1 s to a usable
window), palette-derived colours, no telemetry. All protocol and crypto work is delegated to
`tesla-fleet-api`; SOCKS adds no crypto of its own. Tests run against fakes
(`tests/fakes.py`), never the real car.
