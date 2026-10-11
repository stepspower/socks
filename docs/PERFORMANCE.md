# Performance — 2026-10-11 optimisation pass

Owner's rule: "fast and not complicated, no bloat". This pass made SOCKS a little faster at
start-up, removed a few real hot spots, and deleted about 900 lines without changing behaviour.

## How it was measured

- Machine: the owner's laptop (Linux Mint 22.3, Python 3.13, PySide6-Essentials 6.12).
- Before: the tree at commit `604142b`, checked out into a separate worktree. After: the
  working tree. Both use the same `.venv`.
- Start-up: `QT_QPA_PLATFORM=offscreen python -m socks --fake --measure-startup`, median of
  5 runs. This was offscreen because a real X11 window would have flashed up on the owner's
  desktop. `/proc` ticks are 10 ms, so the process-start figure moves in 10 ms steps.
- DB: a synthetic history in a temp dir (50,000 snapshots at 5-minute spacing with GPS,
  2,000 drives, 600 charge sessions, places learned). Each figure is the median of 5 calls.
  For every query, the statement was captured with `set_trace_callback` and run through
  `EXPLAIN QUERY PLAN`.
- Equivalence: every stats, trips and places function, plus a CSV export, was run on the same
  synthetic DB with the old and the new tree. The output is byte-identical.

## Before / after

| What | Before | After |
|---|---|---|
| First paint, from process start (offscreen, median of 5) | 210 ms | 190 ms |
| First paint, from `socks.gui.app` import | 185 ms | 163 ms |
| `import socks.gui.app` | 7 ms | 7 ms |
| Our import cost before first paint (`socks.gui.main_window` with Qt preloaded, median of 7) | 79 ms | 69 ms |
| Heavy third-party modules loaded before first paint | `cryptography` | none (Qt + stdlib only) |
| Test suite (`pytest -q`, offscreen) | 232 tests, 4.4 s | 233 tests, 3.3 s |
| Lines of Python in `socks/` | 11,846 | 10,942 |
| Lines of Python in `tests/` | 4,477 | 4,491 |
| Chart hover, 6,480-point SoC series (45 fake days) | 1,486 ms per mouse move | 0.2 ms |
| Chart repaint, same series | 27 ms | 7 ms |

### DB query times at 50k snapshots (ms, median of 5)

| Function | Before | After | Notes |
|---|---:|---:|---|
| `stats.summary` (all time) | 4.7 | 4.6 | |
| `stats.daily_km(30)` | 0.7 | 0.7 | |
| `stats.charge_sessions(50)` | 0.2 | 0.2 | |
| `stats.soc_series` (30 days) | 5.3 | 4.3 | tuple cursor |
| `stats.soc_series` (all time) | 40.5 | 27.4 | tuple cursor |
| `stats.battery_health` | 26.8 | 4.9 | COUNT plus two `LIMIT n` queries instead of fetching every row |
| `stats.nerd_stats` | 46.3 | 33.3 | session totals and fastest charge in SQL |
| `stats.estimate_pack_kwh` | 0.7 | 0.7 | |
| `trips.drives` (all, 2k) | 9.5 | 9.1 | |
| `trips.mileage_summary` | 9.7 | 9.5 | |
| `places.fill_drive_ends` (runs on every GPS record) | 0.17 | 0.01 | new partial index (schema v4) |
| `places.pending_geocodes` | 8.9 | 8.5 | |
| `places.relabel_drives` | 15.1 | 15.2 | |
| `places.learn_places` | 62.0 | 61.9 | |
| export snapshots CSV (50k rows) | 482 | 457 | |
| export drives CSV | 22.5 | 22.5 | |
| recorder `record()` | 0.085 / record | 0.084 / record | already one transaction per call |

## What changed and why

1. **Chart hover was O(n²)** (`gui/widgets/chart.py`). Each mouse move scanned every point,
   and for each point it recomputed the y-axis maximum over all points. The fake 45-day series
   froze the UI for about 1.5 s per move. Now the axis maximum is computed once per
   `set_series`, and hover is a `bisect` on the sorted x values. The line path is cached per
   plot rectangle. Series longer than twice the plot width in pixels are drawn with the
   first/min/max/last point of each pixel column. The shape is unchanged; only the
   antialiasing of single pixels can differ. A test checks that the extremes are kept.
2. **Lazy imports.** `core.keys` imported `cryptography` at module level, and the GUI pulled it
   in through `core.config`; it is now imported inside the key functions. `main_window` and
   `location` import `core.backend`, `gui.history_io` (sqlite3, statistics, history) only in
   the functions that need them. All of those run after first paint.
