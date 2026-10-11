# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tests for socks.fleet.partner and socks.fleet.cli (local fakes only)."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from socks.fleet import partner
from socks.fleet.cli import add_fleet_parser, fleet_login, fleet_register, fleet_status
from socks.fleet.oauth import Endpoints, OAuthError


def throwaway_pem(curve: ec.EllipticCurve | None = None) -> bytes:
    key = ec.generate_private_key(curve or ec.SECP256R1())
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


@asynccontextmanager
async def serve(app: web.Application) -> AsyncIterator[str]:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}"
    finally:
        await runner.cleanup()


class FakeFleet:
    def __init__(self) -> None:
        self.token_forms: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, Any, str]] = []

    async def token(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        self.token_forms.append(form)
        if form.get("client_secret") != "right-secret":
            return web.json_response({"error": "invalid_client"}, status=401)
        return web.json_response({"access_token": "partner-tok", "expires_in": 28800})

    async def partner_accounts(self, request: web.Request) -> web.Response:
        body = await request.json() if request.method == "POST" else dict(request.query)
        self.calls.append(
            (request.method, request.path, body, request.headers.get("Authorization", ""))
        )
        return web.json_response({"response": {"domain": body.get("domain")}})


@asynccontextmanager
async def fake_fleet() -> AsyncIterator[tuple[FakeFleet, Endpoints]]:
    fake = FakeFleet()
    app = web.Application()
    app.router.add_post("/oauth2/v3/token", fake.token)
    app.router.add_post("/api/1/partner_accounts", fake.partner_accounts)
    app.router.add_get("/api/1/partner_accounts/public_key", fake.partner_accounts)
    async with serve(app) as base:
        yield fake, Endpoints("eu", f"{base}/authorize", f"{base}/oauth2/v3/token", base)


SETTINGS = SimpleNamespace(client_id="cid", region="eu")


def test_register_uses_partner_token_and_library() -> None:
    async def go() -> tuple[dict[str, Any], dict[str, Any], FakeFleet, Endpoints]:
        async with fake_fleet() as (fake, ep):
            r = await partner.register(SETTINGS, "right-secret", "example.net", endpoints=ep)
            k = await partner.registered_public_key(
                SETTINGS, "right-secret", "example.net", endpoints=ep
            )
            return r, k, fake, ep

    result, key, fake, ep = asyncio.run(go())
    form = fake.token_forms[0]
    assert form["grant_type"] == "client_credentials"
    assert form["client_id"] == "cid" and form["audience"] == ep.audience
    assert "openid" in form["scope"].split()
    method, path, body, auth = fake.calls[0]
    assert (method, path, body) == ("POST", "/api/1/partner_accounts", {"domain": "example.net"})
    assert auth == "Bearer partner-tok"
    assert result == {"response": {"domain": "example.net"}}
    assert fake.calls[1][:3] == (
        "GET",
        "/api/1/partner_accounts/public_key",
        {"domain": "example.net"},
    )
    assert key["response"]["domain"] == "example.net"


def test_register_bad_secret_does_not_leak() -> None:
    async def go() -> None:
        async with fake_fleet() as (_fake, ep):
            await partner.register(SETTINGS, "wrong-secret-xyz", endpoints=ep)

    with pytest.raises(OAuthError) as ei:
        asyncio.run(go())
    assert "invalid_client" in str(ei.value) and "wrong-secret-xyz" not in str(ei.value)


def test_register_requires_secret_and_client_id() -> None:
    with pytest.raises(OAuthError):
        asyncio.run(partner.register(SETTINGS, ""))
    with pytest.raises(OAuthError):
        asyncio.run(partner.register(SimpleNamespace(client_id="", region="eu"), "s"))


def test_links() -> None:
    d = partner.DEFAULT_DOMAIN
    assert partner.public_key_url(d) == (
        f"https://{d}/.well-known/appspecific/com.tesla.3p.public-key.pem"
    )
    assert partner.pairing_link(d) == f"https://tesla.com/_ak/{d}"
    assert partner.pairing_link("d.net", "VIN1") == "https://tesla.com/_ak/d.net?vin=VIN1"


async def _check(
    served: bytes, local: bytes, ctype: str = "application/x-pem-file", status: int = 200
) -> partner.KeyCheck:
    async def handler(_r: web.Request) -> web.Response:
        return web.Response(body=served, status=status, headers={"Content-Type": ctype})

    app = web.Application()
    app.router.add_get(partner.WELL_KNOWN_PATH, handler)
    async with serve(app) as base:
        return await partner.check_public_key(
            "example.net", local, url=base + partner.WELL_KNOWN_PATH
        )


def test_check_public_key_match(tmp_path: Path) -> None:
    pem = throwaway_pem()
    p = tmp_path / "public_key.pem"
    p.write_bytes(pem)

    async def go() -> partner.KeyCheck:
        app = web.Application()

        async def h(_r: web.Request) -> web.Response:
            return web.Response(body=pem, content_type="text/plain")

        app.router.add_get(partner.WELL_KNOWN_PATH, h)
        async with serve(app) as base:
            return await partner.check_public_key("x", p, url=base + partner.WELL_KNOWN_PATH)

    r = asyncio.run(go())
    assert r.ok and r.byte_identical and r.http_status == 200


def test_check_public_key_whitespace_differs_but_same_key() -> None:
    pem = throwaway_pem()
    r = asyncio.run(_check(pem.replace(b"\n", b"\r\n"), pem))
    assert r.ok and not r.byte_identical


def test_check_public_key_failures() -> None:
    pem = throwaway_pem()
    assert asyncio.run(_check(throwaway_pem(), pem)).reason.startswith("served key differs")
    assert asyncio.run(_check(b"", pem, status=404)).reason == "HTTP 404"
    bad = asyncio.run(_check(pem, pem, ctype="application/pem-certificate-chain"))
    assert not bad.ok and "Content-Type" in bad.reason and bad.byte_identical
    assert "served key" in asyncio.run(_check(b"<html>", pem, ctype="text/plain")).reason
    p384 = throwaway_pem(ec.SECP384R1())
    assert "local key" in asyncio.run(_check(pem, p384)).reason


def test_cli_parser_wiring() -> None:
    parser = argparse.ArgumentParser(prog="socks-cli")
    add_fleet_parser(parser.add_subparsers(dest="cmd", required=True))
    a = parser.parse_args(["fleet", "login", "--port", "53682", "--no-browser"])
    assert a.func is fleet_login and a.port == 53682 and a.no_browser
    assert parser.parse_args(["fleet", "register"]).func is fleet_register
    assert parser.parse_args(["fleet", "status", "--check-key"]).func is fleet_status


def test_cli_status_not_logged_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    import socks.fleet.cli as cli

    monkeypatch.setattr(cli, "_settings", lambda _a: SimpleNamespace(client_id="", region="eu"))
    assert fleet_status(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert "Not logged in" in out and "NOT SET" in out
