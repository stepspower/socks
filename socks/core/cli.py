# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""``socks-cli``: status, JSON dump, commands and key management from a terminal.

    socks-cli [--fake] [--source auto|ble|cloud] status
    socks-cli json
    socks-cli cmd                      # list commands
    socks-cli cmd set_charge_limit 80
    socks-cli cmd unlock --yes         # dangerous commands need --yes
    socks-cli key import [PATH]        # default: ~/.config/tesla/private_key.pem (+ vin)
    socks-cli key generate | key public
    socks-cli fleet login|register|status|logout   # from socks.fleet.cli

Exit codes: 0 ok, 1 failure, 2 usage error, 3 refused (dangerous command without --yes).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from socks import __version__
from socks.core.commands import COMMANDS, parse_command
from socks.core.model import KM_PER_MILE, VehicleState, state_to_dict

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_REFUSED = 0, 1, 2, 3


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="socks-cli", description="SOCKS command-line tool")
    p.add_argument("--version", action="version", version=f"socks {__version__}")
    p.add_argument("--fake", action="store_true", help="use the built-in fake car")
    p.add_argument(
        "--source",
        choices=("auto", "ble", "cloud"),
        default="auto",
        help="force a backend (default: router, preferred source from settings)",
    )
    p.add_argument("--config", type=Path, help="settings file (default: XDG config dir)")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("status", help="show the car's state as a table")
    j = sub.add_parser("json", help="print the full VehicleState as JSON")
    j.add_argument("--no-raw", action="store_true", help="omit the raw payload")
    j.add_argument(
        "--with-location",
        action="store_true",
        help="keep latitude/longitude etc. (redacted by default)",
    )
    c = sub.add_parser("cmd", help="send a command (no name: list commands)")
    c.add_argument("name", nargs="?")
    c.add_argument("arg", nargs="?", type=float)
    c.add_argument("--yes", action="store_true", help="confirm a dangerous command")
    k = sub.add_parser("key", help="manage the command-signing key")
    ksub = k.add_subparsers(dest="key_action", required=True)
    ki = ksub.add_parser("import", help="import an existing, already paired P-256 key")
    ki.add_argument("path", nargs="?", type=Path)
    ki.add_argument("--force", action="store_true", help="replace a different existing key")
    ksub.add_parser("generate", help="create a key if none exists (pair it with the car)")
    ksub.add_parser("public", help="print the public key PEM and fingerprint")
    try:
        from socks.fleet.cli import add_fleet_parser
    except ImportError:  # build without fleet support
        f = sub.add_parser("fleet", help="Tesla Fleet API (cloud) setup (not available)")
        f.set_defaults(func=_no_fleet)
    else:
        add_fleet_parser(sub)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action == "fleet":
        return int(args.func(args) or 0)
    if args.action == "key":
        return _key(args)
    if args.action == "cmd" and not args.name:
        return _list_commands()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return EXIT_FAIL


# ---------------------------------------------------------------- backend actions


async def _run(args: argparse.Namespace) -> int:
    from socks.core.backend import CommandError

    settings = _load_settings(args.config)
    if args.action == "cmd":
        try:
            cmd = parse_command(args.name)
        except ValueError as e:
            print(f"socks-cli: {e} (run 'socks-cli cmd' for the list)", file=sys.stderr)
            return EXIT_USAGE
        info = COMMANDS[cmd]
        if info.dangerous and not args.yes:
            print(
                f"socks-cli: '{cmd.value}' ({info.label}) is a dangerous command; "
                "add --yes to really do it",
                file=sys.stderr,
            )
            return EXIT_REFUSED
        arg: float | int | None = args.arg
        if arg is not None and info.arg == "percent" and math.isfinite(arg) and arg == int(arg):
            arg = int(arg)
    backend = _make_backend(args, settings)
    try:
        if args.action == "cmd":
            await backend.send(cmd, arg)
            print(f"ok: {info.label} via {_source_label(backend)}")
            return EXIT_OK
        state = await backend.read_state(wake=True)  # explicit user request: may wake (BLE)
        if args.action == "json":
            data = state_to_dict(state, include_raw=not args.no_raw)
            if not args.with_location:
                from socks.fleet.redact import redact_location

                data = redact_location(data)
            print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
        else:
            print(format_status(state, imperial=settings.units == "imperial"))
        return EXIT_OK
    except CommandError as e:
        print(f"socks-cli: {e}", file=sys.stderr)
        return EXIT_FAIL
    finally:
        closer = getattr(backend, "close", None)
        if closer is not None:
            await closer()


def _load_settings(path: Path | None) -> Any:
    from socks.core.config import Settings

    return Settings.load(path)


def _make_backend(args: argparse.Namespace, settings: Any) -> Any:
    from socks.core import backend as be

    if args.fake:
        return be.fake_backend(start=None)
    token = _token_provider(settings)
    router = be.build_router(settings, token)
    if args.source == "ble":
        return be.Router(router.ble, None)
    if args.source == "cloud":
        if router.cloud is None:
            return be.Router(None, None)  # -> NotAvailable with a clear reason
        return be.Router(None, router.cloud, be.Source.CLOUD)
    return router


def _token_provider(settings: Any) -> Any:
    """Access-token provider from socks.fleet, if that module offers one."""
    if not settings.cloud_enabled:
        return None
    try:
        from socks.fleet import oauth
    except ImportError:
        return None

    async def provider() -> str | None:
        return await oauth.access_token(settings)

    return provider


