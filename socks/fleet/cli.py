# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""`socks-cli fleet login|register|status|logout` handlers.

Worker CORE wires these into ``socks.core.cli``::

    from socks.fleet.cli import add_fleet_parser
    add_fleet_parser(subparsers)        # adds `fleet` with its sub-subcommands
    ...
    return args.func(args)              # each handler returns an exit code

or calls ``fleet_login(args)``, ``fleet_register(args)``, ``fleet_status(args)``
directly with an ``argparse.Namespace``. All attributes are optional.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

SECRET_ENV = "SOCKS_FLEET_CLIENT_SECRET"  # noqa: S105 - env var name


def _settings(args: argparse.Namespace) -> Any:
    settings: Any
    try:
        from socks.core.config import Settings

        settings = Settings.load()
    except ImportError:  # CORE not present (e.g. isolated tests)
        settings = SimpleNamespace(client_id="", region="eu")
    cid = getattr(args, "client_id", None)
    region = getattr(args, "region", None)
    changed = False
    if cid and cid != settings.client_id:
        settings.client_id, changed = cid, True
    if region and region != settings.region:
        settings.region, changed = region, True
    if changed and hasattr(settings, "save"):
        settings.save()  # client_id and region are not secret
    return settings


def _secret(prompt: str, required: bool) -> str | None:
    value = os.environ.get(SECRET_ENV)
    if value:
        return value
    if not sys.stdin.isatty():
        if required:
            raise SystemExit(f"client secret needed: set {SECRET_ENV} or run in a terminal")
        return None
    return getpass.getpass(prompt) or None


def _public_key_path(args: argparse.Namespace) -> Path:
    explicit = getattr(args, "public_key", None)
    if explicit:
        return Path(explicit).expanduser()
    from socks.fleet.oauth import config_dir

    own = config_dir() / "public_key.pem"
    return own if own.exists() else Path.home() / ".config/tesla/public_key.pem"


def fleet_login(args: argparse.Namespace) -> int:
    from socks.fleet import oauth

    settings = _settings(args)
    secret = _secret("Client secret (Enter to try PKCE without it; not stored): ", required=False)
    no_browser = bool(getattr(args, "no_browser", False))

    def show(url: str) -> None:
        print("Open this URL to sign in to Tesla (it contains no secrets):")
        print(url, flush=True)

    try:
        tokens = asyncio.run(
            oauth.login(
                settings,
                client_secret=secret,
                port=int(getattr(args, "port", 0) or 0),
                open_browser=(lambda _u: None) if no_browser else oauth.webbrowser.open,
                on_url=show,
            )
        )
    except oauth.OAuthError as exc:
        print(f"login failed: {exc}", file=sys.stderr)
        return 1
    print(f"Logged in. Scopes: {tokens.scope or '(not reported)'}")
    print(f"Tokens stored in {oauth.token_path()} (mode 0600).")
    return 0


def fleet_register(args: argparse.Namespace) -> int:
    from socks.fleet import oauth, partner

    settings = _settings(args)
    domain = getattr(args, "domain", None) or partner.DEFAULT_DOMAIN
    pem = _public_key_path(args)
    check = asyncio.run(partner.check_public_key(domain, pem))
    print(f"Public key at {check.url}: {check.reason}")
    if not check.ok and not getattr(args, "force", False):
        print("Fix hosting first (or pass --force).", file=sys.stderr)
        return 1
    secret = _secret("Client secret (used once, not stored): ", required=True)
    if not secret:
        print("no client secret given", file=sys.stderr)
        return 1
    try:
        result = asyncio.run(partner.register(settings, secret, domain))
    except oauth.OAuthError as exc:
        print(f"registration failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # tesla_fleet_api raises typed errors per HTTP status
        print(f"registration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Registered {domain} in region {settings.region}: {result.get('response', result)}")
    vin = getattr(settings, "vin", "") or None
    print(f"Now open on your phone: {partner.pairing_link(domain, vin)}")
    return 0


def fleet_status(args: argparse.Namespace) -> int:
    from socks.fleet import oauth, partner

    settings = _settings(args)
    st = asyncio.run(oauth.status(settings))
    print(f"client_id: {'set' if settings.client_id else 'NOT SET'}; region: {settings.region}")
    if not st.logged_in:
        print(f"Not logged in ({st.path}).")
    else:
        exp = datetime.fromtimestamp(st.expires_at or 0, UTC).isoformat(timespec="seconds")
        state = "expired (will refresh)" if st.expired else "valid"
        print(f"Logged in, region {st.region}; access token {state} until {exp}")
        print(f"Refresh token: {'yes' if st.has_refresh_token else 'no'}")
        print(f"Scopes: {' '.join(st.scopes) or '(not reported)'}")
    if getattr(args, "check_key", False):
        domain = getattr(args, "domain", None) or partner.DEFAULT_DOMAIN
        check = asyncio.run(partner.check_public_key(domain, _public_key_path(args)))
        print(f"Public key {check.url}: {check.reason}")
        return 0 if (st.logged_in and check.ok) else 1
    return 0 if st.logged_in else 1


def fleet_logout(args: argparse.Namespace) -> int:
    from socks.fleet import oauth

    removed = asyncio.run(oauth.logout())
    print("Tokens removed." if removed else "No tokens stored.")
    return 0


def add_fleet_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Attach ``fleet`` to an existing ``add_subparsers()`` object."""
    p = subparsers.add_parser("fleet", help="Tesla Fleet API (cloud) setup")
    sub = p.add_subparsers(dest="fleet_cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--client-id", help="developer.tesla.com client ID (saved)")
        sp.add_argument("--region", choices=["eu", "na", "cn"], help="saved")

    sp = sub.add_parser("login", help="sign in with Tesla in the browser")
    common(sp)
    sp.add_argument("--port", type=int, default=0, help="fixed loopback port (default random)")
    sp.add_argument("--no-browser", action="store_true", help="only print the URL")
    sp.set_defaults(func=fleet_login)

    sp = sub.add_parser("register", help="one-time partner_accounts registration")
    common(sp)
    sp.add_argument("--domain", default=None, help="default auth.stepspower.net")
    sp.add_argument("--public-key", default=None, help="public key PEM to compare")
    sp.add_argument("--force", action="store_true", help="register even if key check fails")
    sp.set_defaults(func=fleet_register)

    sp = sub.add_parser("status", help="show login state (no network)")
    common(sp)
    sp.add_argument("--check-key", action="store_true", help="also fetch the hosted key")
    sp.add_argument("--domain", default=None)
    sp.add_argument("--public-key", default=None)
    sp.set_defaults(func=fleet_status)

    sp = sub.add_parser("logout", help="delete stored tokens")
    sp.set_defaults(func=fleet_logout)
    return p
