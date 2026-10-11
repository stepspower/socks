# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tests for socks.fleet.charging against a local fake Fleet API (no real network)."""

from __future__ import annotations

import argparse
import asyncio
import json
import stat
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web

from socks.fleet import charging, cli, oauth
from socks.fleet.charging import ChargingSession
from socks.fleet.oauth import Endpoints, Tokens

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
TOKEN = "acc-SECRET-charging"


def session_json(i: int, site: str, day: str, content_id: str | None = None) -> dict[str, Any]:
    return {
        "sessionId": 1000 + i,
        "vin": "TEST00000000VIN01",
        "siteLocationName": site,
        "chargeStartDateTime": f"{day}T11:43:45+02:00",
        "chargeStopDateTime": f"{day}T12:08:35+02:00",
        "countryCode": "NL",
        "fees": [
            {
                "feeType": "CHARGING",
                "currencyCode": "EUR",
                "usageBase": 40,
                "usageTier2": 24,
                "totalDue": 18.4,
                "uom": "kwh",
            },
            {"feeType": "PARKING", "currencyCode": "EUR", "totalDue": 1.25, "uom": "min"},
        ],
        "billingType": "IMMEDIATE",
        "invoices": (
            [{"fileName": "X.pdf", "contentId": content_id, "invoiceType": "IMMEDIATE"}]
            if content_id
            else []
        ),
    }


SESSIONS = [
    session_json(1, "Amsterdam - Zuidas", "2026-09-01", "inv-1"),
    session_json(2, "../../etc/passwd", "2026-09-10", "../../evil"),
    session_json(3, "Utrecht, NL – Papendorp", "2026-10-01", "inv-3"),
]


class FakeFleet:
    def __init__(self) -> None:
        self.history_queries: list[dict[str, str]] = []
        self.invoice_ids: list[str] = []
        self.auth: set[str] = set()

    async def history(self, request: web.Request) -> web.Response:
        self.auth.add(request.headers.get("Authorization", ""))
        q = dict(request.query)
        self.history_queries.append(q)
        page, size = int(q["pageNo"]), int(q["pageSize"])
        chunk = SESSIONS[(page - 1) * size : page * size]
        return web.json_response({"response": {"data": chunk}, "totalResults": len(SESSIONS)})

    async def invoice(self, request: web.Request) -> web.Response:
        self.auth.add(request.headers.get("Authorization", ""))
        self.invoice_ids.append(request.match_info["id"])
        return web.Response(body=PDF, content_type="application/pdf")


@asynccontextmanager
async def fake_fleet() -> AsyncIterator[tuple[FakeFleet, Endpoints]]:
    fake = FakeFleet()
    app = web.Application()
    app.router.add_get("/api/1/dx/charging/history", fake.history)
    app.router.add_get("/api/1/dx/charging/invoice/{id}", fake.invoice)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    try:
        yield fake, Endpoints("eu", f"{base}/authorize", f"{base}/token", base)
    finally:
        await runner.cleanup()


@pytest.fixture
def token_path(tmp_path: Path) -> Path:
    path = tmp_path / "cfg" / "tokens.json"
    oauth.save_tokens(Tokens(TOKEN, "ref", int(time.time()) + 3600, "eu"), path)
    return path


SETTINGS = SimpleNamespace(client_id="cid", region="eu", vin="TEST00000000VIN01")


def test_history_pagination_and_parsing(token_path: Path) -> None:
    stats = charging.RequestStats()

    async def go() -> tuple[list[ChargingSession], FakeFleet]:
        async with fake_fleet() as (fake, ep):
            got = await charging.charging_history(
                SETTINGS, endpoints=ep, token_path=token_path, stats=stats, page_size=2
            )
            return got, fake

    sessions, fake = asyncio.run(go())
    assert [q["pageNo"] for q in fake.history_queries] == ["1", "2"]
    assert stats.history_pages == 2 and stats.total == 2
    assert fake.auth == {f"Bearer {TOKEN}"}
    assert fake.history_queries[0]["vin"] == "TEST00000000VIN01"
    assert len(sessions) == 3
    s = sessions[0]
    assert s.session_id == "1001" and s.site == "Amsterdam - Zuidas"
    assert s.energy_kwh == 40.0 and s.cost == 19.65 and s.currency == "EUR"
    assert s.invoice_ids == ("inv-1",)
    assert s.start == datetime(2026, 9, 1, 9, 43, 45, tzinfo=UTC)


def test_history_date_filter(token_path: Path) -> None:
    async def go() -> tuple[list[ChargingSession], FakeFleet]:
        async with fake_fleet() as (fake, ep):
            got = await charging.charging_history(
                SETTINGS,
                since=date(2026, 9, 5),
                until=date(2026, 9, 30),
                endpoints=ep,
                token_path=token_path,
            )
            return got, fake

    sessions, fake = asyncio.run(go())
    assert [s.session_id for s in sessions] == ["1002"]  # filtered client-side too
    assert fake.history_queries[0]["startTime"].startswith("2026-09-05T00:00:00")
    assert fake.history_queries[0]["endTime"].startswith("2026-10-01T00:00:00")


def test_slug_prevents_traversal() -> None:
    assert charging.slug("../../etc/passwd") == "etc-passwd"
    assert charging.slug("Utrecht, NL – Papendorp") == "utrecht-nl-papendorp"
    assert charging.slug("Zürich Hbf") == "zurich-hbf"
    assert charging.slug("....///") == "unknown"
    assert charging.slug("ä" * 200 + "b").count("a") <= 40
    name = charging.invoice_filename(charging.parse_session(SESSIONS[1]), "../../evil")
    assert name == "2026-09-10_etc-passwd_evil.pdf"
    assert "/" not in name and ".." not in name


