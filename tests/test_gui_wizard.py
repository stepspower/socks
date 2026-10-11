# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Setup-wizard logic and the in-house QR encoder (offscreen; no car, no network)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from socks.gui.setup_services import (  # noqa: E402
    FakeServices,
    RealServices,
    import_path_feedback,
    vin_feedback,
)
from socks.gui.widgets.qr import (  # noqa: E402
    _BLOCKS,
    QrError,
    _mask_bit,
    data_capacity,
    encode,
    rs_remainder,
)

# Reference: the `qrcode` library, b"SOCKS", version 1, EC level M, mask 2, byte mode.
SOCKS_V1_M_MASK2 = (
    "#######..#....#######",
    "#.....#..##...#.....#",
    "#.###.#.###.#.#.###.#",
    "#.###.#.#.....#.###.#",
    "#.###.#.#...#.#.###.#",
    "#.....#.#..#..#.....#",
    "#######.#.#.#.#######",
    "........##...........",
    "#.#####...##..#####..",
    ".#.###....#####..#...",
    "..#.#.#...#.#.##.#.#.",
    "####.#.#.#.####..####",
    "#..########.#..#..#..",
    "........###.#..#..##.",
    "#######..#.#.#..##.#.",
    "#.....#.##.....#####.",
    "#.###.#.#.##.#..#.##.",
    "#.###.#.#.######.....",
    "#.###.#.##..#.##.#...",
    "#.....#....####.###..",
    "#######.###.#..#.###.",
)


# --------------------------------------------------------------- validation


def test_vin_feedback() -> None:
    assert vin_feedback("5yj3e7eb0kf000001")[0]
    assert not vin_feedback("")[0]
    assert not vin_feedback("5YJ3E7EB0KF00000")[0]  # 16 chars
    ok, msg = vin_feedback("5YJ3E7EB0KF00000I")
    assert not ok and "I" in msg


def test_import_path_feedback(tmp_path: Path) -> None:
    assert not import_path_feedback("")[0]
    assert not import_path_feedback(str(tmp_path / "missing.pem"))[0]
    f = tmp_path / "k.pem"
    f.write_text("not read by the check")
    f.chmod(0o644)
    good, msg = import_path_feedback(str(f))
    assert not good and "chmod 600" in msg
    f.chmod(0o600)
    assert import_path_feedback(str(f))[0]


def test_generate_then_import_key(tmp_path: Path) -> None:
    svc = RealServices(tmp_path / "cfg" / "private_key.pem", domain="example.invalid")
    res = svc.generate_key()
    assert res.created and res.path.is_file()
    assert (res.path.stat().st_mode & 0o777) == 0o600
    assert len(res.fingerprint.split(":")) == 8
    with pytest.raises(FileExistsError):
        svc.generate_key()  # never replaces a (possibly paired) key
    other = RealServices(tmp_path / "other" / "private_key.pem")
    imported = other.import_key(res.path)
    assert imported.fingerprint == res.fingerprint
    assert svc.pairing_link() == "https://tesla.com/_ak/example.invalid"


def test_fake_services_never_touch_network(tmp_path: Path) -> None:
    import asyncio

    svc = FakeServices(tmp_path / "private_key.pem", backend_factory=lambda: None)
    assert asyncio.run(svc.check_key()).ok
    with pytest.raises(ValueError):
        asyncio.run(svc.pair("5YJ3E7EB0KF000001", tmp_path, "admin"))


# ---------------------------------------------------------------------- QR


def test_qr_matches_reference_matrix() -> None:
    q = encode("SOCKS", "M", mask=2)
    assert (q.version, q.size) == (1, 21)
    got = tuple("".join("#" if v else "." for v in row) for row in q.modules)
    assert got == SOCKS_V1_M_MASK2


def _read_back(q: object) -> bytes:
    """Minimal decoder: unmask, read the zigzag, de-interleave, check RS, parse byte mode."""
    n, m, f = q.size, q.modules, q.function  # type: ignore[attr-defined]
    bits: list[int] = []
    right = n - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(n):
            for j in range(2):
                x = right - j
                y = n - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not f[y][x]:
                    bits.append(int(m[y][x] ^ _mask_bit(q.mask, x, y)))  # type: ignore[attr-defined]
        right -= 2
    cws = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits) - 7, 8)]
    ec, groups = _BLOCKS[q.ecl][q.version - 1]  # type: ignore[attr-defined]
    sizes = [d for c, d in groups for _ in range(c)]
    blocks: list[list[int]] = [[] for _ in sizes]
    k = 0
    for i in range(max(sizes)):
        for b, size in enumerate(sizes):
            if i < size:
                blocks[b].append(cws[k])
                k += 1
    for _ in range(ec):
        for b in range(len(sizes)):
            blocks[b].append(cws[k])
            k += 1
    data: list[int] = []
    for b, size in zip(blocks, sizes, strict=True):
        assert rs_remainder(b[:size], ec) == b[size:], "error-correction mismatch"
        data += b[:size]
    stream = "".join(f"{c:08b}" for c in data)
    assert stream[:4] == "0100"
    cc = 8 if q.version < 10 else 16  # type: ignore[attr-defined]
    length = int(stream[4 : 4 + cc], 2)
    body = stream[4 + cc : 4 + cc + 8 * length]
    return bytes(int(body[i : i + 8], 2) for i in range(0, len(body), 8))


@pytest.mark.parametrize(
    "text",
    ["https://tesla.com/_ak/auth.stepspower.net", "A", "ž" * 20, "x" * 150, "y" * 210],
)
@pytest.mark.parametrize("ecl", ["L", "M"])
def test_qr_round_trip(text: str, ecl: str) -> None:
    try:
        q = encode(text, ecl)
    except QrError:
        assert len(text.encode()) > data_capacity(10, ecl) - 3
        return
    assert _read_back(q) == text.encode()
    assert 0 <= q.mask < 8 and q.size == q.version * 4 + 17


def test_qr_too_long() -> None:
    with pytest.raises(QrError):
        encode("z" * 400, "M")
