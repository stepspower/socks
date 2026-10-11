# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""History tests on synthetic sequences (no car, no network)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from socks.core.model import ChargingState, ShiftState, Source, VehicleState
except ImportError:  # CORE's model not merged yet
    from _model_stub import ChargingState, ShiftState, Source, VehicleState

from socks.history import db as dbm
from socks.history import stats, units
from socks.history.recorder import Recorder

VIN = "5YJ3E1EA7KF000001"
T0 = datetime(2026, 3, 2, 8, 0, tzinfo=UTC)  # a Monday


def st(ts, **kw) -> VehicleState:
    base = dict(
        ts=ts, source=Source.BLE, vin=VIN, soc=50, usable_soc=50, charge_limit=80,
        charging=ChargingState.DISCONNECTED, charger_power_kw=None, charger_voltage=None,
        charger_current_a=None, charge_energy_added_kwh=None, minutes_to_limit=None,
        rated_range_km=None, ideal_range_km=None, plugged_in=False, charge_port_open=False,
        scheduled_charging_start=None, odometer_km=1000.0, shift=ShiftState.P,
        speed_kmh=0.0, power_kw=None, inside_temp_c=20.0, outside_temp_c=10.0,
        climate_on=False, target_temp_c=21.0, locked=True,
    )  # fmt: skip
    base.update(kw)
    return VehicleState(**base)


@pytest.fixture
def rec(tmp_path):
    r = Recorder(tmp_path / "data" / "history.sqlite")
    yield r
    r.close()


def feed_charge(rec, t0, source=Source.BLE, counter=True):
    """30 -> 80 % in 11 reads, 10 min apart, power ramping up to 11 kW, then Complete."""
    for i, soc in enumerate(range(30, 85, 5)):
        rec.record(
            st(
                t0 + timedelta(minutes=10 * i),
                source=source,
                soc=soc,
                charging=ChargingState.CHARGING,
                plugged_in=True,
                charger_power_kw=min(11.0, 4.0 + 2 * i),
                charge_energy_added_kwh=(soc - 30) / 100 * 75 if counter else None,
            )
        )
    rec.record(
        st(
            t0 + timedelta(minutes=110),
            soc=80,
            charging=ChargingState.COMPLETE,
            plugged_in=True,
            charge_energy_added_kwh=37.5 if counter else None,
        )
    )


def feed_drive(rec, t0, odo0, km, steps=6, soc0=80, soc1=75):
    """Park read, `steps` D reads one minute... 5 min apart, then P read (no odo change)."""
    rec.record(st(t0 - timedelta(minutes=5), odometer_km=odo0, soc=soc0))
    for i in range(1, steps + 1):
        rec.record(
            st(
                t0 + timedelta(minutes=5 * (i - 1)),
                odometer_km=odo0 + km * i / steps,
                shift=ShiftState.D,
                speed_kmh=50.0,
                soc=round(soc0 + (soc1 - soc0) * i / steps),
            )
        )
    rec.record(st(t0 + timedelta(minutes=5 * steps), odometer_km=odo0 + km, soc=soc1))


def test_schema_and_wal(rec):
    assert dbm.get_meta(rec.conn, "schema_version") == str(dbm.SCHEMA_VERSION)
    assert rec.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    mode = (Path(rec.conn.execute("PRAGMA database_list").fetchone()[2]).parent).stat().st_mode
    assert mode & 0o777 == 0o700


def test_charge_session_boundaries(rec):
    feed_charge(rec, T0)
    (s,) = stats.charge_sessions(rec.conn, VIN, 10, tariff=0.30)
    assert (s.start_soc, s.end_soc) == (30, 80)
    assert s.start == T0 and s.end == T0 + timedelta(minutes=110)
    assert s.energy_kwh == pytest.approx(37.5)
    assert s.max_power_kw == 11.0
    assert not s.estimated
    assert s.cost == pytest.approx(11.25)
    curve = stats.charging_curve(rec.conn, VIN, s.id)
    assert curve[0] == (30, 4.0) and max(kw for _, kw in curve) == 11.0
    assert [soc for soc, _ in curve] == sorted(soc for soc, _ in curve)


