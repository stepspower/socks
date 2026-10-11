# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""GPS endpoints, places, auto-labels and purpose rules (no network)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, timedelta

import pytest
from test_history import T0, VIN, ShiftState, st

from socks.history import db as dbm
from socks.history import places, trips
from socks.history.recorder import Recorder

HOME = (52.3702, 4.8952)
WORK = (52.0907, 5.1214)
CLIENT = (51.9244, 4.4777)
SHOP = (52.2000, 5.0000)


def near(p, i=0):  # a few tens of metres of GPS jitter
    return {"latitude": p[0] + 0.0002 * ((i % 3) - 1), "longitude": p[1] + 0.0002 * (i % 2)}


@pytest.fixture
def rec(tmp_path):
    r = Recorder(tmp_path / "h.sqlite")
    yield r
    r.close()


def trip(rec, t0, odo0, a, b, km=10.0, i=0):
    """Parked at a, drive, parked at b (closing P read). Returns the odometer at the end."""
    rec.record(st(t0 - timedelta(minutes=5), odometer_km=odo0, **near(a, i)))
    rec.record(st(t0, odometer_km=odo0 + km / 2, shift=ShiftState.D, **near(a, i + 1)))
    rec.record(st(t0 + timedelta(minutes=5), odometer_km=odo0 + km, shift=ShiftState.D))
    rec.record(st(t0 + timedelta(minutes=10), odometer_km=odo0 + km, **near(b, i)))
    rec.record(st(t0 + timedelta(minutes=20), odometer_km=odo0 + km, **near(b, i + 1)))
    return odo0 + km


def commute_days(rec, n=3, start_day=0):
    odo = 1000.0 + 100 * start_day
    for k in range(n):
        day = T0 + timedelta(days=start_day + k)
        odo = trip(rec, day.replace(hour=7), odo, HOME, WORK, 35.0, k)
        odo = trip(rec, day.replace(hour=17), odo, WORK, HOME, 35.0, k)
        rec.record(st(day.replace(hour=3) + timedelta(days=1), odometer_km=odo, **near(HOME, k)))
    return odo


def test_migration_v2_to_v3(tmp_path):
    p = tmp_path / "v2.sqlite"
    con = sqlite3.connect(p)
    for stmt in (*dbm.SCHEMA_V1, *dbm.SCHEMA_V2):
        con.execute(stmt)
    con.execute("INSERT INTO meta VALUES ('schema_version', '2')")
    con.execute(
        "INSERT INTO drive (vin, start_ts, end_ts, start_odo_km, end_odo_km, purpose, "
        "start_label) VALUES (?, '2026-03-02T09:00:00Z', '2026-03-02T09:30:00Z', 1, 9, "
        "'business', 'Thuis')",
        (VIN,),
    )
    con.execute("INSERT INTO drive (vin, start_ts) VALUES (?, '2026-03-03T09:00:00Z')", (VIN,))
    con.execute(
        "INSERT INTO snapshot (vin, ts, source, soc) VALUES (?, '2026-03-02T09:00:00Z', 'ble', 5)",
        (VIN,),
    )
    con.commit()
    con.close()
    c = dbm.open(p)
    assert dbm.get_meta(c, "schema_version") == "3"
    rows = dbm.query(c, "SELECT purpose, purpose_source, label_source FROM drive ORDER BY id")
    assert [tuple(r) for r in rows] == [("business", "user", "user"), (None, None, None)]
    assert dbm.query_one(c, "SELECT lat, lon FROM snapshot")["lat"] is None
    assert places.list_places(c, VIN) == []
    c.close()


def test_snapshot_position_dedupe(rec):
    rec.record(st(T0, latitude=HOME[0], longitude=HOME[1]))
    rec.record(st(T0 + timedelta(minutes=1), latitude=HOME[0] + 0.0001, longitude=HOME[1]))  # 11 m
    assert dbm.scalar(rec.conn, "SELECT COUNT(*) FROM snapshot") == 1
    rec.record(st(T0 + timedelta(minutes=2), latitude=HOME[0] + 0.001, longitude=HOME[1]))  # 111 m
    assert dbm.scalar(rec.conn, "SELECT COUNT(*) FROM snapshot") == 2
    rec.record(st(T0 + timedelta(minutes=3)))  # no GPS: stored without coords only if changed
    assert dbm.scalar(rec.conn, "SELECT COUNT(*) FROM snapshot WHERE lat IS NULL") == 0


def test_endpoints_attached(rec):
    trip(rec, T0.replace(hour=9), 1000.0, HOME, WORK, 35.0)
    (d,) = trips.drives(rec.conn, VIN)
    assert places.haversine_m(d.start_lat, d.start_lon, *HOME) < 100
    assert places.haversine_m(d.end_lat, d.end_lon, *WORK) < 100


def test_endpoints_retroactive_odometer_jump(rec):
    rec.record(st(T0, odometer_km=1000.0, **near(HOME)))
    # hours later a cloud poll finds the car elsewhere with a higher odometer
    rec.record(st(T0 + timedelta(hours=3), odometer_km=1042.0, **near(WORK)))
    rec.record(st(T0 + timedelta(hours=3, minutes=20), odometer_km=1042.0, **near(WORK, 1)))
    (d,) = trips.drives(rec.conn, VIN)
    assert (d.start_odo_km, d.end_odo_km) == (1000.0, 1042.0)
    assert places.haversine_m(d.start_lat, d.start_lon, *HOME) < 100
    assert places.haversine_m(d.end_lat, d.end_lon, *WORK) < 100


