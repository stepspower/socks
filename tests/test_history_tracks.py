# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""High-rate tracks, GPX, measured energy, vampire drain and sleep stats."""

from __future__ import annotations

import sqlite3
from datetime import UTC, timedelta
from xml.etree import ElementTree as ET  # noqa: S405

import pytest
from test_history import T0, VIN, ChargingState, ShiftState, st

from socks.history import db as dbm
from socks.history import export, stats, trips
from socks.history.recorder import Recorder

NS = {"g": "http://www.topografix.com/GPX/1/1", "s": "https://github.com/stepspower/socks/gpx/1"}


@pytest.fixture
def rec(tmp_path):
    r = Recorder(tmp_path / "h.sqlite", pack_kwh=80.0)
    yield r
    r.close()


def hi_rate_drive(rec, t0, n=10, step_s=30, kw=20.0, odo0=1000.0, soc=80):
    """Park read, then n in-gear reads `step_s` apart at constant power."""
    rec.record(st(t0 - timedelta(seconds=step_s), odometer_km=odo0, soc=soc))
    for i in range(n):
        rec.record(
            st(
                t0 + timedelta(seconds=step_s * i),
                odometer_km=odo0 + 0.5 * (i + 1),
                shift=ShiftState.D,
                speed_kmh=60.0 + i,
                power_kw=kw,
                soc=soc,
                latitude=52.0 + 0.001 * i,
                longitude=5.0 + 0.001 * i,
            )
        )
    return t0 + timedelta(seconds=step_s * (n - 1))


def count_points(rec):
    return dbm.scalar(rec.conn, "SELECT COUNT(*) FROM track_point")


def test_track_points_only_high_rate_in_gear(rec):
    last = hi_rate_drive(rec, T0)
    assert count_points(rec) == 10
    rec.record(st(last + timedelta(minutes=5), odometer_km=1010.0, shift=ShiftState.D))
    assert count_points(rec) == 10  # 5 min later: drive continues, no track point
    rec.record(st(last + timedelta(minutes=5, seconds=30), odometer_km=1010.2, shift=ShiftState.D))
    assert count_points(rec) == 11  # high-rate again; the skipped 5-min read stays out
    rec.record(st(last + timedelta(minutes=6), odometer_km=1010.2))  # P: closes, no point
    assert count_points(rec) == 11
    # snapshots stay sparse: far fewer than one per read when nothing but speed/odometer...
    assert dbm.scalar(rec.conn, "SELECT COUNT(DISTINCT drive_id) FROM track_point") == 1


def test_no_track_points_when_parked_or_slow_polling(rec):
    for i in range(5):
        rec.record(st(T0 + timedelta(seconds=30 * i)))  # parked at high rate
    for i in range(4):
        rec.record(
            st(T0 + timedelta(hours=1, minutes=3 * i), shift=ShiftState.D, odometer_km=1000 + i)
        )
    assert count_points(rec) == 0


def test_energy_integration_constant_power(rec):
    last = hi_rate_drive(rec, T0)  # 9 intervals x 30 s at 20 kW = 1.5 kWh
    rec.record(st(last + timedelta(seconds=30), odometer_km=1005.0))  # P, closes the drive
    (d,) = trips.drives(rec.conn, VIN)
    assert d.energy_source == "measured"
    assert d.energy_kwh == pytest.approx(1.5)
    s = stats.summary(rec.conn, VIN, now=last, tz=UTC)
    assert s.avg_wh_per_km == pytest.approx(
        1500.0 / 5.0
    )  # drive starts at the park read; SoC did not move: only power knows


def test_energy_falls_back_to_estimate(rec):
    last = hi_rate_drive(rec, T0)
    rec.record(st(last + timedelta(minutes=5), odometer_km=1010.0, shift=ShiftState.D, soc=78))
    rec.record(st(last + timedelta(minutes=6), odometer_km=1010.0, soc=78))
    (d,) = trips.drives(rec.conn, VIN)
    assert d.energy_source == "estimated"  # track covers far less than 90 % of the drive
    assert d.energy_kwh == pytest.approx(0.02 * 80.0)


