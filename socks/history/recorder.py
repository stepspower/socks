# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Turn a stream of VehicleState reads into snapshots, charge sessions and drives.

Enums from core.model are compared by their ``.value`` strings so this module has no
runtime dependency on core (VehicleState is imported for type checking only).

Rules (PLAN section 5):
* snapshot: stored only if a tracked field changed or >= 5 min since the last stored row
  (the data ``source`` is deliberately not a tracked field, so BLE/cloud mixes do not
  inflate the table). Source CACHE states are stale replays and are ignored.
* charge session: opens when ``charging`` becomes Charging, closes when it leaves it.
* drive: active while shift is R/N/D or the odometer rose; closed by a read showing
  shift P with no odometer change, or by a > 10 min gap between reads.
* A read whose ts is <= the newest ts seen for the VIN is out-of-order or a duplicate:
  it is stored as a snapshot if that exact (vin, ts) is free but never touches
  session/drive derivation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from . import db as dbm
from . import places, stats

if TYPE_CHECKING:
    from socks.core.model import VehicleState

SNAPSHOT_MAX_AGE = timedelta(minutes=5)
DRIVE_GAP = timedelta(minutes=10)
CHARGE_GAP = timedelta(minutes=60)
ODO_EPSILON_KM = 0.05
MIN_SOC_FOR_RANGE_AT_100 = 20
MIN_LEARNED_SAMPLES = 3
MOVE_THRESHOLD_M = 25.0
DRIVE_START_WINDOW_S = 600.0
RETRO_START_WINDOW_S = 24 * 3600.0

