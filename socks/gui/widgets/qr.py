# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Compact QR Code encoder: byte mode, EC level L or M, versions 1-10 (ISO/IEC 18004).

Enough for a pairing URL. Pure Python, no dependencies. ``encode(text)`` returns a
``QrCode`` whose ``modules[y][x]`` is True for dark; ``gui.wizard.QrView`` paints it.
Verified bit-for-bit against the reference ``qrcode`` library for all masks, L/M, v1-10.
"""

from __future__ import annotations

from dataclasses import dataclass

# (ec codewords per block, ((block count, data codewords per block), ...)) per version
_BLOCKS: dict[str, tuple[tuple[int, tuple[tuple[int, int], ...]], ...]] = {
    "L": (
        (7, ((1, 19),)), (10, ((1, 34),)), (15, ((1, 55),)), (20, ((1, 80),)),
        (26, ((1, 108),)), (18, ((2, 68),)), (20, ((2, 78),)), (24, ((2, 97),)),
        (30, ((2, 116),)), (18, ((2, 68), (2, 69))),
    ),
    "M": (
        (10, ((1, 16),)), (16, ((1, 28),)), (26, ((1, 44),)), (18, ((2, 32),)),
        (24, ((2, 43),)), (16, ((4, 27),)), (18, ((4, 31),)), (22, ((2, 38), (2, 39))),
        (22, ((3, 36), (2, 37))), (26, ((4, 43), (1, 44))),
    ),
}  # fmt: skip
_ALIGN = ((), (6, 18), (6, 22), (6, 26), (6, 30), (6, 34), (6, 22, 38), (6, 24, 42),
          (6, 26, 46), (6, 28, 50))  # fmt: skip
_FORMAT_EC = {"L": 1, "M": 0}
MAX_VERSION = 10


class QrError(ValueError):
    pass


def _gf_mul(a: int, b: int) -> int:
    r = 0
    for i in range(7, -1, -1):
        r = (r << 1) ^ ((r >> 7) * 0x11D)
        r ^= ((b >> i) & 1) * a
    return r


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def rs_remainder(data: list[int], degree: int) -> list[int]:
    div = _rs_divisor(degree)
    result = [0] * degree
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i in range(degree):
            result[i] ^= _gf_mul(div[i], factor)
    return result


def data_capacity(version: int, ecl: str) -> int:
    _ec, groups = _BLOCKS[ecl][version - 1]
    return sum(n * d for n, d in groups)


def _bits(value: int, length: int) -> list[int]:
    return [(value >> i) & 1 for i in range(length - 1, -1, -1)]


def _codewords(payload: bytes, ecl: str) -> tuple[int, list[int]]:
    for version in range(1, MAX_VERSION + 1):
        cap = data_capacity(version, ecl)
        ccbits = 8 if version < 10 else 16
        if 4 + ccbits + 8 * len(payload) <= cap * 8:
            break
    else:
        raise QrError(f"{len(payload)} bytes do not fit a version-{MAX_VERSION} {ecl} code")
    bits = _bits(0b0100, 4) + _bits(len(payload), ccbits)
    for b in payload:
        bits += _bits(b, 8)
    bits += [0] * min(4, cap * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < cap:
        data.append(pad)
        pad ^= 0xEC ^ 0x11
    ec, groups = _BLOCKS[ecl][version - 1]
    blocks, pos = [], 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(data[pos : pos + size])
            pos += size
    eccs = [rs_remainder(b, ec) for b in blocks]
    out = [b[i] for i in range(max(map(len, blocks))) for b in blocks if i < len(b)]
    out += [e[i] for i in range(ec) for e in eccs]
    return version, out


def _mask_bit(mask: int, x: int, y: int) -> bool:
    return (
        (x + y) % 2 == 0,
        y % 2 == 0,
        x % 3 == 0,
        (x + y) % 3 == 0,
        (x // 3 + y // 2) % 2 == 0,
        x * y % 2 + x * y % 3 == 0,
        (x * y % 2 + x * y % 3) % 2 == 0,
        ((x + y) % 2 + x * y % 3) % 2 == 0,
    )[mask]


@dataclass
class QrCode:
    version: int
    ecl: str
    mask: int
    modules: list[list[bool]]
    function: list[list[bool]]

    @property
    def size(self) -> int:
        return len(self.modules)


class _Builder:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = n = version * 4 + 17
        self.m = [[False] * n for _ in range(n)]
        self.f = [[False] * n for _ in range(n)]

    def fn(self, x: int, y: int, dark: bool) -> None:
        self.m[y][x] = dark
        self.f[y][x] = True

    def draw_function_patterns(self) -> None:
        n = self.size
        for i in range(n):
            self.fn(6, i, i % 2 == 0)
            self.fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.fn(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _ALIGN[self.version - 1]
        last = len(pos) - 1
        for i, ay in enumerate(pos):
            for j, ax in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.fn(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0, "L")  # reserve; real bits drawn after masking
        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.version << 12 | rem
            for i in range(18):
                a, b = n - 11 + i % 3, i // 3
                self.fn(a, b, (bits >> i) & 1 == 1)
                self.fn(b, a, (bits >> i) & 1 == 1)

    def draw_format(self, mask: int, ecl: str) -> None:
        n = self.size
        data = _FORMAT_EC[ecl] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1

        for i in range(6):
            self.fn(8, i, bit(i))
        self.fn(8, 7, bit(6))
        self.fn(8, 8, bit(7))
        self.fn(7, 8, bit(8))
        for i in range(9, 15):
            self.fn(14 - i, 8, bit(i))
        for i in range(8):
            self.fn(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.fn(8, n - 15 + i, bit(i))
        self.fn(8, n - 8, True)

    def place(self, codewords: list[int]) -> None:
        n, i, total = self.size, 0, len(codewords) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    y = n - 1 - vert if ((right + 1) & 2) == 0 else vert
                    if not self.f[y][x] and i < total:
                        self.m[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if not self.f[y][x] and _mask_bit(mask, x, y):
                    self.m[y][x] = not self.m[y][x]


def penalty(m: list[list[bool]]) -> int:
    n, score = len(m), 0
    lines = [list(r) for r in m] + [[m[y][x] for y in range(n)] for x in range(n)]
    finder = [True, False, True, True, True, False, True]
    for line in lines:
        run, prev = 0, None
        for v in line:
            if v == prev:
                run += 1
            else:
                score += run - 2 if run >= 5 else 0
                run, prev = 1, v
        score += run - 2 if run >= 5 else 0
        padded = [False] * 4 + line + [False] * 4
        for k in range(len(padded) - 10):
            w = padded[k : k + 11]
            if (w[:7] == finder and not any(w[7:])) or (w[4:] == finder and not any(w[:4])):
                score += 40
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, m))
    score += (abs(dark * 20 - n * n * 10) + n * n - 1) // (n * n) - 1 if dark else 0
    return score


def encode(text: str | bytes, ecl: str = "M", mask: int | None = None) -> QrCode:
    """Encode ``text`` (UTF-8) in byte mode; ``mask`` None picks the lowest penalty."""
    if ecl not in _BLOCKS:
        raise QrError("ecl must be 'L' or 'M'")
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    version, cws = _codewords(payload, ecl)
    best: tuple[int, int, list[list[bool]]] | None = None
    template = _Builder(version)
    template.draw_function_patterns()
    template.place(cws)
    for k in range(8) if mask is None else (mask,):
        b = _Builder(version)
        b.m = [row[:] for row in template.m]
        b.f = template.f
        b.apply_mask(k)
        b.draw_format(k, ecl)
        p = penalty(b.m) if mask is None else 0
        if best is None or p < best[0]:
            best = (p, k, b.m)
    if best is None:  # unreachable: the loop runs at least once
        raise QrError("no mask evaluated")
    return QrCode(version, ecl, best[1], best[2], [r[:] for r in template.f])
