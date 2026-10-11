# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Queries over the history DB returning plain dataclasses for the GUI.

Everything is in SI-ish units (km, kWh, kW). Empty data yields zeros / empty lists, never
exceptions. Day boundaries use ``tz`` (default: the system local zone); a drive counts
toward the day it started.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from statistics import median, pstdev

from . import db as dbm
from . import units

PHONE_CHARGE_KWH = 0.013  # a typical smartphone battery, about 13 Wh
PLUGGED_GAP_CAP = timedelta(minutes=30)
MIN_SOC_FOR_RANGE = 20
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class Summary:
    total_km: float = 0.0
    km_7d: float = 0.0
    km_30d: float = 0.0
    kwh_charged_30d: float = 0.0
    cost_30d: float = 0.0
    sessions_30d: int = 0
    avg_wh_per_km: float = 0.0
    longest_drive_km: float = 0.0
    best_day_km: float = 0.0


@dataclass(frozen=True)
class DayKm:
    date: date
    km: float


@dataclass(frozen=True)
class ChargeSession:
    start: datetime
    end: datetime
    start_soc: int | None
    end_soc: int | None
    energy_kwh: float
    max_power_kw: float
    cost: float
    estimated: bool
    id: int = 0


@dataclass(frozen=True)
class BatteryHealth:
    first_range_at_100_km: float = 0.0
    latest_range_at_100_km: float = 0.0
    degradation_pct: float = 0.0
    samples: int = 0


@dataclass(frozen=True)
class PackEstimate:
    kwh: float
    samples: int
    stdev_kwh: float
    first: datetime
    last: datetime


@dataclass(frozen=True)
class _Drive:
    start: datetime
    end: datetime
    km: float
    start_soc: int | None
    end_soc: int | None


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


def _drives(db: sqlite3.Connection, vin: str, since: datetime | None = None) -> list[_Drive]:
    rows = db.execute(
        "SELECT start_ts, end_ts, start_odo_km, end_odo_km, start_soc, end_soc FROM drive "
        "WHERE vin = ? AND start_ts >= ? AND start_odo_km IS NOT NULL "
        "AND end_odo_km IS NOT NULL ORDER BY start_ts",
        (vin, dbm.iso(since) if since else ""),
    ).fetchall()
    out = []
    for r in rows:
        km = max(0.0, r["end_odo_km"] - r["start_odo_km"])
        out.append(
            _Drive(
                dbm.parse_ts(r["start_ts"]),
                dbm.parse_ts(r["end_ts"] or r["start_ts"]),
                km,
                r["start_soc"],
                r["end_soc"],
            )
        )
    return out


def _per_day(drives: list[_Drive], tz: tzinfo) -> dict[date, float]:
    days: dict[date, float] = {}
    for d in drives:
        key = d.start.astimezone(tz).date()
        days[key] = days.get(key, 0.0) + d.km
    return days


def _wh_per_km(drives: list[_Drive], pack_kwh: float) -> float:
    km = wh = 0.0
    for d in drives:
        if d.km < 0.5 or d.start_soc is None or d.end_soc is None or d.end_soc >= d.start_soc:
            continue  # too short, unknown, or regen/charging dominated
        km += d.km
        wh += (d.start_soc - d.end_soc) / 100.0 * pack_kwh * 1000.0
    return wh / km if km else 0.0


def summary(
    db: sqlite3.Connection,
    vin: str,
    since: datetime | None = None,
    *,
    tariff: float = 0.0,
    pack_kwh: float = dbm.DEFAULT_PACK_KWH,
    now: datetime | None = None,
    tz: tzinfo | None = None,
) -> Summary:
    now_u = _now(now)
    zone = dbm.local_zone(tz)
    drives = _drives(db, vin, since)
    cut7, cut30 = now_u - timedelta(days=7), now_u - timedelta(days=30)
    row = db.execute(
        "SELECT COALESCE(SUM(energy_added_kwh), 0) AS kwh, COUNT(*) AS n FROM charge_session "
        "WHERE vin = ? AND start_ts >= ?",
        (vin, dbm.iso(cut30)),
    ).fetchone()
    kwh = float(row["kwh"]) if row else 0.0
    per_day = _per_day(drives, zone)
    return Summary(
        total_km=sum(d.km for d in drives),
        km_7d=sum(d.km for d in drives if d.start >= cut7),
        km_30d=sum(d.km for d in drives if d.start >= cut30),
        kwh_charged_30d=kwh,
        cost_30d=kwh * tariff,
        sessions_30d=int(row["n"]) if row else 0,
        avg_wh_per_km=_wh_per_km(drives, pack_kwh),
        longest_drive_km=max((d.km for d in drives), default=0.0),
        best_day_km=max(per_day.values(), default=0.0),
    )