3. **SQLite.** The query plans showed that the indexes the brief asked about already exist:
   `snapshot` PK `(vin, ts)`, `ix_drive_vin_start`, `ix_charge_vin_start`, `ix_place_vin`, and
   the `geocode` PK `(lat_r, lon_r)`. WAL and `synchronous=NORMAL` were already set once in
   `db.open()`, and `record()` already ran in a single `BEGIN IMMEDIATE` transaction. The one
   plan that used only the `vin` prefix was `fill_drive_ends`, which runs on every GPS read. It
   got a partial index `ix_drive_open_end ON drive (vin, end_ts) WHERE end_lat IS NULL` (schema
   v4, migration tested from v3).
4. **Aggregation in SQL where it pays off.** `battery_health` asks SQLite for the count and the
   first/last n values instead of fetching every row. `nerd_stats` computes the session count,
   energy sum, charging time and fastest charge in SQL. It no longer builds 1,000,000-limit
   `ChargeSession` objects. A SQL window function (`LEAD`) for "time plugged in" was measured
   at 33 ms against 24 ms for a Python loop over integer epochs, so the loop stayed. Long
   scans use a tuple cursor (`db.tuples()`), because `sqlite3.Row` was half the cost.
5. **Caching.** `Settings.load()` is called once per process in the GUI, so nothing needed
   caching. The History tab already reloads only after a `record()` or a user action
   (`_history_dirty`), and charts never read the DB on repaint. Repaints now also reuse the
   cached path (see 1).
6. **Tests.** The geocoder rate-limit tests patch the interval to 0.05 s instead of 0.2 s.
   They still assert the spacing.

## Simplifications

- Deleted `gui/_devshim.py` (513 lines) and the fallback layer `gui/_deps.py` (256 lines).
  Both existed only because the modules were written at the same time. The GUI imports core
  and history directly. The synthetic 45-day history moved to `tests/fakes.py`
  (`synthetic_states`), loaded through the same checkout-only loader as `FakeBackend`
  (`core.backend.fakes_module`, audit L2). The rest of `_deps` became `gui/history_io.py`.
- Deleted `tests/_model_stub.py`, its `try/except ImportError`, and the `sys.path` hacks in
  six test files (`pyproject` already sets `pythonpath`).
- Removed every "core may not exist yet" fallback: `inspect.signature` probing of
  `read_state` and stats functions, `getattr(settings, …)` and `hasattr` on the real
  `Settings` (settings tab, wizard, main window, location), and `ImportError` fallbacks in
  `core/cli`, `fleet/cli`, `gui/location` and `gui/setup_services`.
- One helper each: `keys.write_secret_file` (atomic, mode 0600) now also writes OAuth tokens,
  invoices and charging CSVs (`private_dir=False` for a folder the user chose) and history
  exports. It replaced three copies. `oauth.token_provider` replaced two copies (CLI and
  GUI). `db.local_zone` replaced four copies. `history/units` now holds the only
  `KM_PER_MI`, `c_to_f` and `f_to_c`, replacing copies in `core/model`, `core/cli` and
  `gui/fmt`. `keys.validate_vin`/`is_valid_vin` and `keys.tesla_control_dir` replaced copies
  of the VIN regex and the `~/.config/tesla` path in the settings tab and setup services.
- Forwarding wrappers `db.execute/query/query_one` were replaced by `conn.execute(...)`.
- Dead code was removed (unused functions in `history/units`, `colors.is_dark`,
  `AppPaths.public_key_file`, the unused `settings` parameter of `oauth.status`, and
  `cli._no_fleet`).

## Remaining hot spots (not changed)

- **First paint is mostly Qt.** Roughly: importing QtCore/QtGui/QtWidgets takes about
  70 ms, our module imports about 70 ms, and building and painting the window takes the rest.
  Of our 70 ms, shiboken's signature loader (triggered by the first Qt subclass) is about
  30 ms and `asyncio` is about 20 ms. `asyncio` is needed by the async bridge, which the
  window constructor creates; deferring it would mean restructuring the bridge for a small
  gain.
- **History tab reload at 50k rows** costs about 85 ms on the I/O thread (soc_series all
  time, nerd_stats, summary, drives). It is off the GUI thread and runs once per record while
  the tab is visible.
- **`learn_places`** (62 ms) walks every GPS snapshot to find "home". It runs only on
  Rebuild labels and fake seeding.
- **Snapshot CSV export** (about 0.46 s for 50k rows) spends its time formatting local
  timestamps. It is user-initiated and runs off the GUI thread.