def test_charge_energy_estimate_when_no_counter(tmp_path):
    r = Recorder(tmp_path / "h.sqlite", pack_kwh=60.0)
    feed_charge(r, T0, counter=False)
    (s,) = stats.charge_sessions(r.conn, VIN, 10)
    assert s.estimated
    assert s.energy_kwh == pytest.approx(0.5 * 60.0)
    r.close()


def test_two_drives_with_lunch_gap(rec):
    feed_drive(rec, T0 + timedelta(hours=1), 1000.0, 20.0)
    feed_drive(rec, T0 + timedelta(hours=6), 1020.0, 15.0)
    rows = dbm.query(rec.conn, "SELECT * FROM drive ORDER BY start_ts")
    assert len(rows) == 2
    assert [(r["start_odo_km"], r["end_odo_km"]) for r in rows] == [
        (1000.0, 1020.0),
        (1020.0, 1035.0),
    ]
    assert dbm.parse_ts(rows[0]["end_ts"]) == T0 + timedelta(hours=1, minutes=25)
    s = stats.summary(rec.conn, VIN, now=T0 + timedelta(hours=8), tz=UTC)
    assert s.total_km == pytest.approx(35.0)
    assert s.km_7d == pytest.approx(35.0)
    assert s.longest_drive_km == pytest.approx(20.0)
    assert s.best_day_km == pytest.approx(35.0)
    assert s.avg_wh_per_km > 0  # 5 % of the pack over 20 km etc.


def test_drive_closed_by_gap_without_park_read(rec):
    rec.record(st(T0, odometer_km=1000.0, shift=ShiftState.D))
    rec.record(st(T0 + timedelta(minutes=5), odometer_km=1004.0, shift=ShiftState.D))
    rec.record(st(T0 + timedelta(minutes=30), odometer_km=1004.0, shift=ShiftState.P))
    (r,) = dbm.query(rec.conn, "SELECT * FROM drive")
    assert r["end_odo_km"] == 1004.0
    assert dbm.parse_ts(r["end_ts"]) == T0 + timedelta(minutes=5)
    assert dbm.get_meta(rec.conn, f"open_drive:{VIN}") is None


def test_drive_found_by_odometer_jump_while_parked(rec):
    rec.record(st(T0, odometer_km=1000.0))
    rec.record(st(T0 + timedelta(hours=1), odometer_km=1012.0))  # cloud poll, shift P
    (r,) = dbm.query(rec.conn, "SELECT * FROM drive")
    assert (r["start_odo_km"], r["end_odo_km"]) == (1000.0, 1012.0)


def test_day_without_data(rec):
    feed_drive(rec, T0 + timedelta(hours=1), 1000.0, 20.0)  # Mon
    feed_drive(rec, T0 + timedelta(days=2, hours=1), 1020.0, 15.0)  # Wed
    now = T0 + timedelta(days=2, hours=12)
    days = stats.daily_km(rec.conn, VIN, 4, now=now, tz=UTC)
    assert [d.date.isoformat() for d in days] == [
        "2026-02-28", "2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04"
    ][1:]  # fmt: skip
    assert [round(d.km, 1) for d in days] == [0.0, 20.0, 0.0, 15.0]


def test_dedupe_and_duplicate_timestamps(rec):
    for m in range(4):  # identical, 1 min apart
        rec.record(st(T0 + timedelta(minutes=m)))
    assert dbm.scalar(rec.conn, "SELECT COUNT(*) FROM snapshot") == 1
    rec.record(st(T0 + timedelta(minutes=5)))  # >= 5 min since last row
    assert dbm.scalar(rec.conn, "SELECT COUNT(*) FROM snapshot") == 2
    rec.record(st(T0 + timedelta(minutes=6), soc=49))  # changed field
    assert dbm.scalar(rec.conn, "SELECT COUNT(*) FROM snapshot") == 3
    rec.record(st(T0 + timedelta(minutes=6), soc=10))  # duplicate ts: ignored
    rec.record(st(T0 + timedelta(minutes=6), soc=49))
    assert dbm.scalar(rec.conn, "SELECT COUNT(*) FROM snapshot") == 3
    assert dbm.scalar(rec.conn, "SELECT soc FROM snapshot ORDER BY ts DESC LIMIT 1") == 49


