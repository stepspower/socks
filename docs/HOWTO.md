# SOCKS first-run guide

For people comfortable with a terminal but new to Bluetooth keys. Allow 15 minutes. You need
to be within a few metres of the car for steps 5 and 6.

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

Try it with no car first: `socks --fake`.

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

Stand next to the car with an existing key card (or phone key) in hand, then run:

```bash
socks-cli key pair
```

The car shows a prompt on the touchscreen asking you to authenticate the new key. Tap your key
card on the centre console (the spot just behind the cupholders, below the screen), then confirm
on the screen if asked. The terminal reports success. If nothing happens within a minute,
re-run the command; check Bluetooth is on and the car is awake.

## 6. First read

Run `socks`, open the Settings tab, enter your VIN and save, then press Refresh on the Status
tab. Expect a few seconds for the first Bluetooth connection (the car wakes its Bluetooth
radio), then state of charge, range, temperatures and lock state appear, with a "BLE" badge.
The same from the terminal: `socks-cli status`. If it times out, see Troubleshooting in the
README.

Try one harmless command: `socks-cli cmd flash_lights`. Dangerous commands (`unlock`, `frunk`,
`trunk`, `sentry_off`) need `--yes`. In the GUI each command shows a confirmation dialog first.

## 7. How history accumulates

SOCKS records a snapshot every time it reads the car, but only when something changed or at
least 5 minutes have passed. **Polling is off by default**: a background reader would keep
waking the car (draining the battery slightly) and, with the cloud, cost money. So history
fills in whenever you open SOCKS and refresh. Want a denser history? On the Settings tab set a
poll interval (minimum 60 s over Bluetooth, longer with cloud on); it only runs while the window
is open. Charge sessions and drives are derived from those snapshots, so gaps make coarser
sessions.

## 8. Units, tariff, backup

- Units: Settings > Units, metric (km, kWh, °C) or imperial. Stored values stay metric;
  only the display changes.
- Tariff: set your price per kWh to get charging cost on the History tab.
- Backup: history is one file, `~/.local/share/socks/history.sqlite`. Copy it while SOCKS is
  closed (or use `sqlite3 history.sqlite ".backup backup.sqlite"`). Settings, key and tokens
  are in `~/.config/socks/`; back that up somewhere private, because it holds the key.

## 9. Optional: cloud access

See [FLEET_API_SETUP.md](FLEET_API_SETUP.md). Commands: `socks-cli fleet login`,
`socks-cli fleet register`, `socks-cli fleet status`, `socks-cli fleet logout`.
Prices: data $0.002, command $0.001, wake $0.02, with a $10 monthly discount.

## 10. Uninstall and revoke

1. **Revoke the key on the car first:** Controls > Locks, find the key named for SOCKS (or an
   unknown "phone key") and delete it. After this the file is useless to anyone.
2. `socks-cli fleet logout` if you used the cloud.
3. `uv tool uninstall socks-tesla` (or `pipx uninstall socks-tesla`).
4. Delete `~/.config/socks/` and `~/.local/share/socks/` if you want no trace.