def test_download_invoices_modes_and_skip(token_path: Path, tmp_path: Path) -> None:
    dest = tmp_path / "invoices" / "tesla"
    sessions = [charging.parse_session(x) for x in SESSIONS]
    stats = charging.RequestStats()

    async def go() -> tuple[list[Path], list[Path], FakeFleet]:
        async with fake_fleet() as (fake, ep):
            first = await charging.download_invoices(
                SETTINGS, sessions, dest, endpoints=ep, token_path=token_path, stats=stats
            )
            second = await charging.download_invoices(
                SETTINGS, sessions, dest, endpoints=ep, token_path=token_path, stats=stats
            )
            return first, second, fake

    first, second, fake = asyncio.run(go())
    assert [p.name for p in first] == [
        "2026-09-01_amsterdam-zuidas_inv-1.pdf",
        "2026-09-10_etc-passwd_evil.pdf",
        "2026-10-01_utrecht-nl-papendorp_inv-3.pdf",
    ]
    assert second == []  # all existed: skipped, no requests
    assert stats.invoices == 3
    assert fake.invoice_ids == ["inv-1", "../../evil", "inv-3"]  # sent URL-encoded
    assert all(p.parent == dest for p in first)
    assert all(p.read_bytes() == PDF for p in first)
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in first)
    assert stat.S_IMODE(dest.stat().st_mode) == 0o700
    assert not list(dest.glob(".*.tmp"))


def test_download_rejects_non_pdf(token_path: Path, tmp_path: Path) -> None:
    async def go() -> None:
        app = web.Application()

        async def bad(_r: web.Request) -> web.Response:
            return web.Response(text="<html>login</html>", content_type="text/html")

        app.router.add_get("/api/1/dx/charging/invoice/{id}", bad)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        base = f"http://127.0.0.1:{runner.addresses[0][1]}"
        try:
            await charging.download_invoices(
                SETTINGS,
                [charging.parse_session(SESSIONS[0])],
                tmp_path / "d",
                endpoints=Endpoints("eu", "", "", base),
                token_path=token_path,
            )
        finally:
            await runner.cleanup()

    with pytest.raises(charging.ChargingError, match="not a PDF"):
        asyncio.run(go())
    assert not list((tmp_path / "d").iterdir())


def test_summary_csv(tmp_path: Path) -> None:
    sessions = [charging.parse_session(x) for x in SESSIONS]
    evil = charging.parse_session(session_json(9, '=HYPERLINK("x")', "2026-10-02"))
    path = charging.write_summary_csv([*sessions, evil], tmp_path / "s.csv")
    rows = path.read_text().splitlines()
    assert rows[0].startswith("session_id,start,end,site,energy_kwh,cost,currency")
    assert len(rows) == 5
    assert "40,19.65,EUR" in rows[1]
    assert "'=HYPERLINK" in rows[4]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_after_download_hook_runs_without_shell(tmp_path: Path) -> None:
    out = tmp_path / "hook.json"
    files = [tmp_path / "a b.pdf", tmp_path / "$(touch pwned).pdf"]
    script = f"import json,sys; json.dump(sys.argv[1:], open({str(out)!r}, 'w'))"
    cmd = f"{sys.executable} -c {json.dumps(script)}"
    assert cli._run_hook(cmd, files) == 0
    assert json.loads(out.read_text()) == [str(f) for f in files]
    assert not (Path.cwd() / "pwned").exists()
    assert cli._run_hook(f"{sys.executable} -c 'raise SystemExit(3)'", files) == 3


def test_cli_charging_invokes_hook_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sessions = [charging.parse_session(x) for x in SESSIONS]
    new = [tmp_path / "x.pdf", tmp_path / "y.pdf"]

    async def fake_history(_s: Any, **kw: Any) -> list[ChargingSession]:
        kw["stats"].history_pages += 1
        assert kw["since"] == date(2026, 9, 1)
        return sessions

    async def fake_download(_s: Any, _sess: Any, dest: Path, **kw: Any) -> list[Path]:
        kw["stats"].invoices += 2
        assert dest == tmp_path / "dl"
        return new

    calls: list[tuple[str, list[Path]]] = []
    monkeypatch.setattr(charging, "charging_history", fake_history)
    monkeypatch.setattr(charging, "download_invoices", fake_download)
    monkeypatch.setattr(cli, "_settings", lambda _a: SETTINGS)
    monkeypatch.setattr(cli, "_run_hook", lambda c, f: calls.append((c, f)) or 0)

    parser = argparse.ArgumentParser()
    cli.add_fleet_parser(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(
        [
            "fleet",
            "charging",
            "--since",
            "2026-09-01",
            "--download",
            str(tmp_path / "dl"),
            "--after-download",
            "sendmail-invoices -v",
        ]
    )
    assert args.func(args) == 0
    assert calls == [("sendmail-invoices -v", new)]
    out = capsys.readouterr().out
    assert "3 sessions" in out
    assert "Fleet API requests made: 3 (1 history pages, 2 invoices)" in out


def test_cli_charging_bad_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_settings", lambda _a: SETTINGS)
    with pytest.raises(SystemExit):
        cli.fleet_charging(argparse.Namespace(since="01-09-2026"))
