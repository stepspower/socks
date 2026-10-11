# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tests for socks-cli (fake backend and throwaway keys only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Run from a source checkout without installing: make `socks` and `tests.fakes` importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from socks.core import keys
from socks.core.cli import EXIT_FAIL, EXIT_OK, EXIT_REFUSED, format_status, main
from socks.core.config import Settings
from tests.fakes import make_state


@pytest.fixture(autouse=True)
def _xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def test_status_fake(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--fake", "status"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Battery" in out and "70 %" in out and "km" in out


def test_json_fake(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--fake", "json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["soc"] == 70 and data["charging"] == "Disconnected" and "raw" in data
    assert main(["--fake", "json", "--no-raw"]) == EXIT_OK
    assert "raw" not in json.loads(capsys.readouterr().out)


def test_cmd_gate(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--fake", "cmd", "unlock"]) == EXIT_REFUSED
    assert "--yes" in capsys.readouterr().err
    assert main(["--fake", "cmd", "unlock", "--yes"]) == EXIT_OK
    assert main(["--fake", "cmd", "set_charge_limit", "85"]) == EXIT_OK
    assert main(["--fake", "cmd", "set_charge_limit", "5"]) == EXIT_FAIL
    assert main(["--fake", "cmd", "self_destruct"]) == 2
    assert main(["cmd"]) == EXIT_OK
    assert "DANGEROUS" in capsys.readouterr().out


def test_status_without_setup_reports_reason(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["status"]) == EXIT_FAIL
    assert "not set up" in capsys.readouterr().err
    assert main(["--source", "cloud", "status"]) == EXIT_FAIL


def test_key_generate_import_public(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tesla = tmp_path / "config" / "tesla"
    src = tesla / "private_key.pem"
    keys.write_private_key(keys.generate_private_key(), src)
    (tesla / "vin").write_text("5YJ3E7EB0KF000001\n")
    assert main(["key", "import"]) == EXIT_OK  # defaults to $XDG_CONFIG_HOME/tesla
    out = capsys.readouterr().out
    assert "fingerprint" in out and "5YJ3E7EB0KF000001" in out
    assert "PRIVATE" not in out
    s = Settings.load()
    assert s.vin == "5YJ3E7EB0KF000001" and s.key_path == Settings.paths().key_file
    assert main(["key", "public"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "BEGIN PUBLIC KEY" in out and "PRIVATE" not in out
    assert main(["key", "generate"]) == EXIT_OK
    assert "already exists" in capsys.readouterr().out
    assert main(["key", "import", str(tmp_path / "missing.pem")]) == EXIT_FAIL


def test_format_status_imperial() -> None:
    text = format_status(make_state(doors_open=("trunk",)), imperial=True)
    assert " mi" in text and "°F" in text and "trunk" in text


def test_json_redacts_location_by_default(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import tests.fakes as fakes

    original = fakes.make_state

    def located(**kw: object) -> object:
        return original(**{"raw": {"drive_state": {"latitude": 50.1, "longitude": 14.4}}, **kw})

    monkeypatch.setattr(fakes, "make_state", located)
    assert main(["--fake", "json"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "latitude" not in out and "50.1" not in out
    assert main(["--fake", "json", "--with-location"]) == EXIT_OK
    assert "latitude" in capsys.readouterr().out


def test_sentry_off_needs_yes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--fake", "cmd", "sentry_off"]) == EXIT_REFUSED
    assert main(["--fake", "cmd", "set_temp", "inf"]) == EXIT_FAIL
