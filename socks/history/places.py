# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Places learned from drive endpoints, automatic labels and purpose rules.

Everything is local: no network. Reverse geocoding is done by the caller, which reads
``pending_geocodes`` and feeds results back through ``store_geocode``.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo

from . import db as dbm

CLUSTER_RADIUS_M = 150.0
MIN_VISITS = 3
HOME_NIGHT_HOURS = range(2, 5)  # 02:00-04:59 local
HOME_CONFIDENCE = 0.70
HOME_MIN_NIGHTS = 3
END_WINDOW_S = 24 * 3600


@dataclass(frozen=True)
class Place:
    id: int
    vin: str
    name: str
    lat: float
    lon: float
    radius_m: float
    kind: str
    visits: int
    created_ts: str
    default_purpose: str | None = None

    @property
    def display_name(self) -> str:
        return self.name or f"Unnamed place #{self.id}"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def _place(r: sqlite3.Row) -> Place:
    return Place(
        r["id"], r["vin"], r["name"], r["lat"], r["lon"], r["radius_m"], r["kind"],
        r["visits"], r["created_ts"], r["default_purpose"],
    )  # fmt: skip


def list_places(db: sqlite3.Connection, vin: str) -> list[Place]:
    return [
        _place(r) for r in dbm.query(db, "SELECT * FROM place WHERE vin = ? ORDER BY id", (vin,))
    ]


def match(places: list[Place], lat: float | None, lon: float | None) -> Place | None:
    """Nearest place whose radius contains the point."""
    if lat is None or lon is None:
        return None
    best, best_d = None, math.inf
    for p in places:
        d = haversine_m(lat, lon, p.lat, p.lon)
        if d <= p.radius_m and d < best_d:
            best, best_d = p, d
    return best


def place_at(db: sqlite3.Connection, vin: str, lat: float, lon: float) -> Place | None:
    return match(list_places(db, vin), lat, lon)


def add_place(
    db: sqlite3.Connection,
    vin: str,
    name: str,
    lat: float,
    lon: float,
    kind: str = "other",
    radius_m: float = CLUSTER_RADIUS_M,
) -> int:
    """Manually add a named place; returns its id."""
    if kind not in dbm.PLACE_KINDS:
        raise ValueError(f"kind must be one of {dbm.PLACE_KINDS}")
    with dbm.transaction(db):
        if kind in ("home", "work"):
            _demote(db, vin, kind)
        cur = dbm.execute(
            db,
            "INSERT INTO place (vin, name, lat, lon, radius_m, kind, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (vin, name.strip()[:200], lat, lon, radius_m, kind, dbm.iso(datetime.now(UTC))),
        )
    return int(cur.lastrowid or 0)


def _demote(db: sqlite3.Connection, vin: str, kind: str) -> None:
    dbm.execute(db, "UPDATE place SET kind = 'other' WHERE vin = ? AND kind = ?", (vin, kind))


def name_place(db: sqlite3.Connection, place_id: int, name: str, kind: str | None = None) -> None:
    if kind is not None and kind not in dbm.PLACE_KINDS:
        raise ValueError(f"kind must be one of {dbm.PLACE_KINDS}")
    with dbm.transaction(db):
        row = dbm.query_one(db, "SELECT vin FROM place WHERE id = ?", (place_id,))
        if row is None:
            raise LookupError(f"no place with id {place_id}")
        if kind in ("home", "work"):
            _demote(db, row["vin"], kind)
        dbm.execute(
            db,
            "UPDATE place SET name = ?, kind = COALESCE(?, kind) WHERE id = ?",
            (name.strip()[:200], kind, place_id),
        )


def set_place_purpose(db: sqlite3.Connection, place_id: int, purpose: str | None) -> None:
    """Remember the purpose for trips ending here (None forgets it)."""
    if purpose is not None and purpose not in dbm.PURPOSES:
        raise ValueError(f"purpose must be one of {dbm.PURPOSES} or None")
    cur = dbm.execute(db, "UPDATE place SET default_purpose = ? WHERE id = ?", (purpose, place_id))
    if cur.rowcount == 0:
        raise LookupError(f"no place with id {place_id}")


# -- GPS lookups used by the recorder -----------------------------------------------------