def daily_km(
    db: sqlite3.Connection,
    vin: str,
    days: int,
    *,
    now: datetime | None = None,
    tz: tzinfo | None = None,
) -> list[DayKm]:
    """One entry per day for the last ``days`` days, oldest first, zeros for empty days."""
    if days <= 0:
        return []
    zone = dbm.local_zone(tz)
    today = _now(now).astimezone(zone).date()
    first = today - timedelta(days=days - 1)
    start_local = datetime(first.year, first.month, first.day, tzinfo=zone)
    per_day = _per_day(_drives(db, vin, start_local), zone)
    return [
        DayKm(first + timedelta(days=i), per_day.get(first + timedelta(days=i), 0.0))
        for i in range(days)
    ]


def charge_sessions(
    db: sqlite3.Connection, vin: str, limit: int = 20, *, tariff: float = 0.0
) -> list[ChargeSession]:
    """Newest first."""
    rows = db.execute(
        "SELECT * FROM charge_session WHERE vin = ? ORDER BY start_ts DESC, id DESC LIMIT ?",
        (vin, max(0, limit)),
    ).fetchall()
    out = []
    for r in rows:
        kwh = float(r["energy_added_kwh"] or 0.0)
        out.append(
            ChargeSession(
                start=dbm.parse_ts(r["start_ts"]),
                end=dbm.parse_ts(r["end_ts"] or r["start_ts"]),
                start_soc=r["start_soc"],
                end_soc=r["end_soc"],
                energy_kwh=kwh,
                max_power_kw=float(r["max_power_kw"] or 0.0),
                cost=kwh * tariff,
                estimated=bool(r["estimated"]),
                id=int(r["id"]),
            )
        )
    return out


def soc_series(
    db: sqlite3.Connection, vin: str, since: datetime | None = None
) -> list[tuple[datetime, int]]:
    rows = dbm.tuples(db).execute(
        "SELECT ts, soc FROM snapshot WHERE vin = ? AND soc IS NOT NULL AND ts >= ? ORDER BY ts",
        (vin, dbm.iso(since) if since else ""),
    )
    parse = dbm.parse_ts
    return [(parse(ts), int(soc)) for ts, soc in rows]


