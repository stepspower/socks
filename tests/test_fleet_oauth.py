# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tests for socks.fleet.oauth against a local fake token server (no real network)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp
import pytest
from aiohttp import web

from socks.fleet import oauth
from socks.fleet.oauth import Endpoints, OAuthError, Tokens

ACCESS = "acc-SECRET-0001"
REFRESH = "ref-SECRET-0001"


class FakeTesla:
    """Fake fleet-auth token endpoint that enforces PKCE."""

    def __init__(self) -> None:
        self.forms: list[dict[str, str]] = []
        self.challenges: dict[str, str] = {}  # code -> challenge
        self.refresh_valid = {REFRESH}
        self.counter = 1
        self.fail_with: tuple[int, dict[str, Any]] | None = None

    async def token(self, request: web.Request) -> web.Response:
        form = {k: v for k, v in (await request.post()).items() if isinstance(v, str)}
        self.forms.append(form)
        if self.fail_with:
            return web.json_response(self.fail_with[1], status=self.fail_with[0])
        grant = form.get("grant_type")
        if grant == "authorization_code":
            challenge = self.challenges.pop(form.get("code", ""), None)
            verifier = form.get("code_verifier", "")
            digest = hashlib.sha256(verifier.encode()).digest()
            calc = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if challenge is None or calc != challenge:
                return web.json_response({"error": "invalid_grant"}, status=400)
            return self._issue()
        if grant == "refresh_token":
            rt = form.get("refresh_token", "")
            if rt not in self.refresh_valid:
                return web.json_response({"error": "invalid_grant"}, status=401)
            self.refresh_valid.discard(rt)  # single use, like Tesla
            return self._issue()
        if grant == "client_credentials":
            return web.json_response(
                {"access_token": "partner-SECRET", "expires_in": 28800, "token_type": "Bearer"}
            )
        return web.json_response({"error": "unsupported_grant_type"}, status=400)

    def _issue(self) -> web.Response:
        self.counter += 1
        rt = f"ref-SECRET-{self.counter:04d}"
        self.refresh_valid.add(rt)
        return web.json_response(
            {
                "access_token": f"acc-SECRET-{self.counter:04d}",
                "refresh_token": rt,
                "expires_in": 28800,
                "token_type": "Bearer",
                "scope": " ".join(oauth.DEFAULT_SCOPES),
            }
        )


@asynccontextmanager
async def serve(app: web.Application) -> AsyncIterator[str]:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@asynccontextmanager
async def fake_tesla() -> AsyncIterator[tuple[FakeTesla, Endpoints]]:
    fake = FakeTesla()
    app = web.Application()
    app.router.add_post("/oauth2/v3/token", fake.token)
    async with serve(app) as base:
        yield (
            fake,
            Endpoints(
                region="eu",
                authorize_url=f"{base}/oauth2/v3/authorize",
                token_url=f"{base}/oauth2/v3/token",
                audience="https://fleet-api.prd.eu.vn.cloud.tesla.com",
            ),
        )


def settings() -> SimpleNamespace:
    return SimpleNamespace(client_id="client-123", region="eu")


def run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def fake_browser(
    fake: FakeTesla, *, state_override: str | None = None, error: str | None = None
) -> tuple[Callable[[str], None], list[str]]:
    """Mimic Tesla's authorize page: record the challenge, redirect to the loopback."""
    opened: list[str] = []
    tasks: list[asyncio.Task[None]] = []

    def open_url(url: str) -> None:
        opened.append(url)
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        code = f"EU_code{len(opened)}"
        fake.challenges[code] = q["code_challenge"]
        params: dict[str, str] = {"state": state_override or q["state"]}
        if error:
            params["error"] = error
        else:
            params["code"] = code

        async def follow() -> None:
            async with aiohttp.ClientSession() as s:
                redirect = q["redirect_uri"].replace("localhost", "127.0.0.1")
                async with s.get(redirect, params=params) as resp:
                    await resp.read()

        tasks.append(asyncio.get_running_loop().create_task(follow()))

    return open_url, opened


# --------------------------------------------------------------------------- PKCE


def test_pkce_rfc7636_appendix_b_vector() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert oauth.pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_make_pkce_shape() -> None:
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    v1, c1 = oauth.make_pkce()
    v2, _ = oauth.make_pkce()
    assert 43 <= len(v1) <= 128 and set(v1) <= allowed
    assert v1 != v2
    assert c1 == oauth.pkce_challenge(v1) and "=" not in c1 and len(c1) == 43


