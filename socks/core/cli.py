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
from socks.core.model import VehicleState, state_to_dict
from socks.history.units import KM_PER_MI, c_to_f

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
    st = sub.add_parser("status", help="show the car's state as a table")
    st.add_argument("--with-location", action="store_true", help="also show the GPS position")
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
    d = sub.add_parser("depart", help="one-off 'depart by': charge + cabin ready at HH:MM")
    d.add_argument("time", nargs="?", help="local departure time HH:MM (next occurrence)")
    d.add_argument("--limit", type=int, default=90, help="charge limit %% (default 90)")
    d.add_argument("--temp", type=float, default=21.0, help="cabin °C (default 21)")
    d.add_argument("--cancel", action="store_true", help="switch the departure off")
    k = sub.add_parser("key", help="manage the command-signing key")
    ksub = k.add_subparsers(dest="key_action", required=True)
    ki = ksub.add_parser("import", help="import an existing, already paired P-256 key")
    ki.add_argument("path", nargs="?", type=Path)
    ki.add_argument("--force", action="store_true", help="replace a different existing key")
    ki.add_argument("--vin", help="the car's VIN (default: the vin file next to the key)")
    ksub.add_parser("generate", help="create a key if none exists (pair it with the car)")
    ksub.add_parser("public", help="print the public key PEM and fingerprint")
    kp = ksub.add_parser("pair", help="ask the car (over Bluetooth) to accept this key")
    kp.add_argument("--role", choices=("driver", "owner"), default="driver")
    kp.add_argument("--vin", help="the car's VIN (default: from settings)")
    from socks.fleet.cli import add_fleet_parser

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
    if args.action == "depart":
        return asyncio.run(_depart(args))
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
        if arg is not None and info.arg != "temp_c" and math.isfinite(arg) and arg == int(arg):
            arg = int(arg)
    backend = _make_backend(args, settings)
    try:
        if args.action == "cmd":
            await backend.send(cmd, arg)
            print(f"ok: {info.label} via {_source_label(backend)}")
            return EXIT_OK
        # Explicit user request: may wake (BLE only). Location only fetched when asked for.
        want_location = bool(args.with_location and settings.location_enabled)
        state = await backend.read_state(wake=True, include_location=want_location)
        if args.action == "json":
            data = state_to_dict(state, include_raw=not args.no_raw)
            if not args.with_location:
                from socks.fleet.redact import redact_location

                data = redact_location(data)
            print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
        else:
            print(
                format_status(
                    state,
                    imperial=settings.units == "imperial",
                    with_location=args.with_location,
                )
            )
        return EXIT_OK
    except CommandError as e:
        print(f"socks-cli: {e}", file=sys.stderr)
        return EXIT_FAIL
    finally:
        await backend.close()


def _load_settings(path: Path | None) -> Any:
    from socks.core.config import Settings

    return Settings.load(path)


def _make_backend(args: argparse.Namespace, settings: Any) -> Any:
    from socks.core import backend as be

    if args.fake:
        return be.fake_backend(start=None)
    from socks.fleet.oauth import token_provider

    router = be.build_router(settings, token_provider(settings))
    if args.source == "ble":
        return be.Router(router.ble, None)
    if args.source == "cloud":
        if router.cloud is None:
            return be.Router(None, None)  # -> NotAvailable with a clear reason
        return be.Router(None, router.cloud, be.Source.CLOUD)
    return router


def _source_label(backend: Any) -> str:
    src = getattr(backend, "last_source", None) or getattr(backend, "name", None)
    return {"ble": "Bluetooth", "cloud": "cloud"}.get(getattr(src, "value", ""), "fake")


