# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tests for socks.core.keys and socks.core.config (throwaway keys, tmp dirs only)."""

from __future__ import annotations

# Run from a source checkout without installing: make `socks` and `tests.fakes` importable.
import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from socks.core import keys
from socks.core.config import Settings, paths


@pytest.fixture(autouse=True)
def _xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


# ---------------------------------------------------------------- VIN


@pytest.mark.parametrize("vin", ["5YJ3E7EB0KF000001", " 5yj3e7eb0kf000001 \n"])
def test_valid_vins(vin: str) -> None:
    assert keys.validate_vin(vin) == vin.strip().upper()


@pytest.mark.parametrize(
    "vin", ["", "5YJ3E7EB0KF00000", "5YJ3E7EB0KF0000011", "5YJ3E7EB0KF00000I", "5YJ3E7EBOKF000001",
            "QYJ3E7EB0KF000001", "5YJ3E7EB-KF000001"],
)  # fmt: skip
def test_invalid_vins(vin: str) -> None:
    with pytest.raises(ValueError):
        keys.validate_vin(vin)
    assert not keys.is_valid_vin(vin)


def test_read_vin_file(tmp_path: Path) -> None:
    f = tmp_path / "vin"
    f.write_text("5YJ3E7EB0KF000001\n")
    assert keys.read_vin_file(f) == "5YJ3E7EB0KF000001"
    f.write_text("garbage")
    assert keys.read_vin_file(f) is None
    assert keys.read_vin_file(tmp_path / "missing") is None


# ---------------------------------------------------------------- keys