def test_out_of_order_does_not_break_sessions(rec):
    feed_charge(rec, T0)
    rec.record(st(T0 + timedelta(minutes=3), soc=33, charging=ChargingState.CHARGING))
    assert dbm.scalar(rec.conn, "SELECT COUNT(*) FROM charge_session") == 1
    (s,) = stats.charge_sessions(rec.conn, VIN, 5)
    assert s.end == T0 + timedelta(minutes=110) and s.end_soc == 80


def test_mixed_ble_cloud_sources(rec):
    chg = dict(soc=30, charging=ChargingState.CHARGING, charger_power_kw=7.0, plugged_in=True)
    rec.record(st(T0, source=Source.BLE, **chg))
    rec.record(st(T0 + timedelta(minutes=1), source=Source.CLOUD, **chg))
    rec.record(st(T0 + timedelta(minutes=2), source=Source.CACHE, soc=99))
    chg["soc"] = 40
    rec.record(st(T0 + timedelta(minutes=20), source=Source.CLOUD, **chg))
    # source alone is not a change; the CACHE read is ignored
    assert dbm.scalar(rec.conn, "SELECT COUNT(*) FROM snapshot") == 2
    (s,) = stats.charge_sessions(rec.conn, VIN, 5)
    assert s.end_soc == 40
    assert dbm.scalar(rec.conn, "SELECT source FROM charge_session") == "mixed"


def test_battery_health_trend(rec):
    for i in range(10):
        r100 = 500.0 - i  # 500 -> 491 km at 100 %
        rec.record(st(T0 + timedelta(days=i * 30), soc=60, rated_range_km=r100 * 0.6))
    h = stats.battery_health(rec.conn, VIN)
    assert h.samples == 10
    assert h.first_range_at_100_km == pytest.approx(498.0)
    assert h.latest_range_at_100_km == pytest.approx(493.0)
    assert 0.9 < h.degradation_pct < 1.2
    assert dbm.get_meta(rec.conn, f"first_seen_rated_range_at_100:{VIN}") == "500.0"


def test_state_survives_reopen(tmp_path):
    p = tmp_path / "h.sqlite"
    r = Recorder(p)
    r.record(st(T0, charging=ChargingState.CHARGING, soc=30, plugged_in=True))
    r.close()
    r = Recorder(p)
    r.record(st(T0 + timedelta(minutes=5), charging=ChargingState.COMPLETE, soc=35))
    assert dbm.scalar(r.conn, "SELECT COUNT(*) FROM charge_session") == 1
    assert dbm.get_meta(r.conn, f"open_charge:{VIN}") is None
    r.close()


def test_empty_db_returns_zeros(rec):
    now = T0
    assert stats.summary(rec.conn, VIN, None, now=now) == stats.Summary()
    assert stats.summary(rec.conn, VIN, T0, now=now) == stats.Summary()
    assert [d.km for d in stats.daily_km(rec.conn, VIN, 3, now=now, tz=UTC)] == [0.0] * 3
    assert stats.charge_sessions(rec.conn, VIN, 10) == []
    assert stats.soc_series(rec.conn, VIN, None) == []
    assert stats.battery_health(rec.conn, VIN) == stats.BatteryHealth()
    assert stats.charging_curve(rec.conn, VIN, 1) == []
    nerd = stats.nerd_stats(rec.conn, VIN)
    assert nerd["Phone charges"] == "0" and nerd["Charge sessions"] == "0"