async def _depart(args: argparse.Namespace) -> int:
    """Arm, cancel or show the one-off departure plan (socks.core.depart)."""
    from datetime import UTC, datetime

    from socks.core import depart

    settings = _load_settings(args.config)
    if not args.cancel and not args.time:
        plan = settings.depart_plan
        if plan is None:
            print("No departure planned. Usage: socks-cli depart HH:MM [--limit N] [--temp T]")
        else:
            print(
                f"Depart {plan.depart_at.astimezone():%a %d %b %H:%M}: "
                f"{plan.charge_limit} %, {plan.temp_c:g} °C"
            )
        return EXIT_OK
    now = datetime.now(UTC)
    plan = None
    if not args.cancel:
        try:
            plan = depart.make_plan(
                depart.next_occurrence(args.time, now), args.limit, args.temp, now
            )
        except ValueError as e:
            print(f"socks-cli: {e}", file=sys.stderr)
            return EXIT_USAGE
    backend = _make_backend(args, settings)
    try:
        if plan is None:
            lines = [await depart.disarm(backend)]
        else:
            lines = await depart.arm(backend, plan, now)
    finally:
        closer = getattr(backend, "close", None)
        if closer is not None:
            await closer()
    print("\n".join(lines))
    if any(line.startswith(depart.FAILED) for line in lines):
        return EXIT_FAIL
    settings.depart_plan = plan
    settings.save(args.config)
    return EXIT_OK


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


def format_status(
    state: VehicleState, *, imperial: bool = False, with_location: bool = False
) -> str:
    """Human-readable two-column table."""

    def dist(km: float | None) -> str:
        if km is None:
            return "-"
        return f"{km / KM_PER_MI:,.1f} mi" if imperial else f"{km:,.1f} km"

    def speed(kmh: float | None) -> str:
        if kmh is None:
            return "-"
        return f"{kmh / KM_PER_MI:.0f} mph" if imperial else f"{kmh:.0f} km/h"

    def temp(c: float | None) -> str:
        if c is None:
            return "-"
        return f"{c_to_f(c):.1f} °F" if imperial else f"{c:.1f} °C"

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
    if with_location:
        if state.latitude is not None and state.longitude is not None:
            loc = f"{state.latitude:.5f}, {state.longitude:.5f}"
            if state.heading is not None:
                loc += f" heading {state.heading}°"
            if state.gps_as_of is not None:
                loc += f" (fix {when(state.gps_as_of)})"
        else:
            loc = "-"
        rows.append(("Location", loc))
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
            vin = None
            if args.vin:
                vin = keys.validate_vin(args.vin)
            elif not settings.vin:
                for vin_file in (src.parent / "vin", keys.tesla_control_dir() / "vin"):
                    vin = keys.read_vin_file(vin_file)
                    if vin:
                        break
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
        elif args.key_action == "pair":
            return asyncio.run(_key_pair(args, settings))
        else:
            key = keys.load_private_key(settings.key_path)
            sys.stdout.write(keys.public_key_pem(key).decode("ascii"))
            print(f"fingerprint {keys.public_key_fingerprint(key)}")
    except (keys.KeyFileError, ValueError) as e:
        print(f"socks-cli: {e}", file=sys.stderr)
        return EXIT_FAIL
    return EXIT_OK


async def _key_pair(args: argparse.Namespace, settings: Any) -> int:
    """Send the BLE add-key request and wait for the key-card confirmation."""
    from socks.core import backend as be
    from socks.core import keys

    vin = keys.validate_vin(args.vin or settings.vin) if (args.vin or settings.vin) else ""
    if not vin:
        print(
            "socks-cli: no VIN; pass --vin or run 'socks-cli key import --vin ...'", file=sys.stderr
        )
        return EXIT_FAIL
    if args.fake:
        ble: Any = be.fake_backend(start=None, vin=vin)
    else:
        if not Path(settings.key_path).is_file():
            print(
                "socks-cli: no key yet; run 'socks-cli key import' or 'socks-cli key generate'",
                file=sys.stderr,
            )
            return EXIT_FAIL
        ble = be.BleBackend(vin, Path(settings.key_path))
    timeout = be.PAIR_TIMEOUT
    print(
        f"Sending add-key request (role {args.role}, cloud_key) to {vin} over Bluetooth...\n"
        f"Tap your key card on the centre console to confirm (waiting up to {timeout:g} s)."
    )
    try:
        confirmed = await ble.pair(args.role, timeout)
    except be.CommandError as e:
        print(f"socks-cli: pairing failed: {e}", file=sys.stderr)
        return EXIT_FAIL
    finally:
        await ble.close()
    if confirmed:
        print("Key accepted by the car.")
        return EXIT_OK
    print(
        "No confirmation received. Check the car's Locks screen: if the key is listed, "
        "pairing worked; otherwise run 'socks-cli key pair' again."
    )
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