def test_clustering_and_idempotence(rec):
    commute_days(rec, 3)
    trip(rec, T0.replace(hour=12) + timedelta(days=5), 5000.0, HOME, SHOP, 8.0)  # one-off
    ps = places.learn_places(rec.conn, VIN, tz=UTC)
    assert len(ps) == 2  # shop has a single visit
    assert sorted(p.visits for p in ps) == [3, 3]
    assert all(p.name == "" and p.kind in ("home", "other") for p in ps)
    again = places.learn_places(rec.conn, VIN, tz=UTC)
    assert [p.id for p in again] == [p.id for p in ps]


def test_home_detection_and_not_confident(rec, tmp_path):
    commute_days(rec, 4)
    ps = places.learn_places(rec.conn, VIN, tz=UTC)
    homes = [p for p in ps if p.kind == "home"]
    assert len(homes) == 1 and places.haversine_m(homes[0].lat, homes[0].lon, *HOME) < 100

    other = Recorder(tmp_path / "o.sqlite")  # nights split 50/50 -> not confident
    commute_days(other, 3)
    for k in range(3):  # overnight at work instead
        other.record(st(T0.replace(hour=3) + timedelta(days=10 + k), **near(WORK, k)))
    for k in range(3):
        other.record(st(T0.replace(hour=3) + timedelta(days=20 + k), **near(HOME, k)))
    # 3 home nights (from commute_days) + 3 + 3 work + ... home share = 6/9 < 0.7
    ps = places.learn_places(other.conn, VIN, tz=UTC)
    assert not any(p.kind == "home" for p in ps)
    other.close()


def test_labels_commute_rule_and_user_purpose(rec):
    commute_days(rec, 3)
    trip(rec, T0.replace(hour=12) + timedelta(days=5), 5000.0, WORK, CLIENT, 90.0)
    ps = places.learn_places(rec.conn, VIN, tz=UTC)
    home = next(p for p in ps if p.kind == "home")
    work = next(p for p in ps if p.id != home.id)
    places.name_place(rec.conn, home.id, "Thuis")
    places.name_place(rec.conn, work.id, "Kantoor", kind="work")
    ds = trips.drives(rec.conn, VIN)
    trips.set_drive_purpose(rec.conn, ds[0].id, "private")  # user overrides the rule
    assert places.relabel_drives(rec.conn, VIN) > 0
    ds = trips.drives(rec.conn, VIN)
    assert (ds[0].start_label, ds[0].end_label) == ("Thuis", "Kantoor")
    assert ds[0].purpose == "private"
    assert all(d.purpose == "commute" for d in ds[1:6])
    row = dbm.query_one(rec.conn, "SELECT purpose_source FROM drive WHERE id = ?", (ds[0].id,))
    assert row[0] == "user"
    row = dbm.query_one(rec.conn, "SELECT purpose_source FROM drive WHERE id = ?", (ds[1].id,))
    assert row[0] == "rule"
    assert ds[6].purpose is None  # work -> unknown client: no rule
    assert ds[6].end_label is None
    assert places.relabel_drives(rec.conn, VIN) == 0  # stable


def test_unnamed_label_and_learned_place_purpose(rec):
    commute_days(rec, 3)
    places.learn_places(rec.conn, VIN, tz=UTC)
    places.relabel_drives(rec.conn, VIN)
    assert trips.drives(rec.conn, VIN)[0].start_label.startswith("Unnamed place #")

    cid = places.add_place(rec.conn, VIN, "Klant BV", *CLIENT)
    odo = 5000.0
    for k in range(2):
        odo = trip(rec, T0.replace(hour=12) + timedelta(days=6 + k), odo, WORK, CLIENT, 90.0)
    ds = trips.drives(rec.conn, VIN)
    trips.set_drive_purpose(rec.conn, ds[-2].id, "business")  # learned for the place
    assert places.list_places(rec.conn, VIN)[-1].default_purpose == "business"
    places.relabel_drives(rec.conn, VIN)
    last = trips.drives(rec.conn, VIN)[-1]
    assert (last.purpose, last.end_label) == ("business", "Klant BV")
    places.set_place_purpose(rec.conn, cid, "private")
    places.relabel_drives(rec.conn, VIN)
    after = trips.drives(rec.conn, VIN)
    assert after[-1].purpose == "private" and after[-2].purpose == "business"  # user kept
    with pytest.raises(ValueError):
        places.set_place_purpose(rec.conn, cid, "fun")


def test_geocode_cache_round_trip(rec):
    trip(rec, T0.replace(hour=9), 1000.0, HOME, SHOP, 20.0)
    pend = places.pending_geocodes(rec.conn, VIN, 10)
    assert len(pend) == 2 and all(len(p) == 2 for p in pend)
    assert len(places.pending_geocodes(rec.conn, VIN, 1)) == 1
    for lat, lon in pend:
        places.store_geocode(rec.conn, lat + 0.00001, lon, f"Street {lat:.3f}")  # rounds equal
    assert places.pending_geocodes(rec.conn, VIN, 10) == []
    places.relabel_drives(rec.conn, VIN)
    (d,) = trips.drives(rec.conn, VIN)
    assert d.start_label.startswith("Street ") and d.end_label.startswith("Street ")
    trips.set_drive_labels(rec.conn, d.id, end_label="Mijn winkel")
    places.store_geocode(rec.conn, d.end_lat, d.end_lon, "Changed")
    places.relabel_drives(rec.conn, VIN)
    assert trips.drives(rec.conn, VIN)[0].end_label == "Mijn winkel"  # user label wins
