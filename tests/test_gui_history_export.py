# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""History export menu logic, read-only drive rows and chart PNG/SVG export (offscreen)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from socks.gui._deps import seed_fake_history  # noqa: E402
from socks.gui.history_export import (  # noqa: E402
    ExportItem,
    default_filename,
    drive_rows,
    ensure_suffix,
    export_items,
    run_export,
)

VIN = "5YJ3E7EB0KF000001"


@pytest.fixture(scope="module")
def qapp() -> object:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    p = tmp_path_factory.mktemp("hist") / "history.sqlite"
    seed_fake_history(p, VIN, days=10)
    return p


def test_items_and_names() -> None:
    items = export_items(2026)
    assert [(i.kind, i.fmt) for i in items][:2] == [("drives", "csv"), ("drives", "json")]
    assert items[-1] == ExportItem("mileage", "csv", "Mileage register 2026 (CSV)")
    since = datetime(2026, 9, 11, 12, tzinfo=UTC)
    until = datetime(2026, 10, 11, 12, tzinfo=UTC)
    assert default_filename(items[0], since, until, 2026) == (
        "socks-drives-2026-09-11_2026-10-11.csv"
    )
    assert default_filename(items[-1], None, None, 2026) == "socks-mileage-register-2026.csv"
    assert ensure_suffix("/x/out", "json") == "/x/out.json"
    assert ensure_suffix("/x/out.JSON", "json") == "/x/out.JSON"


def test_drive_rows() -> None:
    start = datetime(2026, 10, 1, 7, 30, tzinfo=UTC)
    d = SimpleNamespace(start=start, end=start + timedelta(minutes=30), km=20.0, start_soc=80,
                        end_soc=76)  # fmt: skip
    row = drive_rows([d])[0]
    assert row["minutes"] == 30 and row["avg_kmh"] == pytest.approx(40.0)
    still = SimpleNamespace(start=start, end=start, km=0.0, start_soc=None, end_soc=None)
    assert drive_rows([still])[0]["avg_kmh"] is None


@pytest.mark.parametrize("kind", ["drives", "charges", "snapshots"])
@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_run_export(db_path: Path, tmp_path: Path, kind: str, fmt: str) -> None:
    out = tmp_path / f"out.{fmt}"
    n = run_export(db_path, VIN, ExportItem(kind, fmt, ""), str(out), None, None, "metric", 2026)
    assert n > 0 and out.is_file()
    assert (out.stat().st_mode & 0o777) == 0o600
    if fmt == "json":
        assert len(json.loads(out.read_text())["rows"]) == n


def test_mileage_register(db_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "reg.csv"
    year = datetime.now().year
    n = run_export(
        db_path, VIN, ExportItem("mileage", "csv", ""), str(out), None, None, "metric", year
    )
    assert out.read_text().startswith("date,") and n >= 0


def test_chart_export(qapp: object, tmp_path: Path) -> None:
    from datetime import date

    from socks.gui.widgets.chart import Chart

    c = Chart()
    c.resize(500, 240)
    c.set_series("bars", [(date(2026, 10, d), float(d)) for d in range(1, 11)], "Distance")
    assert c.save_image(str(tmp_path / "c.png"), "png")
    assert (tmp_path / "c.png").read_bytes()[:4] == b"\x89PNG"
    assert c.save_image(str(tmp_path / "c.svg"), "svg")
    assert "<svg" in (tmp_path / "c.svg").read_text()
