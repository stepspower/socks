# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""One-time Fleet API partner registration and public-key hosting check.

Registration (``register``) needs a *partner* token, obtained with the
client-credentials grant from the app's client_id + client_secret. The secret is
used for that single request and never written to disk.

The actual API calls go through ``tesla_fleet_api``'s ``Partner`` helper
(``TeslaFleetApi(...).partner.register / .public_key``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from socks.fleet.oauth import (
    Endpoints,
    FleetSettings,
    OAuthError,
    _client_id,
    _with_session,
    post_token,
)

if TYPE_CHECKING:
    import aiohttp

log = logging.getLogger(__name__)

DEFAULT_DOMAIN = "auth.stepspower.net"
WELL_KNOWN_PATH = "/.well-known/appspecific/com.tesla.3p.public-key.pem"
PARTNER_SCOPES = ("openid", "vehicle_device_data", "vehicle_cmds", "vehicle_charging_cmds")
ACCEPTED_CONTENT_TYPES = ("text/plain", "application/x-pem-file")
_MAX_KEY_BYTES = 16 * 1024


def public_key_url(domain: str) -> str:
    return f"https://{domain}{WELL_KNOWN_PATH}"


def pairing_link(domain: str, vin: str | None = None) -> str:
    """Link the owner opens on the phone to add the virtual key to the car."""
    from urllib.parse import quote, urlencode

    link = f"https://tesla.com/_ak/{quote(domain, safe='.-')}"
    return f"{link}?{urlencode({'vin': vin})}" if vin else link


# --------------------------------------------------------------------------- registration


async def partner_token(
    session: aiohttp.ClientSession,
    endpoints: Endpoints,
    client_id: str,
    client_secret: str,
) -> str:
    """Client-credentials grant. Returns the access token (caller must not log it)."""
    data = await post_token(
        session,
        endpoints.token_url,
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "audience": endpoints.audience,
            "scope": " ".join(PARTNER_SCOPES),
        },
    )
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise OAuthError("token endpoint returned no partner token")
    return token


def _fleet_api(session: aiohttp.ClientSession, token: str, endpoints: Endpoints) -> Any:
    from tesla_fleet_api import TeslaFleetApi

    return TeslaFleetApi(
        session,
        access_token=token,
        server=endpoints.audience,
        charging_scope=False,
        energy_scope=False,
        user_scope=False,
        vehicle_scope=False,
    )


async def register(
    settings: FleetSettings,
    client_secret: str,
    domain: str = DEFAULT_DOMAIN,
    *,
    endpoints: Endpoints | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """POST /api/1/partner_accounts for ``domain`` in the configured region.

    Must be called once per region the app is used in. Returns Tesla's JSON
    response (contains no secrets).
    """
    client_id = _client_id(settings)
    if not client_secret:
        raise OAuthError("client secret is required for partner registration")
    endpoints = endpoints or Endpoints.for_region(settings.region)

    async def _go(s: aiohttp.ClientSession) -> dict[str, Any]:
        token = await partner_token(s, endpoints, client_id, client_secret)
        api = _fleet_api(s, token, endpoints)
        result: dict[str, Any] = await api.partner.register(domain)
        return result

    result: dict[str, Any] = await _with_session(session, _go)
    log.info("partner account registered for %s in region %s", domain, endpoints.region)
    return result


async def registered_public_key(
    settings: FleetSettings,
    client_secret: str,
    domain: str = DEFAULT_DOMAIN,
    *,
    endpoints: Endpoints | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """GET /api/1/partner_accounts/public_key — confirms what Tesla has on file."""
    client_id = _client_id(settings)
    endpoints = endpoints or Endpoints.for_region(settings.region)

    async def _go(s: aiohttp.ClientSession) -> dict[str, Any]:
        token = await partner_token(s, endpoints, client_id, client_secret)
        result: dict[str, Any] = await _fleet_api(s, token, endpoints).partner.public_key(domain)
        return result

    return await _with_session(session, _go)


# --------------------------------------------------------------------------- hosting check


@dataclass(frozen=True)
class KeyCheck:
    url: str
    ok: bool
    reason: str
    http_status: int | None = None
    content_type: str | None = None
    byte_identical: bool = False


def _der(pem: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise ValueError("not an EC prime256v1 public key")
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


async def check_public_key(
    domain: str = DEFAULT_DOMAIN,
    local_pem: Path | bytes | None = None,
    *,
    url: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> KeyCheck:
    """Fetch the well-known URL and compare with the local public key PEM.

    ``local_pem`` is a path to (or the bytes of) the PUBLIC key; never pass a
    private key. ``ok`` means the served key is the same P-256 key and the
    content type is acceptable. TLS is verified (Tesla requires valid HTTPS).
    """
    import aiohttp

    url = url or public_key_url(domain)
    if local_pem is None:
        raise ValueError("local public key PEM is required")
    local = local_pem if isinstance(local_pem, bytes) else Path(local_pem).read_bytes()
    try:
        local_der = _der(local)
    except ValueError as exc:
        return KeyCheck(url=url, ok=False, reason=f"local key: {exc}")

    async def _go(s: aiohttp.ClientSession) -> KeyCheck:
        try:
            async with s.get(url, allow_redirects=False) as resp:
                body = await resp.content.read(_MAX_KEY_BYTES + 1)
                ctype = resp.headers.get("Content-Type", "")
                status_code = resp.status
        except aiohttp.ClientConnectorCertificateError:
            return KeyCheck(url=url, ok=False, reason="TLS certificate not valid for domain")
        except aiohttp.ClientError as exc:
            return KeyCheck(url=url, ok=False, reason=f"fetch failed: {type(exc).__name__}")
        if status_code != 200:
            return KeyCheck(url, False, f"HTTP {status_code}", status_code, ctype)
        if len(body) > _MAX_KEY_BYTES:
            return KeyCheck(url, False, "response too large", status_code, ctype)
        identical = body == local
        try:
            same_key = _der(body) == local_der
        except ValueError as exc:
            return KeyCheck(url, False, f"served key: {exc}", status_code, ctype)
        if not same_key:
            return KeyCheck(url, False, "served key differs from local key", status_code, ctype)
        mime = ctype.split(";")[0].strip().lower()
        if mime not in ACCEPTED_CONTENT_TYPES:
            return KeyCheck(
                url, False, f"unexpected Content-Type {mime!r}", status_code, ctype, identical
            )
        return KeyCheck(url, True, "ok", status_code, ctype, identical)

    return await _with_session(session, _go)