def test_nerd_stats_and_soc_series(rec):
    feed_charge(rec, T0)
    feed_drive(rec, T0 + timedelta(hours=4), 1000.0, 20.0)
    nerd = stats.nerd_stats(rec.conn, VIN, tz=UTC)
    assert nerd["Fastest charge"] == "11.0 kW on 2026-03-02"
    assert nerd["Most-driven weekday"] == "Monday (20 km)"
    assert nerd["Phone charges"] == f"{round(37.5 / 0.013):,}"
    series = stats.soc_series(rec.conn, VIN, T0 + timedelta(hours=1))
    assert series and all(t >= T0 + timedelta(hours=1) for t, _ in series)


def test_units():
    assert units.fmt_distance(160.9344, "imperial") == "100.0 mi"
    assert units.fmt_distance(12.34, "metric") == "12.3 km"
    assert units.fmt_temp(100.0, "imperial") == "212.0 °F"
    assert units.fmt_efficiency(155.0, "metric") == "155 Wh/km"
    assert units.fmt_distance(None) == units.DASH
    assert units.fmt_duration(timedelta(minutes=125)) == "2 h 05 min"
    assert units.mi_to_km(units.km_to_mi(42.0)) == pytest.approx(42.0)
    assert units.f_to_c(units.c_to_f(21.5)) == pytest.approx(21.5)


def add_session(rec, soc0, soc1, kwh, day, estimated=0):
    t = T0 + timedelta(days=day)
    dbm.execute(
        rec.conn,
        "INSERT INTO charge_session (vin, start_ts, end_ts, start_soc, end_soc, "
        "energy_added_kwh, max_power_kw, source, estimated) VALUES (?,?,?,?,?,?,?,?,?)",
        (VIN, dbm.iso(t), dbm.iso(t + timedelta(hours=1)), soc0, soc1, kwh, 7.0, "ble", estimated),
    )


def test_estimate_pack_median_outlier_and_tiny(rec):
    assert stats.estimate_pack_kwh(rec.conn, VIN) is None
    add_session(rec, 20, 70, 40.0, 0)  # 80.0
    add_session(rec, 30, 80, 39.0, 1)  # 78.0
    add_session(rec, 10, 60, 41.0, 2)  # 82.0
    add_session(rec, 50, 60, 2.0, 3)  # delta 10 < 15: skipped
    add_session(rec, 20, 50, 5.0, 4)  # implied 16.7 kWh: outlier
    add_session(rec, 20, 50, 80.0, 5)  # implied 266 kWh: outlier
    add_session(rec, 20, 80, 48.0, 6, estimated=1)  # estimated: skipped
    est = stats.estimate_pack_kwh(rec.conn, VIN)
    assert est is not None
    assert est.samples == 3 and est.kwh == pytest.approx(80.0)
    assert est.stdev_kwh == pytest.approx(1.633, abs=0.01)
    assert est.first == T0 and est.last == T0 + timedelta(days=2)


def test_recorder_learns_pack(tmp_path):
    p = tmp_path / "h.sqlite"
    r = Recorder(p)
    assert (r.pack_kwh, r.pack_source) == (80.0, "default")
    for i, kwh in enumerate((36.0, 36.0, 36.0)):  # 36 kWh over 40 % -> 90 kWh
        add_session(r, 30, 70, kwh, i)
    feed_charge(r, T0 + timedelta(days=10))  # completing a session re-evaluates
    assert r.pack_source == "learned" and r.pack_kwh == pytest.approx(90.0)
    r.close()
    r = Recorder(p, pack_kwh=60.0)
    assert (r.pack_kwh, r.pack_source) == (60.0, "configured")
    r.close()


# --- trips / export -------------------------------------------------------------------

import csv  # noqa: E402
import json  # noqa: E402
import sqlite3  # noqa: E402

from socks.history import export, trips  # noqa: E402


