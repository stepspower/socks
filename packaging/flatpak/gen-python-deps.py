#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Generate python3-deps.json (Flatpak module) from a pinned requirements file.

Stands in for flatpak-pip-generator. Reads `name==version` lines, asks PyPI for the
release files, and emits one `pip install --no-index` module with sha256-pinned
sources: a cp313 manylinux wheel per architecture where one exists, else the
pure-Python wheel, else the sdist.

    uv pip compile req.in --python-version 3.13 \
        --python-platform x86_64-manylinux_2_28 -o req.txt
    python3 -I gen-python-deps.py req.txt > python3-deps.json
"""

import json
import re
import sys
import urllib.request

ARCHES = {"x86_64": "x86_64", "aarch64": "aarch64"}


def release_files(name: str, version: str) -> list[dict]:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - fixed https PyPI URL
        return json.load(r)["urls"]


def pick(files: list[dict]) -> list[tuple[str | None, dict]]:
    """Return [(only_arch|None, file)] sources for one package."""
    pure = [f for f in files if f["filename"].endswith("-none-any.whl")]
    out = []
    for arch in ARCHES:
        cands = [
            f
            for f in files
            if f["filename"].endswith(".whl")
            and "-cp313-cp313-" in f["filename"]
            and "manylinux" in f["filename"]
            and arch in f["filename"]
        ] or [
            f
            for f in files
            if f["filename"].endswith(".whl")
            and "-abi3-" in f["filename"]
            and "manylinux" in f["filename"]
            and arch in f["filename"]
        ]
        if cands:
            out.append((arch, cands[0]))
    if len(out) == len(ARCHES):
        return out  # native wheels preferred (aiohttp, multidict, ... are faster)
    if pure:
        return [(None, pure[0])]
    if out:
        return out
    sd = [f for f in files if f["packagetype"] == "sdist"]
    if not sd:
        raise SystemExit(f"no usable file for {files[0]['filename']}")
    return [(None, sd[0])]


def main(req: str) -> None:
    pins = []
    for line in open(req, encoding="utf-8"):  # noqa: SIM115
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;]+)", line)
        if m:
            pins.append((m.group(1), m.group(2)))
    sources, names = [], []
    for name, ver in pins:
        names.append(f"{name}=={ver}")
        for arch, f in pick(release_files(name, ver)):
            src = {"type": "file", "url": f["url"], "sha256": f["digests"]["sha256"]}
            if arch:
                src["only-arches"] = [arch]
            sources.append(src)
    cmd = (
        "pip3 install --verbose --exists-action=i --no-index "
        '--find-links="file://${PWD}" --prefix=${FLATPAK_DEST} '
        + " ".join(f'"{n}"' for n in names)
        + " --no-build-isolation --no-deps --ignore-installed"
    )
    mod = {
        "name": "python3-deps",
        "buildsystem": "simple",
        "build-commands": [cmd],
        "sources": sources,
    }
    json.dump(mod, sys.stdout, indent=4)
    print()


if __name__ == "__main__":
    main(sys.argv[1])