def test_profile_and_gpx(rec, tmp_path):
    last = hi_rate_drive(rec, T0)
    rec.record(st(last + timedelta(seconds=30), odometer_km=1005.0))
    (d,) = trips.drives(rec.conn, VIN)
    prof = stats.drive_profile(rec.conn, d.id)
    assert len(prof) == 10 and prof[0] == (T0, 60.0, 20.0, 80)
    assert [p[0] for p in prof] == sorted(p[0] for p in prof)

    out = tmp_path / "t.gpx"
    assert export.export_gpx(rec.conn, d.id, out) == 10
    assert out.stat().st_mode & 0o777 == 0o600
    root = ET.parse(out).getroot()  # noqa: S314
    assert root.tag == f"{{{NS['g']}}}gpx" and root.get("version") == "1.1"
    pts = root.findall(".//g:trkpt", NS)
    assert len(pts) == 10
    assert pts[0].get("lat") == "52.000000" and pts[1].get("lon") == "5.001000"
    assert pts[0].find("g:time", NS).text == "2026-03-02T08:00:00Z"
    ext = pts[0].find("g:extensions", NS)
    assert ext.find("s:speed_kmh", NS).text == "60.0"
    assert ext.find("s:power_kw", NS).text == "20.00" and ext.find("s:soc", NS).text == "80"
    with pytest.raises(LookupError):
        export.export_gpx(rec.conn, 999, tmp_path / "x.gpx")
    trips_only = rec.conn.execute("INSERT INTO drive (vin, start_ts) VALUES (?, ?)", (VIN, "x"))
    with pytest.raises(ValueError):
        export.export_gpx(rec.conn, trips_only.lastrowid, tmp_path / "y.gpx")


def test_parked_stats_known_drain_and_sleep(rec):
    rec.record(st(T0, soc=80))
    rec.record(st(T0 + timedelta(hours=12), soc=79))
    rec.record(st(T0 + timedelta(hours=24), soc=78))
    for minutes, asleep in ((120, True), (140, True), (160, True), (180, False)):
        rec.record_unavailable(VIN, T0 + timedelta(minutes=minutes), asleep)
    rec.record_unavailable(VIN, T0 + timedelta(minutes=120), True)  # duplicate: ignored
    p = stats.parked_stats(rec.conn, VIN, None, pack_kwh=80.0)
    assert p.hours_parked == pytest.approx(24.0)
    assert p.soc_lost_pct == 2.0
    assert p.kwh_lost == pytest.approx(1.6)
    assert p.drain_pct_per_day == pytest.approx(2.0)
    assert p.hours_asleep_estimate == pytest.approx(1.0)
    assert stats.parked_stats(rec.conn, VIN, T0 + timedelta(hours=13)).soc_lost_pct == 0.0
    nerd = stats.nerd_stats(rec.conn, VIN, tz=UTC)
    assert nerd["Vampire drain"] == "2.0 %/day"
    assert nerd["Longest sleep"] == "1 h 00 min"
    assert nerd["Share of time asleep"] == "25 %"


def test_parked_stats_ignores_charging_and_driving(rec):
    rec.record(st(T0, soc=50))
    rec.record(
        st(T0 + timedelta(hours=1), soc=60, charging=ChargingState.CHARGING, plugged_in=True)
    )
    rec.record(st(T0 + timedelta(hours=2), soc=70, charging=ChargingState.COMPLETE))
    rec.record(st(T0 + timedelta(hours=3), soc=65, odometer_km=1030.0))  # drove: odometer moved
    p = stats.parked_stats(rec.conn, VIN, None)
    assert (p.hours_parked, p.soc_lost_pct, p.drain_pct_per_day) == (0.0, 0.0, 0.0)
    nerd = stats.nerd_stats(rec.conn, VIN, tz=UTC)
    assert nerd["Vampire drain"] == "—" and nerd["Share of time asleep"] == "—"


def test_migration_v4_to_v5(tmp_path):
    p = tmp_path / "v4.sqlite"
    con = sqlite3.connect(p)
    for stmt in (*dbm.SCHEMA_V1, *dbm.SCHEMA_V2, *dbm.SCHEMA_V3, *dbm.SCHEMA_V4):
        con.execute(stmt)
    con.execute("INSERT INTO meta VALUES ('schema_version', '4')")
    con.execute(
        "INSERT INTO drive (vin, start_ts, end_ts, start_odo_km, end_odo_km, purpose) "
        "VALUES (?, '2026-03-02T09:00:00Z', '2026-03-02T09:30:00Z', 1, 9, 'business')",
        (VIN,),
    )
    con.commit()
    con.close()
    c = dbm.open(p)
    assert dbm.get_meta(c, "schema_version") == "5"
    (d,) = trips.drives(c, VIN)
    assert (d.purpose, d.energy_kwh, d.energy_source) == ("business", None, None)
    c.execute("INSERT INTO track_point (drive_id, ts, power_kw) VALUES (?, 'a', 1.0)", (d.id,))
    c.execute("INSERT INTO availability VALUES (?, 'a', 1)", (VIN,))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE drive SET energy_source = 'guess' WHERE id = ?", (d.id,))
    c.close()