def battery_health(db: sqlite3.Connection, vin: str) -> BatteryHealth:
    """Rated range extrapolated to 100 % (rated / soc * 100), first vs latest readings.

    Uses snapshots with soc >= 20 (low SoC readings are too coarse) and the median of the
    first / last up-to-5 samples to damp noise.
    """
    count = db.execute(
        "SELECT COUNT(*) FROM snapshot WHERE vin = ? AND soc >= ? AND rated_range_km > 0",
        (vin, MIN_SOC_FOR_RANGE),
    ).fetchone()[0]
    if not count:
        return BatteryHealth()
    n = min(5, max(1, count // 2))
    first = db.execute(
        "SELECT rated_range_km / soc * 100.0 FROM snapshot WHERE vin = ? AND soc >= ? "
        "AND rated_range_km > 0 ORDER BY ts LIMIT ?",
        (vin, MIN_SOC_FOR_RANGE, n),
    ).fetchall()
    latest = db.execute(
        "SELECT rated_range_km / soc * 100.0 FROM snapshot WHERE vin = ? AND soc >= ? "
        "AND rated_range_km > 0 ORDER BY ts DESC LIMIT ?",
        (vin, MIN_SOC_FOR_RANGE, n),
    ).fetchall()
    f, la = median(r[0] for r in first), median(r[0] for r in latest)
    return BatteryHealth(f, la, (f - la) / f * 100.0, count)


def charging_curve(db: sqlite3.Connection, vin: str, session_id: int) -> list[tuple[int, float]]:
    """(soc %, kW) points from the snapshots recorded inside one session."""
    sess = db.execute(
        "SELECT start_ts, end_ts FROM charge_session WHERE id = ? AND vin = ?", (session_id, vin)
    ).fetchone()
    if sess is None:
        return []
    rows = db.execute(
        "SELECT soc, charger_power_kw FROM snapshot WHERE vin = ? AND ts >= ? AND ts <= ? "
        "AND soc IS NOT NULL AND charger_power_kw IS NOT NULL AND charging = 'Charging' "
        "ORDER BY ts",
        (vin, sess["start_ts"], sess["end_ts"] or sess["start_ts"]),
    ).fetchall()
    return [(int(r["soc"]), float(r["charger_power_kw"])) for r in rows]


def nerd_stats(
    db: sqlite3.Connection,
    vin: str,
    *,
    units_: str = "metric",
    tz: tzinfo | None = None,
) -> dict[str, str]:
    """Fun facts as ready-to-show strings (label -> value)."""
    zone = dbm.local_zone(tz)
    drives = _drives(db, vin)
    n_sessions, total_kwh, charging_s = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(COALESCE(energy_added_kwh, 0.0)), 0.0), "
        "COALESCE(SUM(strftime('%s', COALESCE(end_ts, start_ts)) - strftime('%s', start_ts)), 0) "
        "FROM charge_session WHERE vin = ?",
        (vin,),
    ).fetchone()
    fastest = db.execute(
        "SELECT COALESCE(max_power_kw, 0.0), start_ts FROM charge_session WHERE vin = ? "
        "ORDER BY COALESCE(max_power_kw, 0.0) DESC, start_ts DESC, id DESC LIMIT 1",
        (vin,),
    ).fetchone()

    plugged_s = 0  # seconds between consecutive snapshots that start plugged in, capped
    cap = PLUGGED_GAP_CAP.total_seconds()
    prev_t: int | None = None
    prev_plugged = False
    for t, plugged_in in dbm.tuples(db).execute(
        "SELECT CAST(strftime('%s', ts) AS INTEGER), plugged_in FROM snapshot "
        "WHERE vin = ? ORDER BY ts",
        (vin,),
    ):
        if prev_t is not None and prev_plugged:
            plugged_s += min(t - prev_t, cap)
        prev_t, prev_plugged = t, bool(plugged_in)
    plugged = timedelta(seconds=plugged_s)
    charging = timedelta(seconds=charging_s)

    out: dict[str, str] = {}
    out["Time plugged in"] = units.fmt_duration(plugged)
    out["Time actually charging"] = units.fmt_duration(charging)
    out["Charging share of plugged time"] = (
        units.fmt_percent(min(100.0, charging / plugged * 100.0)) if plugged else units.DASH
    )
    out["Fastest charge"] = (
        f"{units.fmt_power(fastest[0])} on {dbm.parse_ts(fastest[1]).astimezone(zone):%Y-%m-%d}"
        if fastest and fastest[0] > 0
        else units.DASH
    )
    per_weekday = [0.0] * 7
    for d in drives:
        per_weekday[d.start.astimezone(zone).weekday()] += d.km
    best = max(range(7), key=lambda i: per_weekday[i])
    out["Most-driven weekday"] = (
        f"{_WEEKDAYS[best]} ({units.fmt_distance(per_weekday[best], units_, 0)})"
        if per_weekday[best] > 0
        else units.DASH
    )
    out["Total energy charged"] = units.fmt_energy(total_kwh)
    out["Phone charges"] = f"{round(total_kwh / PHONE_CHARGE_KWH):,}"
    out["Charge sessions"] = str(n_sessions)
    out["Longest drive"] = units.fmt_distance(max((d.km for d in drives), default=0.0), units_)
    return out


PACK_PLAUSIBLE_KWH = (40.0, 130.0)


def estimate_pack_kwh(
    db: sqlite3.Connection, vin: str, min_delta_soc: int = 15
) -> PackEstimate | None:
    """Learn the usable pack size from real (non-estimated) charge sessions.

    Each qualifying session implies energy / delta_soc * 100; the result is the median.
    Sessions with a smaller SoC delta, or an implied size outside 40-130 kWh, are ignored.
    """
    rows = db.execute(
        "SELECT start_ts, energy_added_kwh, start_soc, end_soc FROM charge_session "
        "WHERE vin = ? AND estimated = 0 AND energy_added_kwh > 0 "
        "AND start_soc IS NOT NULL AND end_soc IS NOT NULL AND end_soc - start_soc >= ? "
        "ORDER BY start_ts",
        (vin, max(1, min_delta_soc)),
    ).fetchall()
    vals: list[float] = []
    stamps: list[datetime] = []
    lo, hi = PACK_PLAUSIBLE_KWH
    for r in rows:
        implied = r["energy_added_kwh"] / (r["end_soc"] - r["start_soc"]) * 100.0
        if lo <= implied <= hi:
            vals.append(implied)
            stamps.append(dbm.parse_ts(r["start_ts"]))
    if not vals:
        return None
    return PackEstimate(median(vals), len(vals), pstdev(vals), stamps[0], stamps[-1])
