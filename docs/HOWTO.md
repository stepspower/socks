# SOCKS first-run guide

For people comfortable with a terminal but new to Bluetooth keys. Allow 15 minutes. You need
to be within a few metres of the car for steps 5 and 6.

## Requirements

- **Computer:** Linux with BlueZ 5.x and a Bluetooth LE adapter (most laptops have one). The
  laptop must be within about 10 m of the car.
- **Permissions:** usually no group membership is needed (Ubuntu and Fedora allow desktop users
  by default). Only if BlueZ refuses with a permission error, check your distro's polkit rules
  or add yourself to the `bluetooth` group. The Flatpak needs access to BlueZ on the system bus
  (`--system-talk-name=org.bluez`).
- **Car:** Bluetooth phone-key support: Model 3 and Y (all years), Model S and X (2021 and later).
- **Software:** Python 3.13 or the Flatpak. Nothing else at runtime.
- **Cloud (optional):** a Tesla account with MFA, a developer application, a domain you control
  serving the public key over valid TLS, and a payment card at Tesla (the billing limit is 0
  otherwise). Prices: data $0.002, command $0.001, wake $0.02, with a $10 per month discount.

## 1. Install the system pieces

SOCKS needs BlueZ (the Linux Bluetooth stack), Python 3.13 and uv (a Python tool installer).

| Distro | Command |
|---|---|
| Ubuntu / Mint / Debian | `sudo apt install bluez python3.13` (if 3.13 is missing, skip it: uv installs Python itself) |
| Fedora | `sudo dnf install bluez python3.13` |
| Arch | `sudo pacman -S bluez bluez-utils python` |

