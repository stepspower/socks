# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tesla Fleet API OAuth: authorization code + PKCE with a loopback redirect.

Flow (``login``):

1. Generate a PKCE verifier/challenge (S256) and a random ``state``.
2. Start a one-shot HTTP server on ``127.0.0.1`` and ``::1`` on the first free port of
   ``REDIRECT_PORTS`` (53682, 53683, 53684; all registered in the Tesla portal).
3. Open ``https://auth.tesla.com/oauth2/v3/authorize?...`` in the browser.
4. The browser is redirected to ``http://localhost:<port>/callback?code=..&state=..``;
   the state is compared in constant time, then the code is exchanged at the
   Fleet auth token endpoint together with the verifier.
5. Tokens are written to ``$XDG_CONFIG_HOME/socks/tokens.json`` (file 0600,
   directory 0700), atomically.

Refresh tokens are single-use, so every refresh is persisted before returning.
Token values never appear in logs, exceptions or ``repr``.

``tesla_fleet_api.TeslaFleetOAuth`` is not used for the flow itself because it
has no PKCE support, requires the client secret, and hard-codes the token URL
(which would make it untestable without the real network). Region names,
Fleet API base URLs and scope names come from ``tesla_fleet_api.const``.
"""

from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import json
import logging
import os
import secrets
import socket
import time
import weakref
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlencode

if TYPE_CHECKING:
    import aiohttp

log = logging.getLogger(__name__)

TOKEN_FILE_NAME = "tokens.json"  # noqa: S105 - a file name
# Tesla's portal only accepts http:// redirect URIs on "localhost" (127.0.0.1 is rejected),
# so the browser may resolve it to either 127.0.0.1 or ::1. We therefore hold the SAME port
# on BOTH loopback addresses, so no other local user can squat the side we did not bind
# (audit M1, RFC 8252 §8.3).
REDIRECT_HOST = "localhost"
BIND_HOST = "127.0.0.1"
BIND_HOST_V6 = "::1"
# Tesla requires an explicit port on http:// redirect URIs and cannot register random ports,
# so exactly these are registered in the portal (http://localhost:<port>/callback), tried in order.
REDIRECT_PORTS: tuple[int, ...] = (53682, 53683, 53684)
MAX_BAD_CALLBACKS = 20  # stray/forged callbacks tolerated before giving up (audit L1)
REDIRECT_PATH = "/callback"
LOGIN_TIMEOUT_S = 300.0
EXPIRY_SKEW_S = 60

DEFAULT_SCOPES: tuple[str, ...] = (
    "openid",
    "offline_access",
    "vehicle_device_data",
    "vehicle_cmds",
    "vehicle_charging_cmds",
)
LOCATION_SCOPE = "vehicle_location"  # opt-in only (audit L4): SOCKS does not use location

_FLEET_AUTH_TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"  # noqa: S105
_AUTH_HOST = {"na": "auth.tesla.com", "eu": "auth.tesla.com", "cn": "auth.tesla.cn"}
_TOKEN_URL = {
    "na": _FLEET_AUTH_TOKEN_URL,
    "eu": _FLEET_AUTH_TOKEN_URL,
    "cn": "https://auth.tesla.cn/oauth2/v3/token",
}


class OAuthError(Exception):
    """Login/refresh failed. Messages never contain token or secret values."""


class FleetSettings(Protocol):
    """The subset of ``socks.core.config.Settings`` this module needs."""

    client_id: str
    region: str


@dataclass(frozen=True)
class Endpoints:
    """Where to authorize, where to fetch tokens, and the Fleet API audience."""

    region: str
    authorize_url: str
    token_url: str
    audience: str

    @classmethod
    def for_region(cls, region: str) -> Endpoints:
        from tesla_fleet_api.const import SERVERS, is_valid_region

        region = region.lower()
        if not is_valid_region(region):
            raise OAuthError(f"unknown region {region!r}; use one of {list(SERVERS)}")
        return cls(
            region=region,
            authorize_url=f"https://{_AUTH_HOST[region]}/oauth2/v3/authorize",
            token_url=_TOKEN_URL[region],
            audience=SERVERS[region],
        )


@dataclass
class Tokens:
    """OAuth tokens. Secret fields are excluded from ``repr``."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: int  # unix seconds
    region: str
    scope: str = ""
    token_type: str = "Bearer"  # noqa: S105 - OAuth token type, not a secret

    def expired(self, now: float | None = None, skew: int = EXPIRY_SKEW_S) -> bool:
        return (time.time() if now is None else now) >= self.expires_at - skew

    @classmethod
    def from_response(cls, data: dict[str, Any], region: str) -> Tokens:
        try:
            return cls(
                access_token=str(data["access_token"]),
                refresh_token=str(data.get("refresh_token") or ""),
                expires_at=int(time.time()) + int(data["expires_in"]),
                region=region,
                scope=str(data.get("scope", "")),
                token_type=str(data.get("token_type", "Bearer")),
            )
        except (KeyError, TypeError, ValueError):
            raise OAuthError("token endpoint returned an unexpected response") from None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> Tokens:
        try:
            raw = json.loads(text)
            return cls(
                access_token=str(raw["access_token"]),
                refresh_token=str(raw["refresh_token"]),
                expires_at=int(raw["expires_at"]),
                region=str(raw["region"]),
                scope=str(raw.get("scope", "")),
                token_type=str(raw.get("token_type", "Bearer")),
            )
        except (KeyError, TypeError, ValueError):
            raise OAuthError("token file is corrupt; run `socks-cli fleet login`") from None