def test_state_is_random() -> None:
    states = {oauth.make_state() for _ in range(50)}
    assert len(states) == 50 and all(len(s) >= 40 for s in states)


def test_authorize_url_params() -> None:
    ep = Endpoints.for_region("eu")
    url = oauth.build_authorize_url(ep, "cid", "http://localhost:5555/callback", "st", "ch")
    u = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert u.netloc == "auth.tesla.com" and u.path == "/oauth2/v3/authorize"
    assert q["response_type"] == "code" and q["client_id"] == "cid"
    assert q["code_challenge"] == "ch" and q["code_challenge_method"] == "S256"
    assert q["state"] == "st" and q["redirect_uri"] == "http://localhost:5555/callback"
    assert q["scope"].split() == list(oauth.DEFAULT_SCOPES)


def test_endpoints_regions() -> None:
    eu = Endpoints.for_region("eu")
    assert eu.audience == "https://fleet-api.prd.eu.vn.cloud.tesla.com"
    assert eu.token_url == "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
    assert Endpoints.for_region("cn").authorize_url.startswith("https://auth.tesla.cn/")
    with pytest.raises(OAuthError):
        Endpoints.for_region("mars")


# --------------------------------------------------------------------------- storage


def test_token_file_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    os.umask(0o022)
    t = Tokens(ACCESS, REFRESH, int(time.time()) + 100, "eu", "openid")
    path = oauth.save_tokens(t)
    assert path == tmp_path / "cfg" / "socks" / "tokens.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert oauth.load_tokens() == t
    assert not list(path.parent.glob("*.tmp"))
    # loosened permissions are tightened on load
    os.chmod(path, 0o644)
    oauth.load_tokens()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_existing_dir_is_tightened(tmp_path: Path) -> None:
    d = tmp_path / "socks"
    d.mkdir(mode=0o755)
    oauth.save_tokens(Tokens(ACCESS, REFRESH, 0, "eu"), d / "tokens.json")
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_repr_hides_secrets() -> None:
    t = Tokens(ACCESS, REFRESH, 0, "eu")
    assert ACCESS not in repr(t) and REFRESH not in repr(t)


def test_corrupt_token_file(tmp_path: Path) -> None:
    p = tmp_path / "tokens.json"
    p.write_text("{not json")
    os.chmod(p, 0o600)
    with pytest.raises(OAuthError) as ei:
        oauth.load_tokens(p)
    assert "corrupt" in str(ei.value)
    st = run(oauth.status(path=p))
    assert not st.logged_in


# --------------------------------------------------------------------------- flows