def _source_label(backend: Any) -> str:
    src = getattr(backend, "last_source", None) or getattr(backend, "name", None)
    return {"ble": "Bluetooth", "cloud": "cloud"}.get(getattr(src, "value", ""), "fake")


def _list_commands() -> int:
    print("Commands (dangerous ones need --yes):")
    for info in COMMANDS.values():
        flags = []
        if info.arg:
            flags.append(f"arg: {info.arg}")
        if info.dangerous:
            flags.append("DANGEROUS")
        extra = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {info.command.value:<18} {info.label}{extra}")
    return EXIT_OK


# ---------------------------------------------------------------- formatting


def format_status(state: VehicleState, *, imperial: bool = False) -> str:
    """Human-readable two-column table."""

    def dist(km: float | None) -> str:
        if km is None:
            return "-"
        return f"{km / KM_PER_MILE:,.1f} mi" if imperial else f"{km:,.1f} km"

    def speed(kmh: float | None) -> str:
        if kmh is None:
            return "-"
        return f"{kmh / KM_PER_MILE:.0f} mph" if imperial else f"{kmh:.0f} km/h"

    def temp(c: float | None) -> str:
        if c is None:
            return "-"
        return f"{c * 9 / 5 + 32:.1f} °F" if imperial else f"{c:.1f} °C"

    def opt(value: Any, unit: str = "") -> str:
        return "-" if value is None else f"{value}{unit}"

    def yes(value: bool | None) -> str:
        return "-" if value is None else ("yes" if value else "no")

    def when(dt: datetime | None) -> str:
        return "-" if dt is None else dt.astimezone().strftime("%Y-%m-%d %H:%M")

    charging = state.charging.value
    if state.charger_power_kw:
        charging += f" · {state.charger_power_kw:g} kW"
        if state.charger_voltage and state.charger_current_a:
            charging += f" ({state.charger_voltage} V × {state.charger_current_a} A)"
    rows = [
        ("Car", " · ".join(x for x in (state.car_name, state.vin, state.firmware) if x)),
        ("Read", f"{when(state.ts)} via {state.source.value}"),
        ("Battery", f"{opt(state.soc, ' %')} (limit {opt(state.charge_limit, ' %')})"),
        ("Range", f"{dist(state.rated_range_km)} rated"),
        ("Charging", charging),
        ("Plugged in", f"{yes(state.plugged_in)} (port open: {yes(state.charge_port_open)})"),
    ]
    if state.charge_energy_added_kwh:
        rows.append(("Energy added", f"{state.charge_energy_added_kwh:g} kWh"))
    if state.minutes_to_limit:
        rows.append(("To limit", f"{state.minutes_to_limit} min"))
    if state.scheduled_charging_start:
        rows.append(("Scheduled", when(state.scheduled_charging_start)))
    rows += [
        ("Odometer", dist(state.odometer_km)),
        ("Gear", f"{state.shift.value}  {speed(state.speed_kmh)}"),
        ("Inside / out", f"{temp(state.inside_temp_c)} / {temp(state.outside_temp_c)}"),
        ("Climate", f"{yes(state.climate_on)} (set {temp(state.target_temp_c)})"),
        ("Locked", yes(state.locked)),
        ("Sentry", yes(state.sentry_on)),
    ]
    if state.doors_open:
        rows.append(("Open doors", ", ".join(state.doors_open)))
    if state.windows_open:
        rows.append(("Open windows", ", ".join(state.windows_open)))
    width = max(len(k) for k, _ in rows)
    return "\n".join(f"{k:<{width}}  {v}" for k, v in rows)


# ---------------------------------------------------------------- keys


def _key(args: argparse.Namespace) -> int:
    from socks.core import keys
    from socks.core.config import Settings

    path = args.config
    settings = Settings.load(path)
    try:
        if args.key_action == "import":
            src = args.path or keys.tesla_control_dir() / "private_key.pem"
            dest = Settings.paths().key_file
            key = keys.import_private_key(src, dest, overwrite=args.force)
            settings.key_path = dest
            msg = f"imported key from {src} -> {dest}"
            if not settings.vin:
                vin = keys.read_vin_file(src.parent / "vin")
                if vin:
                    settings.vin = vin
                    msg += f"; VIN {vin}"
            settings.save(path)
            print(f"{msg}\nfingerprint {keys.public_key_fingerprint(key)}")
        elif args.key_action == "generate":
            key, created = keys.ensure_key(settings.key_path)
            state = "created" if created else "already exists"
            print(
                f"key {state}: {settings.key_path}\nfingerprint {keys.public_key_fingerprint(key)}"
            )
            if created:
                print("Pair it with the car (key card) before it can send commands.")
        else:
            key = keys.load_private_key(settings.key_path)
            sys.stdout.write(keys.public_key_pem(key).decode("ascii"))
            print(f"fingerprint {keys.public_key_fingerprint(key)}")
    except keys.KeyFileError as e:
        print(f"socks-cli: {e}", file=sys.stderr)
        return EXIT_FAIL
    return EXIT_OK


# ---------------------------------------------------------------- fleet


def _no_fleet(args: argparse.Namespace) -> int:
    print("socks-cli: fleet support is not installed in this build", file=sys.stderr)
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