_TRACKED = (
    "soc",
    "usable_soc",
    "charge_limit",
    "charging",
    "charger_power_kw",
    "charge_energy_added_kwh",
    "rated_range_km",
    "odometer_km",
    "shift",
    "speed_kmh",
    "inside_temp_c",
    "outside_temp_c",
    "locked",
    "plugged_in",
)
_INSERT_SNAPSHOT = (
    "INSERT OR IGNORE INTO snapshot (vin, ts, source, soc, usable_soc, charge_limit, "
    "charging, charger_power_kw, charge_energy_added_kwh, rated_range_km, odometer_km, "
    "shift, speed_kmh, inside_temp_c, outside_temp_c, locked, plugged_in, lat, lon) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


@dataclass
class _Seen:
    ts: datetime
    odo: float | None
    soc: int | None


def _b(v: bool | None) -> int | None:
    return None if v is None else int(v)


def _pos(s: VehicleState) -> tuple[float | None, float | None]:
    lat, lon = getattr(s, "latitude", None), getattr(s, "longitude", None)
    return (lat, lon) if lat is not None and lon is not None else (None, None)


def _values(s: VehicleState) -> dict[str, object]:
    return {
        "soc": s.soc,
        "usable_soc": s.usable_soc,
        "charge_limit": s.charge_limit,
        "charging": s.charging.value,
        "charger_power_kw": s.charger_power_kw,
        "charge_energy_added_kwh": s.charge_energy_added_kwh,
        "rated_range_km": s.rated_range_km,
        "odometer_km": s.odometer_km,
        "shift": s.shift.value,
        "speed_kmh": s.speed_kmh,
        "inside_temp_c": s.inside_temp_c,
        "outside_temp_c": s.outside_temp_c,
        "locked": _b(s.locked),
        "plugged_in": _b(s.plugged_in),
    }


class Recorder:
    def __init__(self, db_path: Path | str, pack_kwh: float | None = None):
        """``pack_kwh=None`` learns the pack size from past sessions (default 80 kWh)."""
        self._configured_pack = pack_kwh
        self.pack_kwh: float = pack_kwh if pack_kwh is not None else dbm.DEFAULT_PACK_KWH
        self.pack_source: str = "default" if pack_kwh is None else "configured"
        self.conn: sqlite3.Connection = dbm.open(db_path)
        self._seen_vins: set[str] = set()
        self._seen: dict[str, _Seen | None] = {}

    def close(self) -> None:
        self.conn.close()

    # -- public ---------------------------------------------------------------------

    def record(self, state: VehicleState) -> None:
        """Idempotent per (vin, ts)."""
        if state.source.value == "cache":
            return
        ts = state.ts if state.ts.tzinfo else state.ts.replace(tzinfo=dbm.UTC)
        ts = ts.astimezone(dbm.UTC)
        vin = state.vin
        seen = self._get_seen(vin)
        if self._configured_pack is None and vin not in self._seen_vins:
            self._seen_vins.add(vin)
            self._refresh_pack(vin)
        with dbm.transaction(self.conn):
            if seen is not None and ts <= seen.ts:
                if ts < seen.ts:
                    self._insert_snapshot(state, ts)
                return
            self._charge(state, ts, seen)
            if self._should_snapshot(state, ts):
                self._insert_snapshot(state, ts)
            self._track_range(state)
            self._drive(state, ts, seen)
            if _pos(state)[0] is not None:
                places.fill_drive_ends(self.conn, vin, self._open_id("drive", vin), ts)
        self._seen[vin] = _Seen(
            ts,
            state.odometer_km if state.odometer_km is not None else (seen.odo if seen else None),
            state.soc if state.soc is not None else (seen.soc if seen else None),
        )

    # -- seen / snapshot --------------------------------------------------------------

    def _get_seen(self, vin: str) -> _Seen | None:
        if vin not in self._seen:
            row = dbm.query_one(
                self.conn,
                "SELECT ts, soc, odometer_km FROM snapshot WHERE vin = ? ORDER BY ts DESC LIMIT 1",
                (vin,),
            )
            self._seen[vin] = (
                _Seen(dbm.parse_ts(row["ts"]), row["odometer_km"], row["soc"]) if row else None
            )
            if row and row["odometer_km"] is None:
                last = dbm.scalar(
                    self.conn,
                    "SELECT odometer_km FROM snapshot WHERE vin = ? AND odometer_km IS NOT NULL "
                    "ORDER BY ts DESC LIMIT 1",
                    (vin,),
                )
                self._seen[vin].odo = last  # type: ignore[union-attr]
        return self._seen[vin]

    def _insert_snapshot(self, s: VehicleState, ts: datetime) -> None:
        vals = _values(s)
        dbm.execute(
            self.conn,
            _INSERT_SNAPSHOT,
            (s.vin, dbm.iso(ts), s.source.value, *(vals[c] for c in _TRACKED), *_pos(s)),
        )

    def _should_snapshot(self, s: VehicleState, ts: datetime) -> bool:
        last = dbm.query_one(
            self.conn, "SELECT * FROM snapshot WHERE vin = ? ORDER BY ts DESC LIMIT 1", (s.vin,)
        )
        if last is None or ts - dbm.parse_ts(last["ts"]) >= SNAPSHOT_MAX_AGE:
            return True
        lat, lon = _pos(s)
        if lat is not None and (
            last["lat"] is None
            or places.haversine_m(lat, lon, last["lat"], last["lon"]) > MOVE_THRESHOLD_M
        ):
            return True
        return any(last[k] != v for k, v in _values(s).items())

    def _track_range(self, s: VehicleState) -> None:
        if s.rated_range_km and s.soc and s.soc >= MIN_SOC_FOR_RANGE_AT_100:
            dbm.execute(
                self.conn,
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                (
                    f"first_seen_rated_range_at_100:{s.vin}",
                    f"{s.rated_range_km / s.soc * 100:.1f}",
                ),
            )

    # -- open-session bookkeeping -----------------------------------------------------

    def _open_id(self, kind: str, vin: str) -> int | None:
        v = dbm.get_meta(self.conn, f"open_{kind}:{vin}")
        return int(v) if v else None

    def _set_open(self, kind: str, vin: str, row_id: int | None) -> None:
        key = f"open_{kind}:{vin}"
        if row_id is None:
            dbm.del_meta(self.conn, key)
        else:
            dbm.set_meta(self.conn, key, str(row_id))

    # -- charge sessions --------------------------------------------------------------

    def _charge(self, s: VehicleState, ts: datetime, seen: _Seen | None) -> None:
        vin = s.vin
        open_id = self._open_id("charge", vin)
        if open_id is not None and seen is not None and ts - seen.ts > CHARGE_GAP:
            self._set_open("charge", vin, None)  # row already ends at its last update
            open_id = None
        charging = s.charging.value == "Charging"
        if open_id is None:
            if not charging:
                return
            cur = dbm.execute(
                self.conn,
                "INSERT INTO charge_session (vin, start_ts, end_ts, start_soc, end_soc, "
                "energy_added_kwh, max_power_kw, source, estimated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    vin,
                    dbm.iso(ts),
                    dbm.iso(ts),
                    s.soc,
                    s.soc,
                    None,
                    s.charger_power_kw,
                    s.source.value,
                ),
            )
            open_id = int(cur.lastrowid or 0)
            self._set_open("charge", vin, open_id)
        row = dbm.query_one(self.conn, "SELECT * FROM charge_session WHERE id = ?", (open_id,))
        if row is None:  # stale meta
            self._set_open("charge", vin, None)
            return
        end_soc = s.soc if s.soc is not None else row["end_soc"]
        max_kw = row["max_power_kw"]
        if s.charger_power_kw is not None and (max_kw is None or s.charger_power_kw > max_kw):
            max_kw = s.charger_power_kw
        counter = s.charge_energy_added_kwh
        estimated = bool(row["estimated"])
        energy = row["energy_added_kwh"]
        if counter is not None:
            # The car's own counter is cumulative since plug-in: take the largest value.
            energy = max(counter, 0.0 if estimated else (energy or 0.0))
            estimated = False
        if estimated and row["start_soc"] is not None and end_soc is not None:
            energy = max(0, end_soc - row["start_soc"]) / 100.0 * self.pack_kwh
        source = row["source"] if row["source"] == s.source.value else "mixed"
        dbm.execute(
            self.conn,
            "UPDATE charge_session SET end_ts = ?, end_soc = ?, energy_added_kwh = ?, "
            "max_power_kw = ?, source = ?, estimated = ? WHERE id = ?",
            (dbm.iso(ts), end_soc, energy, max_kw, source, int(estimated), open_id),
        )
        if not charging:
            self._set_open("charge", vin, None)
            self._refresh_pack(vin)

    def _refresh_pack(self, vin: str) -> None:
        if self._configured_pack is not None:
            return
        est = stats.estimate_pack_kwh(self.conn, vin)
        if est is not None and est.samples >= MIN_LEARNED_SAMPLES:
            self.pack_kwh, self.pack_source = est.kwh, "learned"
        else:
            self.pack_kwh, self.pack_source = dbm.DEFAULT_PACK_KWH, "default"

    # -- drives -------------------------------------------------------------------------

    def _drive(self, s: VehicleState, ts: datetime, seen: _Seen | None) -> None:
        vin = s.vin
        open_id = self._open_id("drive", vin)
        if open_id is not None and seen is not None and ts - seen.ts > DRIVE_GAP:
            self._set_open("drive", vin, None)
            open_id = None
        last_odo = seen.odo if seen else None
        odo = s.odometer_km
        odo_up = odo is not None and last_odo is not None and odo > last_odo + ODO_EPSILON_KM
        moving = s.shift.value in ("R", "N", "D")
        active = moving or odo_up
        if open_id is None:
            if not active:
                return
            if odo_up and seen is not None:
                start = (dbm.iso(seen.ts), last_odo, seen.soc)
                pos = places.position_at_or_before(self.conn, vin, seen.ts, RETRO_START_WINDOW_S)
            else:
                start = (dbm.iso(ts), odo, s.soc)
                pos = places.position_at_or_before(self.conn, vin, ts, DRIVE_START_WINDOW_S)
            lat0, lon0 = pos or (None, None)
            cur = dbm.execute(
                self.conn,
                "INSERT INTO drive (vin, start_ts, end_ts, start_odo_km, end_odo_km, "
                "start_soc, end_soc, start_lat, start_lon) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (vin, start[0], dbm.iso(ts), start[1], odo, start[2], s.soc, lat0, lon0),
            )
            self._set_open("drive", vin, int(cur.lastrowid or 0))
            return
        if active:
            dbm.execute(
                self.conn,
                "UPDATE drive SET end_ts = ?, end_odo_km = COALESCE(?, end_odo_km), "
                "end_soc = COALESCE(?, end_soc) WHERE id = ?",
                (dbm.iso(ts), odo, s.soc, open_id),
            )
        elif s.shift.value == "P":
            self._set_open("drive", vin, None)  # parked, nothing moved: row ends at last activity