def make_drives(rec):
    """Three drives in 2026 (Mon 2 Mar, Tue 3 Mar) and one in 2025."""
    feed_drive(rec, T0 + timedelta(hours=1), 1000.0, 20.0)
    feed_drive(rec, T0 + timedelta(hours=6), 1020.0, 15.0)
    feed_drive(rec, T0 + timedelta(days=1, hours=1), 1035.0, 10.0)
    ids = [d.id for d in trips.drives(rec.conn, VIN)]
    assert len(ids) == 3
    return ids


def test_migration_from_v1_preserves_rows(tmp_path):
    p = tmp_path / "old.sqlite"
    con = sqlite3.connect(p)
    for stmt in dbm.SCHEMA_V1:
        con.execute(stmt)
    con.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    con.execute(
        "INSERT INTO drive (vin, start_ts, end_ts, start_odo_km, end_odo_km, start_soc, "
        "end_soc) VALUES (?, '2026-03-02T09:00:00Z', '2026-03-02T09:30:00Z', 1000, 1020, 80, 75)",
        (VIN,),
    )
    con.execute(
        "INSERT INTO snapshot (vin, ts, source, soc) VALUES (?, '2026-03-02T09:00:00Z', 'ble', 80)",
        (VIN,),
    )
    con.commit()
    con.close()
    c = dbm.open(p)
    assert dbm.get_meta(c, "schema_version") == "2"
    (d,) = trips.drives(c, VIN)
    assert (d.start_odo_km, d.end_odo_km, d.purpose, d.start_label) == (1000, 1020, None, None)
    assert dbm.scalar(c, "SELECT COUNT(*) FROM snapshot") == 1
    trips.set_drive_purpose(c, d.id, "business")  # new columns are usable
    c.close()
    c = dbm.open(p)  # reopening a v2 DB is a no-op
    assert trips.drives(c, VIN)[0].purpose == "business"
    c.close()


def test_purpose_labels_and_validation(rec):
    ids = make_drives(rec)
    trips.set_drive_purpose(rec.conn, ids[0], "business", note="client visit")
    trips.set_drive_labels(rec.conn, ids[0], start_label="Home", end_label="Amsterdam")
    trips.set_drive_purpose(rec.conn, ids[0], "commute")  # note kept
    d = trips.drives(rec.conn, VIN, purpose="commute")[0]
    assert (d.purpose, d.note, d.start_label, d.end_label) == (
        "commute", "client visit", "Home", "Amsterdam",
    )  # fmt: skip
    assert (d.start_lat, d.end_lon) == (None, None)
    trips.set_drive_labels(rec.conn, ids[0], end_label="")  # clear
    assert trips.drives(rec.conn, VIN)[0].end_label == ""
    trips.set_drive_purpose(rec.conn, ids[0], None)
    assert trips.drives(rec.conn, VIN)[0].purpose is None
    with pytest.raises(ValueError):
        trips.set_drive_purpose(rec.conn, ids[0], "fun")
    with pytest.raises(LookupError):
        trips.set_drive_purpose(rec.conn, 9999, "private")
    with pytest.raises(sqlite3.IntegrityError):  # DB-level CHECK as well
        rec.conn.execute("UPDATE drive SET purpose = 'x' WHERE id = ?", (ids[0],))


def test_mileage_summary_math(rec):
    ids = make_drives(rec)
    trips.set_drive_purpose(rec.conn, ids[0], "business")  # 20
    trips.set_drive_purpose(rec.conn, ids[1], "private")  # 15
    m = trips.mileage_summary(rec.conn, VIN, 2026, tz=UTC)  # 10 km unclassified
    assert (m.total_km, m.business_km, m.private_km, m.commute_km, m.unclassified_km, m.drives) == (
        pytest.approx(45.0), pytest.approx(20.0), pytest.approx(15.0), 0.0,
        pytest.approx(10.0), 3,
    )  # fmt: skip
    assert trips.mileage_summary(rec.conn, VIN, 2025, tz=UTC) == trips.MileageSummary()


