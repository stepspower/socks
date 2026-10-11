# SOCKS: State Of Charge, Keys & Stats

A lean, fast, local-first Tesla companion for Linux. SOCKS talks to your car directly over
Bluetooth, optionally through the Tesla Fleet API, shows charge status and range, sends the
everyday commands, and keeps a private history of what you drove and charged. It starts in
under a second, runs no background daemon unless you turn polling on, and sends nothing
anywhere except the Tesla endpoints you enabled.

![SOCKS screenshot placeholder](docs/screenshots/status.png)

## Features

- Status: state of charge, charge limit, range, charging power and time to limit, lock state,
  doors, temperatures, odometer.
- Controls: lock/unlock, climate, charge start/stop, charge limit, charge port, flash/honk,
  frunk/trunk, sentry. Commands that change the car go through a confirmation gate.
- History: kilometres per day/week/month, charging sessions and cost, efficiency, charging
  curve, battery health trend. Metric by default, imperial switchable.
- Bluetooth first, cloud as fallback. Works the same on KDE, GNOME, XFCE, X11 and Wayland, and
  follows the system theme and dark mode.
- Only three runtime dependencies: `tesla-fleet-api`, `PySide6-Essentials`, `platformdirs`.

## Install

Flatpak (once published on Flathub):

```bash
flatpak install flathub net.stepspower.Socks
```

With uv or pipx (PyPI name `socks-tesla`, needs Python 3.13):

```bash
uv tool install socks-tesla
# or
pipx install socks-tesla
```

From source:

```bash
git clone https://github.com/stepspower/socks && cd socks
uv venv --python 3.13 && uv pip install -e ".[dev]"
.venv/bin/socks            # GUI
.venv/bin/socks-cli status # command line
```

You need a working BlueZ stack for Bluetooth. No special capabilities or `setcap` are needed.

## First run

1. Enter your VIN.
2. Key: import an existing Tesla BLE key (an EC P-256 PEM already paired with the car, for
   example from Tesla's `tesla-control`), or let SOCKS generate a new one and pair it by
   tapping your key card on the console when prompted.
3. Done. Status and commands work over Bluetooth while you are in range.

The Fleet API is optional and only needed for access out of Bluetooth range. It needs a
developer app and a public key on a domain you control; see `docs/FLEET_API_SETUP.md`.
Tesla bills usage per request: data read $0.002, command $0.001, wake $0.02, with a $10 per month
discount. Polling is off by default and Bluetooth is tried first, so most users pay nothing.

## Privacy

- No telemetry, no analytics, no update checks. The only network traffic is to Tesla endpoints
  you enabled (cloud off by default).
- History is a local SQLite file in `$XDG_DATA_HOME/socks/history.sqlite`.
- The private key and any OAuth tokens live in `$XDG_CONFIG_HOME/socks/` (directory 0700,
  files 0600) and are never logged or stored in the database. In the Flatpak these paths are
  inside `~/.var/app/net.stepspower.Socks/`.
- See `docs/SECURITY.md` for the threat model and how to report a vulnerability.

## Trademark

Tesla is a trademark of Tesla, Inc. This project is not affiliated with, endorsed by, or
sponsored by Tesla, Inc.

## License

GPL-3.0-or-later. See `LICENSE`. Protocol work uses Tesla's open source
[`tesla-fleet-api`](https://github.com/Teslemetry/python-tesla-fleet-api) (Apache-2.0).
