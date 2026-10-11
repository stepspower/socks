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


def _zone(tz: tzinfo | None) -> tzinfo:
    return tz or datetime.now().astimezone().tzinfo or UTC


def _drives(db: sqlite3.Connection, vin: str, since: datetime | None = None) -> list[_Drive]:
    rows = dbm.query(
        db,
        "SELECT start_ts, end_ts, start_odo_km, end_odo_km, start_soc, end_soc FROM drive "
        "WHERE vin = ? AND start_ts >= ? AND start_odo_km IS NOT NULL "
        "AND end_odo_km IS NOT NULL ORDER BY start_ts",
        (vin, dbm.iso(since) if since else ""),
    )
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
    zone = _zone(tz)
    drives = _drives(db, vin, since)
    cut7, cut30 = now_u - timedelta(days=7), now_u - timedelta(days=30)
    row = dbm.query_one(
        db,
        "SELECT COALESCE(SUM(energy_added_kwh), 0) AS kwh, COUNT(*) AS n FROM charge_session "
        "WHERE vin = ? AND start_ts >= ?",
        (vin, dbm.iso(cut30)),
    )
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
    zone = _zone(tz)
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
    rows = dbm.query(
        db,
        "SELECT * FROM charge_session WHERE vin = ? ORDER BY start_ts DESC, id DESC LIMIT ?",
        (vin, max(0, limit)),
    )
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
    rows = dbm.query(
        db,
        "SELECT ts, soc FROM snapshot WHERE vin = ? AND soc IS NOT NULL AND ts >= ? ORDER BY ts",
        (vin, dbm.iso(since) if since else ""),
    )
    return [(dbm.parse_ts(r["ts"]), int(r["soc"])) for r in rows]


def battery_health(db: sqlite3.Connection, vin: str) -> BatteryHealth:
    """Rated range extrapolated to 100 % (rated / soc * 100), first vs latest readings.

    Uses snapshots with soc >= 20 (low SoC readings are too coarse) and the median of the
    first / last up-to-5 samples to damp noise.
    """
    rows = dbm.query(
        db,
        "SELECT rated_range_km, soc FROM snapshot WHERE vin = ? AND soc >= ? "
        "AND rated_range_km > 0 ORDER BY ts",
        (vin, MIN_SOC_FOR_RANGE),
    )
    vals = [r["rated_range_km"] / r["soc"] * 100.0 for r in rows]
    if not vals:
        return BatteryHealth()
    n = min(5, max(1, len(vals) // 2))
    first, latest = median(vals[:n]), median(vals[-n:])
    return BatteryHealth(first, latest, (first - latest) / first * 100.0, len(vals))


def charging_curve(db: sqlite3.Connection, vin: str, session_id: int) -> list[tuple[int, float]]:
    """(soc %, kW) points from the snapshots recorded inside one session."""
    sess = dbm.query_one(
        db,
        "SELECT start_ts, end_ts FROM charge_session WHERE id = ? AND vin = ?",
        (session_id, vin),
    )
    if sess is None:
        return []
    rows = dbm.query(
        db,
        "SELECT soc, charger_power_kw FROM snapshot WHERE vin = ? AND ts >= ? AND ts <= ? "
        "AND soc IS NOT NULL AND charger_power_kw IS NOT NULL AND charging = 'Charging' "
        "ORDER BY ts",
        (vin, sess["start_ts"], sess["end_ts"] or sess["start_ts"]),
    )
    return [(int(r["soc"]), float(r["charger_power_kw"])) for r in rows]


def nerd_stats(
    db: sqlite3.Connection,
    vin: str,
    *,
    units_: str = "metric",
    tz: tzinfo | None = None,
) -> dict[str, str]:
    """Fun facts as ready-to-show strings (label -> value)."""
    zone = _zone(tz)
    drives = _drives(db, vin)
    sessions = charge_sessions(db, vin, 1_000_000)

    plugged = timedelta()
    prev_ts: datetime | None = None
    prev_plugged = False
    for r in dbm.query(db, "SELECT ts, plugged_in FROM snapshot WHERE vin = ? ORDER BY ts", (vin,)):
        t = dbm.parse_ts(r["ts"])
        if prev_ts is not None and prev_plugged:
            plugged += min(t - prev_ts, PLUGGED_GAP_CAP)
        prev_ts, prev_plugged = t, bool(r["plugged_in"])
    charging = sum((s.end - s.start for s in sessions), timedelta())

    out: dict[str, str] = {}
    out["Time plugged in"] = units.fmt_duration(plugged)
    out["Time actually charging"] = units.fmt_duration(charging)
    out["Charging share of plugged time"] = (
        units.fmt_percent(min(100.0, charging / plugged * 100.0)) if plugged else units.DASH
    )
    fastest = max(sessions, key=lambda s: s.max_power_kw, default=None)
    out["Fastest charge"] = (
        f"{units.fmt_power(fastest.max_power_kw)} on {fastest.start.astimezone(zone):%Y-%m-%d}"
        if fastest and fastest.max_power_kw > 0
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
    total_kwh = sum(s.energy_kwh for s in sessions)
    out["Total energy charged"] = units.fmt_energy(total_kwh)
    out["Phone charges"] = f"{round(total_kwh / PHONE_CHARGE_KWH):,}"
    out["Charge sessions"] = str(len(sessions))
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
    rows = dbm.query(
        db,
        "SELECT start_ts, energy_added_kwh, start_soc, end_soc FROM charge_session "
        "WHERE vin = ? AND estimated = 0 AND energy_added_kwh > 0 "
        "AND start_soc IS NOT NULL AND end_soc IS NOT NULL AND end_soc - start_soc >= ? "
        "ORDER BY start_ts",
        (vin, max(1, min_delta_soc)),
    )
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