def test_export_roundtrip_and_mode(rec, tmp_path):
    ids = make_drives(rec)
    trips.set_drive_purpose(rec.conn, ids[0], "business", note='=cmd, "quoted"\nline2')
    trips.set_drive_labels(rec.conn, ids[0], "Thuis", "Kantoor, Utrecht")
    feed_charge(rec, T0 + timedelta(days=2))
    out = tmp_path / "drives.csv"
    assert export.export_csv(rec.conn, VIN, "drives", out, tz=UTC) == 3
    assert out.stat().st_mode & 0o777 == 0o600
    with open(out, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 3
    assert rows[0]["km"] == "20.0" and rows[0]["start_odometer_km"] == "1000.0"
    assert rows[0]["start"] == "2026-03-02T08:55:00+00:00"
    assert rows[0]["end_label"] == "Kantoor, Utrecht"
    assert rows[0]["note"] == '\'=cmd, "quoted"\nline2'  # formula-guarded, quoting intact
    assert rows[2]["purpose"] == ""
    assert b"\r\n" in out.read_bytes()

    imp = tmp_path / "imp.csv"
    export.export_csv(rec.conn, VIN, "drives", imp, units="imperial", tz=UTC)
    with open(imp, encoding="utf-8", newline="") as fh:
        assert next(csv.DictReader(fh))["mi"] == "12.4"

    ch = tmp_path / "charges.csv"
    assert export.export_csv(rec.conn, VIN, "charges", ch, tz=UTC) == 1
    with open(ch, encoding="utf-8", newline="") as fh:
        (r,) = list(csv.DictReader(fh))
    assert r["energy_kwh"] == "37.50" or r["energy_kwh"] == "37.5"
    assert r["estimated"] == "False"

    sn = tmp_path / "snap.csv"
    n = dbm.scalar(rec.conn, "SELECT COUNT(*) FROM snapshot")
    assert export.export_csv(rec.conn, VIN, "snapshots", sn, tz=UTC) == n
    # since/until window: only the Mar 2 drives
    w = tmp_path / "w.csv"
    assert (
        export.export_csv(
            rec.conn, VIN, "drives", w, since=T0, until=T0 + timedelta(days=1), tz=UTC
        )
        == 2
    )
    with pytest.raises(ValueError):
        export.export_csv(rec.conn, VIN, "bogus", tmp_path / "x.csv")
    assert not list(tmp_path.glob(".*.tmp"))


def test_export_json(rec, tmp_path):
    make_drives(rec)
    out = tmp_path / "d.json"
    assert export.export_json(rec.conn, VIN, "drives", out, tz=UTC) == 3
    assert out.stat().st_mode & 0o777 == 0o600
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["vin"] == VIN and doc["units"] == "metric" and doc["schema_version"] == 2
    assert doc["exported_at"] and len(doc["rows"]) == 3
    assert doc["rows"][0]["km"] == 20.0


def test_mileage_register_csv(rec, tmp_path):
    ids = make_drives(rec)
    trips.set_drive_purpose(rec.conn, ids[0], "business")
    trips.set_drive_labels(rec.conn, ids[0], "Thuis", "Klant BV")
    out = tmp_path / "ritten.csv"
    assert export.export_mileage_register_csv(rec.conn, VIN, 2026, out, "metric", tz=UTC) == 3
    assert out.stat().st_mode & 0o777 == 0o600
    with open(out, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == [
            "date", "start_time", "end_time", "start_odometer_km", "end_odometer_km", "km",
            "purpose", "start_label", "end_label", "note",
        ]  # fmt: skip
        rows = list(reader)
    assert rows[0]["date"] == "2026-03-02" and rows[0]["start_time"] == "08:55"
    assert rows[0]["purpose"] == "business" and rows[0]["end_label"] == "Klant BV"
    assert sum(float(r["km"]) for r in rows) == pytest.approx(45.0)
    assert export.export_mileage_register_csv(rec.conn, VIN, 2025, tmp_path / "e.csv", tz=UTC) == 0
