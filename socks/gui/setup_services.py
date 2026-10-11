# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""What the setup wizard does, separated from its widgets (and testable).

``RealServices`` calls core.keys / core.backend / socks.fleet; ``FakeServices``
(``--fake``) simulates the car and Tesla's endpoints and writes keys only into a
throwaway directory. Nothing here runs until the user clicks the matching button.
The private key is only ever *loaded* by core.keys (to fingerprint or copy it);
its contents are never returned, displayed or logged.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from socks.core.keys import tesla_control_dir, validate_vin

DEFAULT_IMPORT = tesla_control_dir() / "private_key.pem"
PAIR_ROLES = ("driver", "owner")
ROLE_HELP = {
    "driver": "Driver: can unlock, drive and use the car, but cannot add or remove keys.",
    "owner": "Owner: full access, including managing other keys. Pick only if you need it.",
}
PRICE_NOTE = (
    "Fleet API prices: data read $0.002, command $0.001, wake $0.02; Tesla credits $10 a "
    "month, which usually covers personal use. SOCKS uses Bluetooth first and polling "
    "stays off by default."
)


# ------------------------------------------------------------------ validation


def vin_feedback(text: str) -> tuple[bool, str]:
    """(valid, message) for the live VIN field."""
    norm = text.strip().upper()
    if not norm:
        return False, "Enter the 17-character VIN."
    try:
        validate_vin(norm)
    except ValueError as exc:
        return False, str(exc)
    return True, "VIN looks valid."


def import_path_feedback(text: str) -> tuple[bool, str]:
    """(usable, message) for the import path; looks at the file system entry only."""
    raw = text.strip()
    if not raw:
        return False, "Choose the private key file (PEM)."
    p = Path(raw).expanduser()
    if not p.is_file():
        return False, "No file at this path."
    try:
        if p.stat().st_mode & 0o077:
            return False, "The key file is readable by other users; run chmod 600 on it first."
    except OSError as exc:
        return False, f"Cannot read file information: {exc.strerror}"
    return True, "File found."


@dataclass(frozen=True)
class KeyResult:
    path: Path
    fingerprint: str
    created: bool


# -------------------------------------------------------------------- services


class RealServices:
    fake = False

    def __init__(self, key_dest: Path, domain: str | None = None) -> None:
        from socks.fleet.partner import DEFAULT_DOMAIN

        self.key_dest = key_dest
        self.domain = domain or DEFAULT_DOMAIN

    # keys (blocking; run on the I/O thread) ----------------------------------

    def import_key(self, src: Path) -> KeyResult:
        from socks.core import keys

        key = keys.import_private_key(src.expanduser(), self.key_dest)
        return KeyResult(self.key_dest, keys.public_key_fingerprint(key), False)

    def generate_key(self) -> KeyResult:
        from socks.core import keys

        if self.key_dest.exists():
            raise FileExistsError(
                f"a key already exists at {self.key_dest}; import it instead of replacing it"
            )
        key, created = keys.ensure_key(self.key_dest)
        return KeyResult(self.key_dest, keys.public_key_fingerprint(key), created)

    def public_key_path(self) -> Path:
        return self.key_dest.with_name("public_key.pem")

    # car -----------------------------------------------------------------------

    def _ble(self, vin: str, key_path: Path) -> Any:
        from socks.core.backend import BleBackend

        return BleBackend(vin, key_path)

    async def pair(self, vin: str, key_path: Path, role: str) -> bool:
        ble = self._ble(vin, key_path)
        try:
            return bool(await ble.pair(role=role))
        finally:
            await ble.close()

    async def test_connection(self, vin: str, key_path: Path) -> Any:
        ble = self._ble(vin, key_path)
        try:
            return await ble.read_state(wake=True)
        finally:
            await ble.close()

    # cloud ---------------------------------------------------------------------

    async def check_key(self) -> Any:
        from socks.fleet import partner

        return await partner.check_public_key(self.domain, self.public_key_path())

    async def register(self, settings: Any, secret: str) -> Any:
        from socks.fleet import partner

        return await partner.register(settings, secret, self.domain)

    async def login(self, settings: Any, secret: str | None) -> None:
        from socks.fleet import oauth

        await oauth.login(settings, client_secret=secret or None)

    async def token_status(self, settings: Any) -> Any:
        from socks.fleet import oauth

        return await oauth.status()

    def pairing_link(self) -> str:
        from socks.fleet.partner import pairing_link

        return pairing_link(self.domain)


class FakeServices(RealServices):
    """Same surface, simulated. Keys go to the throwaway config dir of ``--fake``."""

    fake = True

    def __init__(self, key_dest: Path, backend_factory: Any) -> None:
        super().__init__(key_dest)
        self._backend_factory = backend_factory
        self._logged_in = False

    async def pair(self, vin: str, key_path: Path, role: str) -> bool:
        if role not in PAIR_ROLES:
            raise ValueError(f"role must be one of {', '.join(PAIR_ROLES)}")
        await asyncio.sleep(1.5)
        return True

    async def test_connection(self, vin: str, key_path: Path) -> Any:
        b = self._backend_factory()
        return await b.read_state(wake=True)

    async def check_key(self) -> Any:
        await asyncio.sleep(0.5)
        return SimpleNamespace(
            ok=True, url=f"https://{self.domain}/.well-known/…", reason="fake: key matches"
        )

    async def register(self, settings: Any, secret: str) -> Any:
        await asyncio.sleep(0.8)
        return {"response": {"domain": self.domain, "fake": True}}

    async def login(self, settings: Any, secret: str | None) -> None:
        await asyncio.sleep(1.0)
        self._logged_in = True

    async def token_status(self, settings: Any) -> Any:
        return SimpleNamespace(
            logged_in=self._logged_in,
            region=settings.region,
            expires_at=None,
            expired=False,
            scopes=("openid", "vehicle_device_data") if self._logged_in else (),
        )