Make sure Bluetooth is running: `systemctl status bluetooth` (start it with
`sudo systemctl enable --now bluetooth`). `bluetoothctl show` should print your controller.

Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh` or your distro's `uv` package.

## 2. Install SOCKS

```bash
uv tool install socks-tesla --python 3.13
```

(`pipx install socks-tesla` also works if pipx already runs Python 3.13.) This gives you two
programs: `socks` (the window) and `socks-cli` (the command line).

Alternatives: the Flatpak bundle from the GitHub releases page (`flatpak install --user
socks.flatpak`, Flathub listing submitted but not yet live), or the `.deb`, `.rpm` or wheel from
the same page. Try it with no car first: `socks --fake`.

## 3. Find your VIN

The 17-character VIN is on the bottom-left corner of the windscreen (visible from outside), in
the Tesla app under Software, on the touchscreen under Controls > Software > Additional
Vehicle Information, and on your registration papers.

## 4. Get a key

A key is a file that proves to the car that SOCKS may talk to it. Two ways:

- **You already have a paired key** (for example from Tesla's `tesla-control`). Import it:
  `socks-cli key import /path/to/private_key.pem`. The car already trusts it, so skip step 5.
- **New key:** `socks-cli key generate`. It is stored in `~/.config/socks/` (private, mode
  0600). `socks-cli key public` prints the public half and a fingerprint, which is safe to share.

Never share or upload the private key.

## 5. Pair a new key with the car

Stand next to the car with an existing key card in hand, then run:

```bash
socks-cli key pair [--role driver|owner] [--vin VIN]
```

(`--vin` is only needed if you have not saved your VIN yet; the default role is the one the
command documents in `socks-cli key pair -h`.) It prints "Tap your key card on the centre console
to confirm (waiting up to 60 s)". The car shows a prompt on the touchscreen: tap your key card
on the centre console (just behind the cupholders, below the screen). The command returns
success once the car confirms. If it times out, check the car's Locks screen: the key may have
been added anyway, or the prompt may have been dismissed. Then re-run, making sure Bluetooth is
on and the car is awake.

## 6. First read

Run `socks`, open the Settings tab, enter your VIN and save, then press Refresh on the Status
tab. Expect a few seconds for the first Bluetooth connection (the car wakes its Bluetooth
radio), then state of charge, range, temperatures and lock state appear, with a "BLE" badge.
The same from the terminal: `socks-cli status`. If it times out, see Troubleshooting in the
README.

Try one harmless command: `socks-cli cmd flash_lights`. Dangerous commands (`unlock`, `frunk`,
`trunk`, `sentry_off`) need `--yes`. The GUI has no confirmation dialogs: risky switches
(unlock, sentry off, open charge port/frunk/trunk) only act after a full slide, or Enter/Space
held for a second.

### Log file

SOCKS writes a log to `~/.local/state/socks/socks.log` (`$XDG_STATE_HOME/socks/`; rotated at
1 MB, 3 old files kept; folder 0700, file 0600). Settings > **Open log folder** opens it. Start
with `socks --debug` to log Bluetooth and Fleet API details as well. Tokens, secrets and private
keys are removed from every line before it is written, so the file can be attached to a bug
report — still read it over first, because it does contain your VIN and car state.

## 7. How history accumulates

SOCKS records a snapshot every time it reads the car, but only when something changed or at
least 5 minutes have passed. **Polling is off by default**: a background reader would keep
waking the car (draining the battery slightly) and, with the cloud, cost money. So history
fills in whenever you open SOCKS and refresh. Want a denser history? On the Settings tab set a
poll interval (minimum 60 s over Bluetooth, longer with cloud on); it only runs while the window
is open. Charge sessions and drives are derived from those snapshots, so gaps make coarser
sessions.

### Drive mode and recording a drive

When a Bluetooth read shows the car in gear, SOCKS switches to **drive mode**: it reads the
car every 15 s over Bluetooth (free; never over the cloud) and records a detailed track (speed,
power, state of charge and, if location is on, position) that the History tab shows as a
drive profile and can export as GPX. Two reads in P end drive mode.

To record with the laptop lid closed, turn on **Settings > Keep this computer awake while
driving (lid closed)** (off by default). While the car is in gear SOCKS then holds a
systemd-logind "sleep / lid switch" inhibitor and shows an *awake lock* chip; it releases it
when the car parks or SOCKS quits. Notes:

- **Battery cost:** an awake laptop typically uses about 5–10 % of its battery per hour.
- **The laptop must already be awake when the drive starts.** The lock is only taken once a
  read shows the car in gear; a laptop that suspended before that stays asleep.
- If your system refuses the inhibitor (polkit policy), SOCKS shows a one-line note and
  continues without it.

When the laptop was asleep for the whole drive, SOCKS has no Bluetooth track. If the cloud and
**Follow trips via cloud while the car is away** are on, it records one cloud position every
10 minutes (never waking the car; this costs data reads, see section 9). Without the cloud the
drive is reconstructed from the odometer and state of charge only.

### Status tab: switches, click-to-change and Depart by

- Click a value (charge limit, target temperature, charging amps) to change it in place.
- Switches for lock, climate, charge port, frunk, trunk and sentry act on release. The risky
  direction (unlock, frunk/trunk, sentry off) needs a full slide or holding Enter for about a
  second, replacing the typed challenge on this tab. The Controls tab keeps typed challenges.
- **Depart by...** (or `socks-cli depart 07:30 --limit 90 --temp 21`, `--cancel` to clear)
  sets a one-off departure: the car charges to the chosen % and warms the cabin so both are
  ready at that time. The car does the timing, so SOCKS can be closed. It is cleared
  automatically once done.

### Location, places and the mileage register

1. **Location** is off by default. Turn it on in Settings ("Read and show the car's location");
   positions stay on this computer. The Status tab then shows a Location card with "Open in
   map" (openstreetmap.org in your browser) and Copy.
2. **Place names** come from your own drives first: SOCKS learns places from drive start and
   end points (History > Places; double-click or F2 to rename). Optional online lookup is a
   separate Settings switch, **off by default**; when on it sends rounded coordinates to
   openstreetmap.org (Nominatim).
3. Drives are labelled automatically (for example Home to Work). Click the Purpose cell of a
   drive to cycle none, business, private, commute; **Rebuild labels** relearns places.
4. **History > Export... > Mileage register YYYY (CSV)** writes a CSV with one row per trip (date, times,
   odometer start/end, km, purpose, places) for a yearly register, the shape used for the Dutch
   *rittenregistratie*. **Disclaimer:** this is a convenience export only. You are responsible
   for the register being complete and correct (it must cover every trip of the year and carry
   accurate places) and for complying with the tax rules. SOCKS does not verify compliance.

## 8. Units, language, tariff, backup

- Units: Settings > Units, metric (km, kWh, °C) or imperial. Stored values stay metric;
  only the display changes.
- Language: Settings > Language (English, Nederlands, Cestina, Deutsch, Norsk bokmal, Svenska).
  Translations are machine-drafted; corrections welcome, see [TRANSLATING.md](TRANSLATING.md).
- Tariff: set your price per kWh to get charging cost on the History tab.
- Backup: history is one file, `~/.local/share/socks/history.sqlite`. Copy it while SOCKS is
  closed (or use `sqlite3 history.sqlite ".backup backup.sqlite"`). Settings, key and tokens
  are in `~/.config/socks/`; back that up somewhere private, because it holds the key.

## 9. Optional: cloud access

See [FLEET_API_SETUP.md](FLEET_API_SETUP.md). Commands: `socks-cli fleet login`,
`socks-cli fleet register`, `socks-cli fleet status`, `socks-cli fleet logout`.
Prices: data $0.002, command $0.001, wake $0.02, with a $10 monthly discount.

**Charging invoices:** `socks-cli fleet charging --since 2026-01-01 --download ~/Invoices --csv
charging.csv` lists your Supercharger sessions, saves new invoice PDFs and writes a summary CSV.
`--after-download CMD` runs a command once with the new PDF paths appended (no shell). Per Tesla's
pricing spec these endpoints are not charged.

## 10. Uninstall and revoke

1. **Revoke the key on the car first:** Controls > Locks, find the key named for SOCKS (or an
   unknown "phone key") and delete it. After this the file is useless to anyone.
2. `socks-cli fleet logout` if you used the cloud.
3. `uv tool uninstall socks-tesla` (or `pipx uninstall socks-tesla`).
4. Delete `~/.config/socks/` and `~/.local/share/socks/` if you want no trace.
