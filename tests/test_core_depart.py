# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tests for socks.core.depart (one-off "Depart by") with FakeBackend only."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# Run from a source checkout without installing: make `socks` and `tests.fakes` importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from socks.core import depart
from socks.core.cli import EXIT_FAIL, EXIT_OK, EXIT_USAGE, main
from socks.core.commands import Command, DepartureArg
from socks.core.config import Settings
from socks.core.depart import DeparturePlan, arm, disarm, is_due, maintain, make_plan
from tests.fakes import FakeBackend

NOW = datetime(2026, 10, 11, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import os
    import time

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Amsterdam"
    time.tzset()
    yield
    if old_tz is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old_tz
    time.tzset()


def _plan(hours: float = 13.5) -> DeparturePlan:
    return make_plan(NOW + timedelta(hours=hours), 90, 21.0, now=NOW)


async def test_arm_sends_three_commands_in_order() -> None:
    fb = FakeBackend()
    plan = _plan()  # 2026-10-12 07:30 UTC = 09:30 Amsterdam
    lines = await arm(fb, plan, now=NOW)
    assert [c for c, _ in fb.sent] == [
        Command.SET_CHARGE_LIMIT,
        Command.SET_TEMP,
        Command.SET_SCHEDULED_DEPARTURE,
    ]
    assert fb.sent[0][1] == 90 and fb.sent[1][1] == 21.0
    assert fb.sent[2][1] == DepartureArg(9 * 60 + 30, preconditioning=True, off_peak_charging=True)
    assert all(line.startswith("ok") for line in lines) and len(lines) == 3
    s = await fb.read_state()
    assert s.charge_limit == 90 and s.target_temp_c == 21.0
    assert s.scheduled_charging_mode == "DepartBy" and s.preconditioning_enabled


async def test_arm_stops_at_first_failure_and_validates() -> None:
    fb = FakeBackend(fail_commands=True)
    lines = await arm(fb, _plan(), now=NOW)
    assert len(lines) == 1 and lines[0].startswith("failed")
    past = DeparturePlan(NOW - timedelta(hours=1), 90, 21.0, NOW)
    assert (await arm(FakeBackend(), past, now=NOW))[0].startswith("failed")
    with pytest.raises(ValueError, match="7 days"):
        make_plan(NOW + timedelta(days=8), now=NOW)
    with pytest.raises(ValueError):
        make_plan(NOW + timedelta(hours=1), 101, now=NOW)


async def test_arm_warns_beyond_24h() -> None:
    lines = await arm(FakeBackend(), _plan(hours=50), now=NOW)
    assert lines[-1].startswith("note") and "daily" in lines[-1]


async def test_disarm_and_maintain_only_when_due(tmp_path: Path) -> None:
    fb = FakeBackend()
    await arm(fb, _plan(), now=NOW)
    settings = Settings(depart_plan=_plan())
    settings.save()
    assert await maintain(fb, settings, NOW + timedelta(hours=13.5)) is None  # at departure
    assert settings.depart_plan is not None
    assert not is_due(settings.depart_plan, NOW + timedelta(hours=13.5, minutes=14))
    assert await maintain(fb, settings, NOW + timedelta(hours=13.5, minutes=15)) == "cleared"
    assert fb.sent[-1] == (Command.SET_SCHEDULED_DEPARTURE, None)
    assert settings.depart_plan is None and Settings.load().depart_plan is None
    assert (await fb.read_state()).scheduled_charging_mode == "Off"
    assert await maintain(fb, settings, NOW + timedelta(days=1)) is None  # nothing left
    failing = Settings(depart_plan=_plan())
    line = await maintain(FakeBackend(fail_commands=True), failing, NOW + timedelta(days=1))
    assert line is not None and line.startswith("failed") and failing.depart_plan is not None
    assert (await disarm(FakeBackend())).startswith("ok")


def test_plan_persists_in_settings() -> None:
    plan = _plan()
    Settings(vin="LRW3E7EJXTC899247", depart_plan=plan).save()
    loaded = Settings.load()
    assert loaded.depart_plan == plan and loaded.vin == "LRW3E7EJXTC899247"
    f = Settings.paths().settings_file
    f.write_text(f.read_text().replace("charge_limit = 90", "charge_limit = 5"))
    assert Settings.load().depart_plan is None  # malformed plan ignored


def test_next_occurrence() -> None:
    at = depart.next_occurrence("07:30", NOW)  # 20:00 local -> tomorrow 07:30
    assert (at.hour, at.minute) == (7, 30) and at > NOW and at - NOW < timedelta(days=1)
    at = depart.next_occurrence("21:15", NOW)  # later today
    assert at.date() == NOW.astimezone().date()
    for bad in ("7", "25:00", "aa:bb"):
        with pytest.raises(ValueError):
            depart.next_occurrence(bad, NOW)


def test_next_occurrence_across_dst() -> None:
    # Europe/Amsterdam leaves summer time on 2026-10-25 03:00 CEST and enters it on
    # 2026-03-29 02:00 CET. 07:30 must stay 07:30 wall-clock on the far side of the change.
    for now, offset in (
        (datetime(2026, 10, 24, 19, 0, tzinfo=UTC), 1),  # Sat 21:00 CEST -> Sun 07:30 CET
        (datetime(2026, 10, 24, 23, 30, tzinfo=UTC), 1),  # Sun 01:30 CEST, same day
        (datetime(2026, 3, 28, 19, 0, tzinfo=UTC), 2),  # Sat 20:00 CET -> Sun 07:30 CEST
    ):
        at = depart.next_occurrence("07:30", now)
        assert (at.hour, at.minute) == (7, 30)
        assert at.utcoffset() == timedelta(hours=offset)
        assert at > now and at - now < timedelta(days=1)
    plan = make_plan(
        depart.next_occurrence("07:30", datetime(2026, 10, 24, 19, 0, tzinfo=UTC)),
        85,
        20.5,
        now=datetime(2026, 10, 24, 19, 0, tzinfo=UTC),
    )
    assert plan.minutes_after_midnight == 450


def test_cli_depart(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["depart"]) == EXIT_OK
    assert "No departure planned" in capsys.readouterr().out
    assert main(["--fake", "depart", "07:30", "--limit", "85", "--temp", "20.5"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "85 %" in out and "20.5 °C" in out and "07:30" in out
    plan = Settings.load().depart_plan
    assert plan is not None and plan.charge_limit == 85 and plan.minutes_after_midnight == 450
    assert main(["depart"]) == EXIT_OK
    assert "07:30" in capsys.readouterr().out
    assert main(["--fake", "depart", "--cancel"]) == EXIT_OK
    assert Settings.load().depart_plan is None
    assert main(["--fake", "depart", "7h"]) == EXIT_USAGE
    assert main(["depart", "07:30"]) == EXIT_FAIL  # no car configured