@dataclass(frozen=True)
class TokenStatus:
    """What ``status`` reports. Contains no secrets."""

    path: Path
    logged_in: bool
    region: str | None = None
    expires_at: int | None = None
    expired: bool | None = None
    scopes: tuple[str, ...] = ()
    has_refresh_token: bool = False


# --------------------------------------------------------------------------- paths


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "socks"


def token_path() -> Path:
    return config_dir() / TOKEN_FILE_NAME


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def save_tokens(tokens: Tokens, path: Path | None = None) -> Path:
    """Atomically write tokens with mode 0600 into a 0700 directory."""
    path = path or token_path()
    _ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(tokens.to_json())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.chmod(path, 0o600)
    return path


def load_tokens(path: Path | None = None) -> Tokens | None:
    path = path or token_path()
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        return None
    if mode & 0o077:
        log.warning(
            "%s had mode %o; tightened to 0600. Tokens may have been exposed: run "
            "`socks-cli fleet logout && socks-cli fleet login`",
            path,
            mode,
        )
        os.chmod(path, 0o600)
    return Tokens.from_json(path.read_text(encoding="utf-8"))


def delete_tokens(path: Path | None = None) -> bool:
    path = path or token_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


# --------------------------------------------------------------------------- PKCE


def make_pkce() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` per RFC 7636 with S256."""
    verifier = secrets.token_urlsafe(64)  # 86 chars from the unreserved set
    return verifier, pkce_challenge(verifier)


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def make_state() -> str:
    return secrets.token_urlsafe(32)


def build_authorize_url(
    endpoints: Endpoints,
    client_id: str,
    redirect_uri: str,
    state: str,
    challenge: str,
    scopes: Sequence[str] = DEFAULT_SCOPES,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt_missing_scopes": "true",
    }
    return f"{endpoints.authorize_url}?{urlencode(params)}"


# --------------------------------------------------------------------------- loopback


@dataclass
class CallbackResult:
    code: str = field(repr=False)


def _bind(family: socket.AddressFamily, host: str, port: int) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind((host, port))
    except BaseException:
        sock.close()
        raise
    return sock


# errnos meaning "this host has no usable ::1" - then nobody else can bind it either
_NO_IPV6 = {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT}


def _bind_loopback_pair(port: int) -> list[socket.socket]:
    """Bind ``port`` on 127.0.0.1 AND ::1, or raise OAuthError (fail closed).

    If the host has no IPv6 loopback at all, IPv4 alone is safe and is used.
    """
    try:
        v4 = _bind(socket.AF_INET, BIND_HOST, port)
    except OSError as exc:
        raise OAuthError(f"cannot listen on {BIND_HOST}:{port} ({exc.strerror})") from None
    if not socket.has_ipv6:
        return [v4]
    try:
        v6 = _bind(socket.AF_INET6, BIND_HOST_V6, v4.getsockname()[1])
    except OSError as exc:
        if exc.errno in _NO_IPV6:
            log.debug("no IPv6 loopback on this host; listening on IPv4 only")
            return [v4]
        v4.close()
        raise OAuthError(
            f"cannot listen on [{BIND_HOST_V6}]:{port} ({exc.strerror}); another program holds it"
        ) from None
    return [v4, v6]


def _bind_first_free(ports: Sequence[int]) -> list[socket.socket]:
    """Try each registered redirect port in order; the first that binds on both wins."""
    errors: list[str] = []
    for port in ports:
        try:
            return _bind_loopback_pair(port)
        except OAuthError as exc:
            errors.append(str(exc))
    raise OAuthError(
        "no login port free: "
        + "; ".join(errors)
        + ". Close the program using these ports, or register another "
        "http://localhost:<port>/callback in the Tesla portal and pass --port <port>."
    )


class LoopbackServer:
    """One-shot redirect receiver on 127.0.0.1 and ::1 (same registered port)."""

    def __init__(self, state: str, port: int | None = None) -> None:
        self._state = state
        self._port = port
        self._runner: Any = None
        self._future: asyncio.Future[CallbackResult] | None = None
        self.port = 0
        self.bad_callbacks = 0

    @property
    def redirect_uri(self) -> str:
        return f"http://{REDIRECT_HOST}:{self.port}{REDIRECT_PATH}"

    async def start(self) -> None:
        from aiohttp import web

        self._future = asyncio.get_running_loop().create_future()
        app = web.Application()
        app.router.add_get(REDIRECT_PATH, self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        try:
            ports = (self._port,) if self._port else REDIRECT_PORTS
            socks = _bind_first_free(ports)
        except BaseException:
            await self._runner.cleanup()
            self._runner = None
            raise
        self.port = socks[0].getsockname()[1]
        for sock in socks:
            await web.SockSite(self._runner, sock).start()

    async def wait(self, timeout: float = LOGIN_TIMEOUT_S) -> CallbackResult:
        if self._future is None:
            raise RuntimeError("LoopbackServer.start() was not awaited")
        try:
            return await asyncio.wait_for(self._future, timeout)
        except TimeoutError:
            raise OAuthError("timed out waiting for the browser login") from None

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def _fail(self, message: str) -> None:
        if self._future is not None and not self._future.done():
            self._future.set_exception(OAuthError(message))

    async def _handle(self, request: Any) -> Any:
        from aiohttp import web

        q = request.query
        got_state = q.get("state", "")
        if not secrets.compare_digest(got_state.encode(), self._state.encode()):
            # Not ours (stray probe or forgery): answer generically and keep waiting
            # for the real redirect, so a local process cannot abort the login (L1).
            self.bad_callbacks += 1
            if "code" in q:
                log.warning("ignored OAuth callback with a wrong state (possible CSRF)")
            if self.bad_callbacks >= MAX_BAD_CALLBACKS:
                self._fail("too many invalid OAuth callbacks; login aborted")
            return _page(web, 400, "Bad request.")
        if "error" in q:
            # error codes/descriptions are not secret; truncate defensively
            desc = q.get("error_description", "")[:200]
            self._fail(f"authorization failed: {q['error'][:64]} {desc}".strip())
            return _page(web, 400, "Login was not completed. You can close this tab.")
        code = q.get("code", "")
        if not code:
            self._fail("OAuth callback without a code")
            return _page(web, 400, "Login failed: no authorization code.")
        if self._future is not None and not self._future.done():
            self._future.set_result(CallbackResult(code=code))
        return _page(web, 200, "SOCKS is signed in. You can close this tab.")


def _page(web: Any, status: int, text: str) -> Any:
    body = (
        "<!doctype html><meta charset=utf-8><title>SOCKS</title>"
        f"<p style='font:16px sans-serif;margin:3em'>{text}</p>"
    )
    return web.Response(
        status=status,
        text=body,
        content_type="text/html",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


# --------------------------------------------------------------------------- token endpoint


async def post_token(
    session: aiohttp.ClientSession, token_url: str, form: dict[str, str]
) -> dict[str, Any]:
    """POST a form to the token endpoint; raise OAuthError without leaking values."""
    async with session.post(
        token_url,
        data=form,
        headers={"Accept": "application/json"},
    ) as resp:
        try:
            data = await resp.json(content_type=None)
        except Exception:  # noqa: BLE001 - any non-JSON body counts as failure
            data = None
        if resp.status != 200 or not isinstance(data, dict):
            err = desc = ""
            if isinstance(data, dict):
                err = str(data.get("error", ""))[:64]
                desc = str(data.get("error_description", ""))[:200]
            grant = form.get("grant_type", "?")
            msg = f"token request ({grant}) failed: HTTP {resp.status} {err} {desc}"
            raise OAuthError(msg.strip())
        return data


async def exchange_code(
    session: aiohttp.ClientSession,
    endpoints: Endpoints,
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    client_secret: str | None = None,
) -> Tokens:
    form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "audience": endpoints.audience,
    }
    if client_secret:
        form["client_secret"] = client_secret
    data = await post_token(session, endpoints.token_url, form)
    return Tokens.from_response(data, endpoints.region)


async def refresh_tokens(
    session: aiohttp.ClientSession,
    endpoints: Endpoints,
    client_id: str,
    tokens: Tokens,
) -> Tokens:
    if not tokens.refresh_token:
        raise OAuthError("no refresh token stored; run `socks-cli fleet login`")
    form = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": tokens.refresh_token,
    }
    data = await post_token(session, endpoints.token_url, form)
    new = Tokens.from_response(data, endpoints.region)
    if not new.refresh_token:  # keep the old one if the server didn't rotate it
        new.refresh_token = tokens.refresh_token
    if not new.scope:
        new.scope = tokens.scope
    return new


# --------------------------------------------------------------------------- public API


def _client_id(settings: FleetSettings) -> str:
    cid = (settings.client_id or "").strip()
    if not cid:
        raise OAuthError("no client_id configured; see docs/FLEET_API_SETUP.md")
    return cid


async def _with_session[T](
    session: aiohttp.ClientSession | None,
    fn: Callable[[aiohttp.ClientSession], Any],
) -> T:
    if session is not None:
        return await fn(session)
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        return await fn(s)


async def login(
    settings: FleetSettings,
    *,
    client_secret: str | None = None,
    scopes: Sequence[str] = DEFAULT_SCOPES,
    port: int = 0,
    open_browser: Callable[[str], Any] = webbrowser.open,
    endpoints: Endpoints | None = None,
    path: Path | None = None,
    session: aiohttp.ClientSession | None = None,
    timeout: float = LOGIN_TIMEOUT_S,
    on_url: Callable[[str], None] | None = None,
) -> Tokens:
    """Run the browser login and store the resulting tokens.

    ``client_secret`` is optional: Tesla documents it as required for the code
    exchange; with PKCE it may be omitted. It is used once and never stored.
    ``on_url`` receives the authorize URL (e.g. to print it if no browser opens).
    """
    client_id = _client_id(settings)
    endpoints = endpoints or Endpoints.for_region(settings.region)
    verifier, challenge = make_pkce()
    state = make_state()
    server = LoopbackServer(state, port)
    await server.start()
    try:
        url = build_authorize_url(
            endpoints, client_id, server.redirect_uri, state, challenge, scopes
        )
        log.info("waiting for Tesla login on %s", server.redirect_uri)
        if on_url is not None:
            on_url(url)
        open_browser(url)
        result = await server.wait(timeout)
    finally:
        await server.close()

    async def _go(s: aiohttp.ClientSession) -> Tokens:
        return await exchange_code(
            s,
            endpoints,
            client_id,
            result.code,
            verifier,
            server.redirect_uri,
            client_secret,
        )

    tokens: Tokens = await _with_session(session, _go)
    save_tokens(tokens, path)
    log.info("Fleet API login stored (region %s)", tokens.region)
    return tokens


async def refresh(
    settings: FleetSettings,
    *,
    endpoints: Endpoints | None = None,
    path: Path | None = None,
    session: aiohttp.ClientSession | None = None,
) -> Tokens:
    """Refresh and persist (refresh tokens are single-use, so persist first)."""
    client_id = _client_id(settings)
    current = load_tokens(path)
    if current is None:
        raise OAuthError("not logged in; run `socks-cli fleet login`")
    endpoints = endpoints or Endpoints.for_region(current.region or settings.region)

    async def _go(s: aiohttp.ClientSession) -> Tokens:
        return await refresh_tokens(s, endpoints, client_id, current)

    new: Tokens = await _with_session(session, _go)
    save_tokens(new, path)
    log.info("Fleet API token refreshed")
    return new


_refresh_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _refresh_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _refresh_locks.get(loop)
    if lock is None:
        lock = _refresh_locks[loop] = asyncio.Lock()
    return lock


async def access_token(
    settings: FleetSettings,
    *,
    endpoints: Endpoints | None = None,
    path: Path | None = None,
    session: aiohttp.ClientSession | None = None,
) -> str:
    """Return a valid access token, refreshing if needed.

    Suitable as the ``access_token`` callable for ``tesla_fleet_api.TeslaFleetApi``:
    ``TeslaFleetApi(session, access_token=lambda: access_token(settings), region=...)``.
    """
    async with _refresh_lock():  # never spend a single-use refresh token twice
        tokens = load_tokens(path)
        if tokens is None:
            raise OAuthError("not logged in; run `socks-cli fleet login`")
        if tokens.expired():
            tokens = await refresh(settings, endpoints=endpoints, path=path, session=session)
        return tokens.access_token


async def status(settings: FleetSettings | None = None, *, path: Path | None = None) -> TokenStatus:
    """Local token status. No network."""
    path = path or token_path()
    try:
        tokens = load_tokens(path)
    except OAuthError:
        return TokenStatus(path=path, logged_in=False)
    if tokens is None:
        return TokenStatus(path=path, logged_in=False)
    return TokenStatus(
        path=path,
        logged_in=True,
        region=tokens.region,
        expires_at=tokens.expires_at,
        expired=tokens.expired(skew=0),
        scopes=tuple(tokens.scope.split()),
        has_refresh_token=bool(tokens.refresh_token),
    )


async def logout(path: Path | None = None) -> bool:
    return delete_tokens(path)