def position_at_or_before(
    db: sqlite3.Connection, vin: str, ts: datetime, window_s: float
) -> tuple[float, float] | None:
    lo = dbm.iso(datetime.fromtimestamp(ts.timestamp() - window_s, UTC))
    row = dbm.query_one(
        db,
        "SELECT lat, lon FROM snapshot WHERE vin = ? AND ts <= ? AND ts >= ? "
        "AND lat IS NOT NULL ORDER BY ts DESC LIMIT 1",
        (vin, dbm.iso(ts), lo),
    )
    return (row["lat"], row["lon"]) if row else None


def fill_drive_ends(db: sqlite3.Connection, vin: str, skip_id: int | None, now: datetime) -> None:
    """Give closed drives their end position: first GPS snapshot at/after the drive end."""
    horizon = dbm.iso(datetime.fromtimestamp(now.timestamp() - 7 * 86400, UTC))
    for d in dbm.query(
        db,
        "SELECT id, end_ts FROM drive WHERE vin = ? AND end_lat IS NULL AND end_ts >= ? "
        "AND id IS NOT ?",
        (vin, horizon, skip_id),
    ):
        end = dbm.parse_ts(d["end_ts"])
        hi = dbm.iso(datetime.fromtimestamp(end.timestamp() + END_WINDOW_S, UTC))
        pos = dbm.query_one(
            db,
            "SELECT lat, lon FROM snapshot WHERE vin = ? AND ts >= ? AND ts <= ? "
            "AND lat IS NOT NULL ORDER BY ts LIMIT 1",
            (vin, d["end_ts"], hi),
        )
        if pos:
            dbm.execute(
                db,
                "UPDATE drive SET end_lat = ?, end_lon = ? WHERE id = ?",
                (pos["lat"], pos["lon"], d["id"]),
            )


# -- learning -------------------------------------------------------------------------------


def learn_places(
    db: sqlite3.Connection, vin: str, *, now: datetime | None = None, tz: tzinfo | None = None
) -> list[Place]:
    """Cluster drive endpoints into places and detect home. Idempotent. Returns all places."""
    now = now or datetime.now(UTC)
    with dbm.transaction(db):
        existing = list_places(db, vin)
        points: list[tuple[float, float, bool]] = []  # lat, lon, is_arrival
        for r in dbm.query(
            db,
            "SELECT start_lat, start_lon, end_lat, end_lon FROM drive WHERE vin = ? "
            "ORDER BY start_ts",
            (vin,),
        ):
            if r["start_lat"] is not None:
                points.append((r["start_lat"], r["start_lon"], False))
            if r["end_lat"] is not None:
                points.append((r["end_lat"], r["end_lon"], True))
        visits: dict[int, int] = {p.id: 0 for p in existing}
        clusters: list[list[float]] = []  # [sum_lat, sum_lon, n, arrivals]
        for lat, lon, arrival in points:
            p = match(existing, lat, lon)
            if p:
                visits[p.id] += arrival
                continue
            for c in clusters:
                if haversine_m(lat, lon, c[0] / c[2], c[1] / c[2]) <= CLUSTER_RADIUS_M:
                    break
            else:
                c = [0.0, 0.0, 0, 0]
                clusters.append(c)
            c[0] += lat
            c[1] += lon
            c[2] += 1
            c[3] += arrival
        for pid, n in visits.items():
            dbm.execute(db, "UPDATE place SET visits = ? WHERE id = ?", (n, pid))
        for c in clusters:
            if c[3] >= MIN_VISITS:
                dbm.execute(
                    db,
                    "INSERT INTO place (vin, name, lat, lon, kind, visits, created_ts) "
                    "VALUES (?, '', ?, ?, 'other', ?, ?)",
                    (vin, c[0] / c[2], c[1] / c[2], int(c[3]), dbm.iso(now)),
                )
        _detect_home(db, vin, tz)
    return list_places(db, vin)


def _detect_home(db: sqlite3.Connection, vin: str, tz: tzinfo | None) -> None:
    places = list_places(db, vin)
    if not places or any(p.kind == "home" for p in places):
        return  # nothing to choose from, or home already decided (auto or by the user)
    zone = tz or datetime.now().astimezone().tzinfo or UTC
    nights: dict[object, Place | None] = {}
    for r in dbm.query(
        db,
        "SELECT ts, lat, lon FROM snapshot WHERE vin = ? AND lat IS NOT NULL ORDER BY ts",
        (vin,),
    ):
        local = dbm.parse_ts(r["ts"]).astimezone(zone)
        if local.hour in HOME_NIGHT_HOURS and local.date() not in nights:
            nights[local.date()] = match(places, r["lat"], r["lon"])
    if len(nights) < HOME_MIN_NIGHTS:
        return
    counts: dict[int, int] = {}
    for p in nights.values():
        if p:
            counts[p.id] = counts.get(p.id, 0) + 1
    if not counts:
        return
    best = max(counts, key=lambda k: counts[k])
    if counts[best] / len(nights) >= HOME_CONFIDENCE:
        dbm.execute(db, "UPDATE place SET kind = 'home' WHERE id = ? AND kind = 'other'", (best,))


