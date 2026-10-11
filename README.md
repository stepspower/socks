# SOCKS: State Of Charge, Keys & Stats

**SOCKS for your Tesla.** **SOCKS** = **S**tate **O**f **C**harge, **K**eys & **S**tats. *SoC* is what battery engineers call the state of charge, the one number every EV app revolves around, so the name is the battery term plus the two other things the app does, in the spirit of Tesla naming things Plaid and Ludicrous. Hence the sock with a lightning bolt.

**A lean, local-first Tesla companion for Linux: Bluetooth control, charge status, and a private history of what you drove and charged.**

![SOCKS status tab](docs/screenshots/status.png)

## Why

- **Lean.** Three runtime dependencies, no background daemon, usable window in under a second.
- **Local-first.** Your key, your history and your settings stay on your computer. No telemetry,
  no update checks, no account.
- **No cloud needed.** Talks to the car directly over Bluetooth. The Tesla Fleet API is an
  optional extra for when you are out of range.
- **Open source.** GPL-3.0-or-later. Protocol work is Tesla's own open source library,
  [`tesla-fleet-api`](https://github.com/Teslemetry/python-tesla-fleet-api); SOCKS adds no crypto.
- **Everywhere.** Qt 6; same on KDE, GNOME, XFCE, X11 and Wayland; follows system theme and dark mode.

## Features

**Status.** State of charge, range, charging, temperatures, locks and closures, odometer, and a
Location card (opt-in; open in OpenStreetMap or copy). Values are clickable to change them, and
risky switches (unlock, frunk, trunk, sentry off) need a full slide or a held Enter instead of a
typed challenge.

**Depart by.** Pick a time, a charge % and a cabin temperature once; the car does the timing and
the schedule clears itself afterwards. Also `socks-cli depart 07:30 --limit 90 --temp 21`.

![Status tab](docs/screenshots/status.png)

**Controls.** Lock/unlock, climate, charging, charge port, flash/honk, frunk/trunk, sentry, wake.
Every command is confirmed; dangerous ones keep a typed challenge here.

![Controls tab](docs/screenshots/controls.png)

**History.** Distance per day, charge sessions and cost (set your tariff), consumption,
state-of-charge curve, battery health, per-drive profiles and GPX export. Metric by default,
imperial switchable.

![History tab](docs/screenshots/history.png)

**Places and mileage register.** SOCKS learns places from where your drives start and end,
labels drives automatically, and lets you set the purpose (business, private, commute) in one click.
**Export > Mileage register (CSV)** writes a yearly km CSV in the layout of a Dutch
*rittenregistratie*. It is a convenience export only: you are responsible for the register being
complete and correct and for complying with the tax rules; SOCKS does not verify compliance.
Reverse geocoding (place names) is off by default; turning it on sends coordinates to
openstreetmap.org.

**Charging invoices.** `socks-cli fleet charging --download DIR --csv summary.csv` fetches your
Supercharger history and invoice PDFs through the Fleet API. Tesla does not charge for these
endpoints (per their pricing spec).

**Drive mode (bonus).** While the car is in gear, SOCKS reads it every 15 s over Bluetooth and
records a detailed track. There is an option to keep the computer awake with the lid closed.
The honest caveat: the laptop must already be awake when the drive starts. Otherwise, if
trip watch and the cloud are on, away trips get one cloud position per 10 minutes.

**Languages.** English, plus Dutch, Czech, German, Norwegian Bokmal and Swedish. The translations
are machine-drafted and welcome review: see [docs/TRANSLATING.md](docs/TRANSLATING.md).

![Settings tab](docs/screenshots/settings.png)

The screenshots use `--fake` (a simulated car and synthetic data).

## Requirements

- Linux with BlueZ 5.x and a Bluetooth LE adapter; keep the laptop within about 10 m of the car.
  Your user usually does not need to be in the `bluetooth` group (Ubuntu and Fedora allow
  desktop users by default); add it only if BlueZ polkit rules on your distro require it.
- A Tesla with Bluetooth phone-key support: Model 3 and Y (all years), Model S and X (2021 and later).
- Python 3.13, or the Flatpak (which needs access to BlueZ on the system bus,
  `--system-talk-name=org.bluez`). Nothing else at runtime.
- Cloud (optional): a Tesla account with MFA, a Tesla developer application, a domain you control
  serving the public key over valid TLS, and a payment card at Tesla (the billing limit is 0
  otherwise). Prices: data $0.002, command $0.001, wake $0.02, with a $10 per month discount.

## Install

Pick one. Needs a working BlueZ for Bluetooth. Step-by-step with distro package names:
[docs/HOWTO.md](docs/HOWTO.md). Deb, rpm and wheel files are also on the releases page.

**Flatpak** (any distro, recommended). Download `socks.flatpak` from the
[GitHub releases page](https://github.com/stepspower/socks/releases):

```bash
flatpak install --user socks.flatpak
```

It needs the `org.kde.Platform` 6.11 runtime, which flatpak installs automatically when the
Flathub remote is configured (`flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo`).
*Why not on Flathub?* Flathub's policy excludes AI-assisted manifests and submissions, and this
project is built with AI assistance (see [Built with AI assistance](#built-with-ai-assistance)).

**Per distro** (packages are attached to each GitHub release; they bundle the Python
dependencies that distros lack, and use your distro's PySide6):

| Distro | Command |
|---|---|
| Debian 13 / Ubuntu 25.04+ | `sudo apt install ./socks-tesla_*.deb` |
| Fedora 42+ | `sudo dnf install ./socks-tesla-*.rpm` |

Ubuntu 24.04 and Linux Mint 22 ship Python 3.12 and an older PySide6, so the .deb does not
install there: use the Flatpak (or uv/pipx below, which fetches Python 3.13 itself).

**uv or pipx** (PyPI name `socks-tesla`, Python 3.13):

```bash
uv tool install socks-tesla --python 3.13     # or: pipx install socks-tesla
```

**From source:**

```bash
git clone https://github.com/stepspower/socks && cd socks
uv sync --extra dev
.venv/bin/socks
```

## Quick start

1. **Key.** Import a key already paired with your car, or create a new one:
   ```bash
   socks-cli key import /path/to/private_key.pem   # existing, already paired
   socks-cli key generate                          # or: make a new one
   ```
2. **Pair** a new key with the car (stand next to it, key card ready):
   ```bash
   socks-cli key pair [--role driver|owner] [--vin VIN]
   ```
   It prints "Tap your key card on the centre console to confirm (waiting up to 60 s)". Tap the
   card; the command succeeds when the car confirms. On timeout, check the car's Locks screen.
   Skip this if you imported an already-paired key.
3. **Run** `socks`, enter your VIN on the Settings tab, and press Refresh.

Try it without a car: `socks --fake` or `socks-cli --fake status`.

## Optional cloud

The Fleet API gives remote access when Bluetooth is out of range. It needs a Tesla developer app
and a public key hosted on a domain you control: follow
[docs/FLEET_API_SETUP.md](docs/FLEET_API_SETUP.md). Tesla bills per request: data read $0.002,
command $0.001, wake $0.02, with a $10 per month discount. SOCKS tries Bluetooth first and
polling is off by default, so most people pay nothing.

## Privacy and security

- No telemetry. The only network traffic is to Tesla endpoints you enabled (cloud is off by default).
- Private key and tokens live in `$XDG_CONFIG_HOME/socks/` (directory 0700, files 0600), never
  logged and never in the database. History is a local SQLite file in `$XDG_DATA_HOME/socks/`.
- All state-changing commands go through one confirmation gate (slide switch or typed challenge).
- Location, geocoding, cloud and trip watch are all off until you turn them on.
- See [docs/SECURITY.md](docs/SECURITY.md) (threat model, reporting). An in-project security
  review by a separate reviewer was done before release; the report is
  [docs/AUDIT-2026-10-11.md](docs/AUDIT-2026-10-11.md). It is not a third-party audit.

## Troubleshooting

| Symptom | Try |
|---|---|
| "No Bluetooth adapter" or permission errors | `systemctl status bluetooth`; `bluetoothctl show` must list a controller; make sure Bluetooth is not blocked (`rfkill list`). Your user normally needs no extra group. |
| Car not found / timeouts | The car is asleep or out of range (about 5 to 10 m). Stand next to it, wake it with a door or the Tesla app, retry. |
| Fleet login: "Only local hosts" | Tesla's portal accepts plain `http://` redirect URIs only for `localhost` with a port. Register exactly the three `http://localhost:5368x/callback` URIs from the setup guide. |
| Wrong or no data | Check the VIN (17 characters) on the Settings tab; run `socks --debug` for a log. |

## Built with AI assistance

The code, packaging and documentation of SOCKS were produced by AI coding agents (Claude) under
the owner's direction and review. Design decisions, live testing on a real car, and handling of
the security review's findings were done by people. The project is GPL-licensed and
contributions are welcome; report issues the normal way.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Trademark

Tesla is a trademark of Tesla, Inc. This project is not affiliated with, endorsed by, or
sponsored by Tesla, Inc.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).