def test_login_flow(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    path = tmp_path / "socks" / "tokens.json"

    async def go() -> tuple[Tokens, FakeTesla, list[str]]:
        async with fake_tesla() as (fake, ep):
            browser, opened = fake_browser(fake)
            tokens = await oauth.login(
                settings(), endpoints=ep, path=path, open_browser=browser, timeout=10
            )
            return tokens, fake, opened

    tokens, fake, opened = run(go())
    form = fake.forms[0]
    assert form["grant_type"] == "authorization_code"
    assert form["client_id"] == "client-123"
    assert "client_secret" not in form
    assert form["audience"] == "https://fleet-api.prd.eu.vn.cloud.tesla.com"
    q = parse_qs(urlparse(opened[0]).query)
    assert form["redirect_uri"] == q["redirect_uri"][0]
    redirect = urlparse(form["redirect_uri"])
    assert redirect.hostname == "localhost" and redirect.path == "/callback"
    assert redirect.port and redirect.port > 1024
    assert tokens.access_token == "acc-SECRET-0002" and tokens.region == "eu"
    assert oauth.load_tokens(path) == tokens
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "SECRET" not in caplog.text and form["code_verifier"] not in caplog.text


def test_login_sends_secret_when_given(tmp_path: Path) -> None:
    async def go() -> FakeTesla:
        async with fake_tesla() as (fake, ep):
            browser, _ = fake_browser(fake)
            await oauth.login(
                settings(),
                client_secret="shh",
                endpoints=ep,
                path=tmp_path / "t.json",
                open_browser=browser,
                timeout=10,
            )
            return fake

    assert run(go()).forms[0]["client_secret"] == "shh"


def test_login_rejects_bad_state(tmp_path: Path) -> None:
    async def go() -> None:
        async with fake_tesla() as (fake, ep):
            browser, _ = fake_browser(fake, state_override="forged")
            await oauth.login(
                settings(),
                endpoints=ep,
                path=tmp_path / "t.json",
                open_browser=browser,
                timeout=10,
            )

    with pytest.raises(OAuthError, match="state mismatch"):
        run(go())
    assert not (tmp_path / "t.json").exists()


def test_login_authorization_denied(tmp_path: Path) -> None:
    async def go() -> None:
        async with fake_tesla() as (fake, ep):
            browser, _ = fake_browser(fake, error="access_denied")
            await oauth.login(
                settings(),
                endpoints=ep,
                path=tmp_path / "t.json",
                open_browser=browser,
                timeout=10,
            )

    with pytest.raises(OAuthError, match="access_denied"):
        run(go())


def test_login_timeout(tmp_path: Path) -> None:
    async def go() -> None:
        async with fake_tesla() as (_fake, ep):
            await oauth.login(
                settings(),
                endpoints=ep,
                path=tmp_path / "t.json",
                open_browser=lambda _u: None,
                timeout=0.2,
            )

    with pytest.raises(OAuthError, match="timed out"):
        run(go())


def test_loopback_binds_ipv4_loopback_only() -> None:
    async def go() -> tuple[int, int]:
        a, b = oauth.LoopbackServer("s"), oauth.LoopbackServer("s")
        await a.start()
        await b.start()
        try:
            async with aiohttp.ClientSession() as s:
                url = f"http://127.0.0.1:{a.port}/callback?state=s&code=x"
                async with s.get(url) as resp:
                    assert resp.status == 200
            assert (await a.wait(1)).code == "x"
            return a.port, b.port
        finally:
            await a.close()
            await b.close()

    pa, pb = run(go())
    assert pa != pb and pa > 0


def test_refresh_rotates_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    oauth.save_tokens(Tokens(ACCESS, REFRESH, 0, "eu", "openid"), path)

    async def go() -> tuple[Tokens, FakeTesla]:
        async with fake_tesla() as (fake, ep):
            t = await oauth.refresh(settings(), endpoints=ep, path=path)
            # the old refresh token is spent; a second refresh must use the new one
            t2 = await oauth.refresh(settings(), endpoints=ep, path=path)
            assert t2.refresh_token != t.refresh_token
            return t, fake

    t, fake = run(go())
    assert fake.forms[0] == {
        "grant_type": "refresh_token",
        "client_id": "client-123",
        "refresh_token": REFRESH,
    }
    assert t.refresh_token == "ref-SECRET-0002"
    assert oauth.load_tokens(path).refresh_token == "ref-SECRET-0003"  # type: ignore[union-attr]


def test_access_token_refreshes_only_when_expired(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    oauth.save_tokens(Tokens(ACCESS, REFRESH, int(time.time()) + 3600, "eu"), path)

    async def go() -> tuple[str, str, FakeTesla]:
        async with fake_tesla() as (fake, ep):
            fresh = await oauth.access_token(settings(), endpoints=ep, path=path)
            oauth.save_tokens(Tokens(ACCESS, REFRESH, 0, "eu"), path)
            r = await asyncio.gather(
                *[oauth.access_token(settings(), endpoints=ep, path=path) for _ in range(3)]
            )
            assert len(set(r)) == 1  # concurrent callers share one refresh
            return fresh, r[0], fake

    fresh, refreshed, fake = run(go())
    assert fresh == ACCESS
    assert refreshed == "acc-SECRET-0002"
    assert len(fake.forms) == 1


def test_token_error_does_not_leak(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    oauth.save_tokens(Tokens(ACCESS, REFRESH, 0, "eu"), path)

    async def go() -> None:
        async with fake_tesla() as (fake, ep):
            fake.fail_with = (401, {"error": "invalid_grant", "error_description": "expired"})
            await oauth.refresh(settings(), endpoints=ep, path=path)

    with pytest.raises(OAuthError) as ei:
        run(go())
    msg = str(ei.value)
    assert "invalid_grant" in msg and "401" in msg
    assert REFRESH not in msg and ACCESS not in msg


def test_status(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    assert not run(oauth.status(path=path)).logged_in
    exp = int(time.time()) + 600
    oauth.save_tokens(Tokens(ACCESS, REFRESH, exp, "eu", "openid offline_access"), path)
    st = run(oauth.status(path=path))
    assert st.logged_in and not st.expired and st.expires_at == exp
    assert st.scopes == ("openid", "offline_access") and st.has_refresh_token
    assert ACCESS not in repr(st) and REFRESH not in repr(st)
    assert json.loads(path.read_text())["region"] == "eu"


def test_missing_client_id(tmp_path: Path) -> None:
    with pytest.raises(OAuthError, match="client_id"):
        run(oauth.login(SimpleNamespace(client_id="", region="eu"), path=tmp_path / "t"))