# -- geocode cache --------------------------------------------------------------------------


def _round(v: float) -> float:
    return round(v, 4)


def store_geocode(db: sqlite3.Connection, lat: float, lon: float, label: str) -> None:
    dbm.execute(
        db,
        "INSERT INTO geocode (lat_r, lon_r, label, ts) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(lat_r, lon_r) DO UPDATE SET label = excluded.label, ts = excluded.ts",
        (_round(lat), _round(lon), label.strip()[:300], dbm.iso(datetime.now(UTC))),
    )


def cached_geocode(db: sqlite3.Connection, lat: float, lon: float) -> str | None:
    return dbm.scalar(
        db,
        "SELECT label FROM geocode WHERE lat_r = ? AND lon_r = ?",
        (_round(lat), _round(lon)),
    )


def _endpoints(db: sqlite3.Connection, vin: str):
    for r in dbm.query(
        db,
        "SELECT start_lat, start_lon, end_lat, end_lon FROM drive WHERE vin = ? "
        "ORDER BY start_ts DESC",
        (vin,),
    ):
        yield r["end_lat"], r["end_lon"]
        yield r["start_lat"], r["start_lon"]


def pending_geocodes(
    db: sqlite3.Connection, vin: str, limit: int = 10
) -> list[tuple[float, float]]:
    """Rounded drive-endpoint coordinates with no place and no cached label, newest first."""
    places = list_places(db, vin)
    out: list[tuple[float, float]] = []
    for lat, lon in _endpoints(db, vin):
        if len(out) >= limit:
            break
        if lat is None or match(places, lat, lon):
            continue
        key = (_round(lat), _round(lon))
        if key not in out and cached_geocode(db, lat, lon) is None:
            out.append(key)
    return out


# -- relabelling and purpose rules --------------------------------------------------------------


def relabel_drives(db: sqlite3.Connection, vin: str) -> int:
    """Fill labels and rule-based purposes. Returns the number of drives changed.

    Labels: place name ("Unnamed place #id" if unnamed), else the geocode cache; drives
    whose labels were typed by the user are left alone. Purposes: home<->work is
    'commute', else the destination place's default_purpose; a purpose the user set is
    never overwritten, and a rule-set purpose is cleared again if no rule applies.
    """
    places = list_places(db, vin)
    changed = 0
    with dbm.transaction(db):
        for r in dbm.query(db, "SELECT * FROM drive WHERE vin = ?", (vin,)):
            sp = match(places, r["start_lat"], r["start_lon"])
            ep = match(places, r["end_lat"], r["end_lon"])
            touched = False
            if r["label_source"] in (None, "auto"):
                labels = []
                for place, lat, lon, cur in (
                    (sp, r["start_lat"], r["start_lon"], r["start_label"]),
                    (ep, r["end_lat"], r["end_lon"], r["end_label"]),
                ):
                    new = place.display_name if place else None
                    if new is None and lat is not None:
                        new = cached_geocode(db, lat, lon)
                    labels.append(new if new is not None else cur)
                if (labels[0], labels[1]) != (r["start_label"], r["end_label"]):
                    dbm.execute(
                        db,
                        "UPDATE drive SET start_label = ?, end_label = ?, label_source = 'auto' "
                        "WHERE id = ?",
                        (labels[0], labels[1], r["id"]),
                    )
                    touched = True
            if r["purpose"] is None or r["purpose_source"] == "rule":
                new_purpose = None
                if sp and ep and {sp.kind, ep.kind} == {"home", "work"}:
                    new_purpose = "commute"
                elif ep and ep.default_purpose:
                    new_purpose = ep.default_purpose
                if new_purpose != r["purpose"]:
                    dbm.execute(
                        db,
                        "UPDATE drive SET purpose = ?, purpose_source = ? WHERE id = ?",
                        (new_purpose, "rule" if new_purpose else None, r["id"]),
                    )
                    touched = True
            changed += touched
    return changed
