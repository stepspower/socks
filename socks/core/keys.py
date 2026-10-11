# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Command-signing key handling (EC P-256) and VIN validation.

The private key is only ever handled as a ``cryptography`` key object or written to a 0600
file. It is never logged, printed or put into an exception message: errors name the *path*
only. Files are written atomically (temp file created 0600, then renamed) in a 0700 dir.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


class KeyFileError(Exception):
    """A key file is missing, unreadable, unsafe or not an EC P-256 private key."""


def tesla_control_dir() -> Path:
    """Where Tesla's ``tesla-control`` keeps its key and VIN (``~/.config/tesla``)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tesla"


# ---------------------------------------------------------------- VIN


def validate_vin(vin: str) -> str:
    """Return the normalised (upper-case, stripped) VIN or raise ValueError.

    17 characters, A-Z/0-9 without I, O and Q (ISO 3779).
    """
    norm = vin.strip().upper()
    if len(norm) != 17:
        raise ValueError(f"VIN must be 17 characters, got {len(norm)}")
    if not VIN_RE.match(norm):
        raise ValueError("VIN may only contain A-Z and 0-9, without I, O or Q")
    return norm


def is_valid_vin(vin: str) -> bool:
    try:
        validate_vin(vin)
    except ValueError:
        return False
    return True


def read_vin_file(path: Path) -> str | None:
    """Read a VIN from a one-line file (as ``tesla-control`` stores it); None if absent/invalid."""
    try:
        text = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    try:
        return validate_vin(text)
    except ValueError:
        return None


# ---------------------------------------------------------------- keys


def load_private_key(path: Path) -> ec.EllipticCurvePrivateKey:
    """Load an unencrypted EC P-256 private key from a PEM file.

    Refuses files readable by group/others (like ssh does), symlink tricks aside.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        raise KeyFileError(f"no private key at {path}") from None
    except OSError as e:
        raise KeyFileError(f"cannot access {path}: {e.strerror}") from None
    if not stat.S_ISREG(st.st_mode):
        raise KeyFileError(f"{path} is not a regular file")
    if st.st_mode & 0o077:
        raise KeyFileError(
            f"{path} is accessible by other users (mode {stat.S_IMODE(st.st_mode):04o}); "
            f"run: chmod 600 {path}"
        )
    try:
        data = path.read_bytes()
    except OSError as e:
        raise KeyFileError(f"cannot read {path}: {e.strerror}") from None
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except (ValueError, TypeError):
        # Deliberately no chained exception: it could echo key material.
        raise KeyFileError(f"{path} is not an unencrypted PEM private key") from None
    finally:
        del data
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise KeyFileError(f"{path} is not an EC P-256 (prime256v1) key")
    return key


def generate_private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def private_key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    """PEM bytes of a private key: only for writing to a 0600 file, never for display."""
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def public_key_pem(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> bytes:
    """SubjectPublicKeyInfo PEM of the public half (safe to show, upload, share)."""
    pub = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    return pub.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def public_key_fingerprint(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> str:
    """Short SHA-256 fingerprint of the uncompressed public point, for display."""
    pub = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    raw = pub.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[i : i + 4] for i in range(0, 32, 4))


def ensure_private_dir(path: Path) -> None:
    """Create ``path`` (and parents) with mode 0700; tighten it if it already exists."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def write_secret_file(path: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``path`` with mode 0600 (dir made 0700)."""
    ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_private_key(
    key: ec.EllipticCurvePrivateKey, path: Path, *, overwrite: bool = False
) -> None:
    if path.exists() and not overwrite:
        raise KeyFileError(f"refusing to overwrite existing key at {path}")
    write_secret_file(path, private_key_pem(key))


def write_public_key(key: ec.EllipticCurvePrivateKey, path: Path) -> None:
    """Write the public key PEM next to the private key (0644 is fine, but keep it 0600-dir)."""
    ensure_private_dir(path.parent)
    path.write_bytes(public_key_pem(key))


def ensure_key(path: Path) -> tuple[ec.EllipticCurvePrivateKey, bool]:
    """Load the key at ``path``; generate and store a new one if none exists.

    Returns ``(key, created)``. A *new* key must still be paired with the car (key card).
    """
    if path.exists():
        return load_private_key(path), False
    key = generate_private_key()
    write_private_key(key, path)
    write_public_key(key, path.with_name("public_key.pem"))
    return key, True


def import_private_key(
    src: Path, dest: Path, *, overwrite: bool = False
) -> ec.EllipticCurvePrivateKey:
    """Copy an existing (already paired) P-256 key into SOCKS' config dir as 0600.

    If ``dest`` already holds the same key this is a no-op; a *different* key at ``dest``
    is only replaced with ``overwrite=True``.
    """
    key = load_private_key(src)
    if src.resolve() == dest.resolve():
        return key
    if dest.exists():
        try:
            existing = load_private_key(dest)
        except KeyFileError:
            existing = None
        if existing is not None and existing.private_numbers() == key.private_numbers():
            return key
        if not overwrite:
            raise KeyFileError(f"a different key already exists at {dest}")
    write_private_key(key, dest, overwrite=True)
    write_public_key(key, dest.with_name("public_key.pem"))
    return key