def test_generate_writes_0600_in_0700_dir(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "socks" / "private_key.pem"
    key, created = keys.ensure_key(path)
    assert created
    assert isinstance(key.curve, ec.SECP256R1)
    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700
    assert (path.parent / "public_key.pem").read_bytes() == keys.public_key_pem(key)
    again, created2 = keys.ensure_key(path)
    assert not created2
    assert again.private_numbers() == key.private_numbers()
    with pytest.raises(keys.KeyFileError):
        keys.write_private_key(key, path)


def test_import_existing_key(tmp_path: Path) -> None:
    src_dir = tmp_path / "tesla"
    src_dir.mkdir()
    src = src_dir / "private_key.pem"
    original = keys.generate_private_key()
    keys.write_private_key(original, src)
    dest = tmp_path / "socks" / "private_key.pem"
    imported = keys.import_private_key(src, dest)
    assert imported.private_numbers() == original.private_numbers()
    assert _mode(dest) == 0o600
    assert keys.load_private_key(dest).private_numbers() == original.private_numbers()
    # Same key again: no-op. Different key: refused unless overwrite.
    keys.import_private_key(src, dest)
    other = tmp_path / "other.pem"
    keys.write_private_key(keys.generate_private_key(), other)
    with pytest.raises(keys.KeyFileError, match="different key"):
        keys.import_private_key(other, dest)
    keys.import_private_key(other, dest, overwrite=True)
    assert keys.load_private_key(dest).private_numbers() != original.private_numbers()


def test_load_refuses_world_readable_key(tmp_path: Path) -> None:
    path = tmp_path / "k.pem"
    keys.write_private_key(keys.generate_private_key(), path)
    path.chmod(0o644)
    with pytest.raises(keys.KeyFileError, match="chmod 600"):
        keys.load_private_key(path)


def test_load_rejects_wrong_curve_and_garbage_without_leaking(tmp_path: Path) -> None:
    p384 = tmp_path / "p384.pem"
    keys.write_secret_file(p384, keys.private_key_pem(ec.generate_private_key(ec.SECP384R1())))
    with pytest.raises(keys.KeyFileError, match="P-256"):
        keys.load_private_key(p384)
    junk = tmp_path / "junk.pem"
    secret = b"-----BEGIN PRIVATE KEY-----\nTOPSECRETMATERIAL\n-----END PRIVATE KEY-----\n"
    keys.write_secret_file(junk, secret)
    with pytest.raises(keys.KeyFileError) as exc:
        keys.load_private_key(junk)
    assert "TOPSECRET" not in str(exc.value)
    assert exc.value.__cause__ is None
    with pytest.raises(keys.KeyFileError, match="no private key"):
        keys.load_private_key(tmp_path / "nope.pem")


def test_public_key_and_fingerprint() -> None:
    key = keys.generate_private_key()
    pem = keys.public_key_pem(key)
    assert pem.startswith(b"-----BEGIN PUBLIC KEY-----")
    assert b"PRIVATE" not in pem
    fp = keys.public_key_fingerprint(key)
    assert fp == keys.public_key_fingerprint(key.public_key())
    assert len(fp.split(":")) == 8


def test_tesla_control_dir_follows_xdg(tmp_path: Path) -> None:
    assert keys.tesla_control_dir() == tmp_path / "config" / "tesla"


# ---------------------------------------------------------------- settings


def test_paths_follow_xdg(tmp_path: Path) -> None:
    p = paths()
    assert p.config == tmp_path / "config" / "socks"
    assert p.data == tmp_path / "data" / "socks"
    assert p.history_db == tmp_path / "data" / "socks" / "history.sqlite"
    assert Settings().key_path == p.config / "private_key.pem"
    assert Settings.paths() == p


def test_settings_roundtrip(tmp_path: Path) -> None:
    s = Settings(
        vin="5YJ3E7EB0KF000001",
        units="imperial",
        prefer="cloud",
        cloud_enabled=True,
        client_id='abc"def\\x',
        region="na",
        poll_seconds=900,
        tariff_eur_per_kwh=0.25,
        currency="CZK",
    )
    s.save()
    f = paths().settings_file
    assert _mode(f) == 0o600
    assert _mode(f.parent) == 0o700
    assert Settings.load() == s


def test_settings_missing_file_gives_defaults() -> None:
    assert Settings.load() == Settings()


def test_settings_bad_values_fall_back(tmp_path: Path) -> None:
    f = tmp_path / "s.toml"
    f.write_text(
        'vin = "5YJ3E7EB0KF000001"\nunits = "furlongs"\npoll_seconds = -5\n'
        'cloud_enabled = "yes"\ntariff_eur_per_kwh = 0.3\nregion = "eu"\nunknown = 1\n'
        'key_path = "~/k.pem"\n'
    )
    s = Settings.load(f)
    assert s.vin == "5YJ3E7EB0KF000001"
    assert s.units == "metric"
    assert s.poll_seconds == 0
    assert s.cloud_enabled is False
    assert s.tariff_eur_per_kwh == 0.3
    assert s.key_path == Path.home() / "k.pem"


def test_settings_corrupt_file(tmp_path: Path) -> None:
    f = tmp_path / "s.toml"
    f.write_text("this is = = not toml")
    assert Settings.load(f) == Settings()


def test_load_refuses_symlinked_key(tmp_path: Path) -> None:
    real = tmp_path / "real.pem"
    keys.write_private_key(keys.generate_private_key(), real)
    link = tmp_path / "link.pem"
    link.symlink_to(real)
    with pytest.raises(keys.KeyFileError, match="symlink"):
        keys.load_private_key(link)
    with pytest.raises(keys.KeyFileError, match="regular file"):
        keys.load_private_key(tmp_path)


def test_poll_floor_clamped_on_load_and_save(tmp_path: Path) -> None:
    s = Settings(poll_seconds=5)
    assert s.min_poll_seconds() == 60
    s.save()
    assert s.poll_seconds == 60 and Settings.load().poll_seconds == 60
    s = Settings(poll_seconds=120, cloud_enabled=True)
    assert s.min_poll_seconds() == 600
    s.save()
    assert Settings.load().poll_seconds == 600
    f = tmp_path / "s.toml"
    f.write_text("poll_seconds = 1\ncloud_enabled = true\n")
    assert Settings.load(f).poll_seconds == 600
    f.write_text("poll_seconds = 0\ncloud_enabled = true\n")
    assert Settings.load(f).poll_seconds == 0  # off stays off


def test_settings_invalid_vin_ignored(tmp_path: Path) -> None:
    f = tmp_path / "s.toml"
    f.write_text('vin = "5YJ3E7EBOKF000001"\n')
    assert Settings.load(f).vin == ""
    f.write_text('vin = "5yj3e7eb0kf000001"\n')
    assert Settings.load(f).vin == "5YJ3E7EB0KF000001"


def test_new_settings_defaults_and_trip_interval_floor(tmp_path: Path) -> None:
    s = Settings()
    assert s.location_enabled is True
    assert s.trip_watch_enabled is False and s.trip_watch_interval_seconds == 600
    f = tmp_path / "s.toml"
    f.write_text("trip_watch_interval_seconds = 30\nlocation_enabled = false\n")
    loaded = Settings.load(f)
    assert loaded.trip_watch_interval_seconds == 300 and loaded.location_enabled is False
