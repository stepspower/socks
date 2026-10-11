# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tests for socks.core.geocode against a local fake Nominatim (no real network)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

# Run from a source checkout without installing: make `socks` importable.
from socks.core import geocode
from socks.core.config import Settings
from socks.core.geocode import GeocodeRunner, format_label, reverse

REPLY = {
    "display_name": "12, Damrak, Centrum, Amsterdam, Noord-Holland, Nederland",
    "address": {"house_number": "12", "road": "Damrak", "city": "Amsterdam"},
}


@pytest.fixture
async def nominatim(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(geocode, "MIN_INTERVAL", 0.05)
    seen: list[dict[str, Any]] = []
    mode = {"status": 200, "body": REPLY}

    async def handler(request: web.Request) -> web.Response:
        seen.append(
            {"t": time.monotonic(), "q": dict(request.query), "ua": request.headers["User-Agent"]}
        )
        if mode["status"] != 200:
            return web.Response(status=mode["status"])
        if isinstance(mode["body"], str):
            return web.Response(text=mode["body"])
        return web.json_response(mode["body"])

    app = web.Application()
    app.router.add_get("/reverse", handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    async with aiohttp.ClientSession() as session:
        yield session, str(server.make_url("/reverse")), seen, mode
    await server.close()


async def test_reverse_label_and_request(nominatim) -> None:
    session, url, seen, _ = nominatim
    assert await reverse(52.3745, 4.8979, session=session, lang="nl", base_url=url) == (
        "Damrak 12, Amsterdam"
    )
    q = seen[0]["q"]
    assert q["format"] == "jsonv2" and q["zoom"] == "17" and q["accept-language"] == "nl"
    assert q["lat"] == "52.374500" and q["lon"] == "4.897900"
    assert seen[0]["ua"].startswith("SOCKS/") and "github.com/stepspower/socks" in seen[0]["ua"]


async def test_rate_limit_respected(nominatim) -> None:
    session, url, seen, _ = nominatim
    for _ in range(3):
        await reverse(52.0, 5.0, session=session, base_url=url)
    gaps = [b["t"] - a["t"] for a, b in zip(seen, seen[1:], strict=False)]
    assert len(gaps) == 2 and all(g >= 0.045 for g in gaps)


async def test_failures_return_none(nominatim) -> None:
    session, url, _, mode = nominatim
    mode["status"] = 500
    assert await reverse(52.0, 5.0, session=session, base_url=url) is None
    mode["status"], mode["body"] = 200, "not json"
    assert await reverse(52.0, 5.0, session=session, base_url=url) is None
    mode["body"] = {"error": "Unable to geocode"}
    assert await reverse(52.0, 5.0, session=session, base_url=url) is None
    assert await reverse(float("nan"), 5.0, session=session, base_url=url) is None
    assert await reverse(95.0, 5.0, session=session, base_url=url) is None
    dead = "http://127.0.0.1:9/reverse"  # nothing listens on the discard port
    assert await reverse(52.0, 5.0, session=session, base_url=dead) is None


def test_format_label() -> None:
    assert format_label(REPLY) == "Damrak 12, Amsterdam"
    assert format_label({"address": {"road": "Dorpsstraat", "village": "Ee"}}) == "Dorpsstraat, Ee"
    assert format_label({"address": {"road": "A1"}}) == "A1"
    assert format_label({"display_name": "Gate 3, Schiphol, Haarlemmermeer, NL"}) == (
        "Gate 3, Schiphol"
    )
    assert format_label({"address": {"town": "Delft"}}) == "Delft"
    assert format_label({}) is None and format_label("x") is None


@dataclass
class FakeSettings:
    geocode_enabled: bool = True


async def test_runner_bridges_history(nominatim, monkeypatch: pytest.MonkeyPatch) -> None:
    from socks.history import places

    session, url, seen, _ = nominatim
    stored: list[tuple[float, float, str]] = []
    relabelled: list[str] = []
    monkeypatch.setattr(places, "pending_geocodes", lambda db, vin, limit: [(52.0, 5.0)][:limit])
    monkeypatch.setattr(
        places, "store_geocode", lambda db, a, b, label: stored.append((a, b, label))
    )
    monkeypatch.setattr(places, "relabel_drives", lambda db, vin: relabelled.append(vin) or 1)
    runner = GeocodeRunner(FakeSettings(), session, base_url=url)
    assert await runner.run_once(object(), "VIN1") == 1
    assert stored == [(52.0, 5.0, "Damrak 12, Amsterdam")] and relabelled == ["VIN1"]
    off = GeocodeRunner(FakeSettings(geocode_enabled=False), session, base_url=url)
    assert await off.run_once(object(), "VIN1") == 0
    assert len(seen) == 1  # disabled: no request at all


def test_geocode_setting_is_opt_in() -> None:
    assert Settings().geocode_enabled is False
