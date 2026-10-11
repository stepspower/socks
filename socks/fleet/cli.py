# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""`socks-cli fleet login|register|status|charging|logout` handlers.

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
                scopes=oauth.DEFAULT_SCOPES
                + ((oauth.LOCATION_SCOPE,) if getattr(args, "with_location", False) else ()),
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


def _run_hook(cmd: str, files: list[Path]) -> int:
    """Run the user's own command once with the new files as arguments. No shell."""
    import shlex
    import subprocess

    argv = shlex.split(cmd)
    if not argv:
        return 0
    try:
        done = subprocess.run(  # noqa: S603 - user-configured argv, shell=False
            [*argv, *map(str, files)], shell=False, check=False, timeout=600
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"after-download command failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if done.returncode:
        print(f"after-download command exited with {done.returncode}", file=sys.stderr)
    return done.returncode


def fleet_charging(args: argparse.Namespace) -> int:
    from datetime import date

    from socks.fleet import charging, oauth

    settings = _settings(args)

    def day(name: str) -> date | None:
        value = getattr(args, name, None)
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise SystemExit(f"--{name}: expected YYYY-MM-DD, got {value!r}") from None

    since, until = day("since"), day("until")
    download = getattr(args, "download", None)
    hook = getattr(args, "after_download", None)
    csv_path = getattr(args, "csv", None)
    stats = charging.RequestStats()

    async def go() -> tuple[list[charging.ChargingSession], list[Path]]:
        sessions = await charging.charging_history(settings, since=since, until=until, stats=stats)
        new: list[Path] = []
        if download:
            new = await charging.download_invoices(
                settings,
                sessions,
                Path(download),
                stats=stats,
                overwrite=bool(getattr(args, "overwrite", False)),
            )
        return sessions, new

    try:
        sessions, new = asyncio.run(go())
    except (oauth.OAuthError, charging.ChargingError) as exc:
        print(f"charging history failed: {exc}", file=sys.stderr)
        print(f"Fleet API requests made: {stats.total}", file=sys.stderr)
        return 1
    for x in sessions:
        when = x.start.strftime("%Y-%m-%d %H:%M") if x.start else "?"
        kwh = f"{x.energy_kwh:7.1f} kWh" if x.energy_kwh is not None else "      ? kWh"
        cost = f"{x.cost:8.2f} {x.currency}" if x.cost is not None else "       ?"
        print(f"{when}  {kwh}  {cost}  {x.site}  invoices: {len(x.invoice_ids)}")
    total_kwh = sum(x.energy_kwh or 0 for x in sessions)
    print(f"{len(sessions)} sessions, {total_kwh:.1f} kWh")
    if csv_path:
        print(f"Summary CSV: {charging.write_summary_csv(sessions, Path(csv_path))}")
    if download:
        print(f"{len(new)} new invoice PDF(s) in {Path(download).expanduser()}")
    print(
        f"Fleet API requests made: {stats.total} "
        f"({stats.history_pages} history pages, {stats.invoices} invoices)"
    )
    if hook and new:
        return _run_hook(hook, new)
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
    sp.add_argument(
        "--port",
        type=int,
        default=0,
        help="override the loopback port (default: first free of 53682, 53683, 53684)",
    )
    sp.add_argument(
        "--with-location",
        action="store_true",
        help="also request the vehicle_location scope (off by default)",
    )
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

    sp = sub.add_parser("charging", help="Supercharger history and invoice PDFs")
    common(sp)
    sp.add_argument("--since", metavar="YYYY-MM-DD", help="first day (inclusive)")
    sp.add_argument("--until", metavar="YYYY-MM-DD", help="last day (inclusive)")
    sp.add_argument("--download", metavar="DIR", help="save new invoice PDFs here")
    sp.add_argument("--overwrite", action="store_true", help="re-download existing PDFs")
    sp.add_argument("--csv", metavar="FILE", help="write a summary CSV")
    sp.add_argument(
        "--after-download",
        metavar="CMD",
        help="run CMD once with the new PDF paths appended as arguments (no shell)",
    )
    sp.set_defaults(func=fleet_charging)

    sp = sub.add_parser("logout", help="delete stored tokens")
    sp.set_defaults(func=fleet_logout)
    return p
