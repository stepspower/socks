# Contributing

## Dev setup

```bash
uv venv --python 3.13
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/ruff check --config ruff.toml .
.venv/bin/ruff format --config ruff.toml .
.venv/bin/pytest -q
```

CI also runs `pip-audit`, `bandit -r socks -ll`, and desktop/AppStream validation.
The GUI runs against fake data with `python -m socks --fake`.

## Rules

- Tests and development never touch a real car, Bluetooth device or Tesla endpoint. Use the
  fakes in `tests/fakes.py`. Test keys are generated throwaways, never real keys.
- Runtime dependencies stay at three (see `pyproject.toml`). Lazy-import heavy modules inside
  functions. Do not add numpy, pandas or a charting library.
- Type hints everywhere. Every file starts with
  `# SPDX-License-Identifier: GPL-3.0-or-later` and `# Copyright (C) 2026 stepspower`.
- Never log or raise secrets (keys, tokens). All SQL is parameterised. Anything that changes
  the car goes through the single GUI `confirm()` gate.
- Colours come from `QPalette`, not hardcoded values.

## Sign-off

Certify the [Developer Certificate of Origin](https://developercertificate.org/) by signing
off each commit: `git commit -s`, which adds `Signed-off-by: Your Name <you@example.org>`.

## Architecture

See `docs/ARCHITECTURE.md`. Decisions go, one line each, in `docs/DECISIONS.md`.
Security issues: do not open a public issue, see `docs/SECURITY.md`.
