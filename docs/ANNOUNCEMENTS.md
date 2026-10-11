# SOCKS announcement drafts

Every post carries a one-line AI-assistance disclosure (keep it). Each section below is ready to paste. Replace nothing except where marked `[ ]`.
Repo: https://github.com/stepspower/socks

## Do not post before

- [ ] v0.1.0 is tagged and the release assets (flatpak, deb, rpm, wheel/sdist) are live on GitHub, and the install lines in the posts work on a clean machine.
- [ ] `docs/screenshots/*.png` are current (they use `--fake`; the posts say so).
- [x] The security contact mailbox (security@stepspower.net) exists and receives mail.
- [ ] Audit findings M1 (OAuth redirect host vs bind) and M2 (polling wakes the car, no interval floor) from `docs/AUDIT-2026-10-11.md` are fixed. The audit verdict was "fix first", and the posts say findings were addressed.
- [ ] Decide whether "independent" is accurate for the audit. The posts say "security audit" and link the report, without the word.
- [ ] Be online for the first few hours to answer comments.

---

## 1. r/teslamotors

**Title** (84 chars)

SOCKS: open-source Linux app for Tesla, Bluetooth key, no cloud account needed

**Body** (about 175 words)

I built a desktop app for Linux that talks to the car over Bluetooth, using a phone-key-style key you pair with a key card. No Tesla cloud account or subscription is needed. The optional Fleet API covers remote access when you are out of range.

It shows charge, range, temperatures, locks and odometer, and sends the usual commands: lock/unlock, climate, charging, port, frunk/trunk, sentry. Every command asks for confirmation; unlock and trunk make you type the first letters.

History is a local SQLite file: drives, charge sessions, efficiency, battery health trend, learned places, and an automatic km register export.

Limits: the laptop must be within Bluetooth range (about 10 m). The cloud is not free beyond Tesla's $10/month discount. Tesla's own trip history is not available over the API, so history only grows from what the app sees while it is running.

No telemetry. GPL-3.0-or-later. Security audit before release; report in the repo.

Looking for testers, especially on Model S/X and older Model 3/Y.

https://github.com/stepspower/socks

Built with AI assistance (Claude), under my direction and review; testing on a real car and the security review were done by people.

Tesla is a trademark of Tesla, Inc.; not affiliated.

---

## 2. r/linux

**Title**

SOCKS 0.1.0: a Qt desktop companion for Tesla cars, Flatpak and distro packages

**Body** (about 155 words)

SOCKS (State Of Charge, Keys & Stats; yes, it was the acronym that came first) is a Tesla companion for the Linux desktop. Python 3.13 and PySide6-Essentials, nothing else at runtime.

It is plain Qt 6, so it looks the same on KDE, GNOME and XFCE, on X11 and Wayland, and follows the system theme and dark mode. No daemon, no tray requirement, window opens in under a second.

It talks to the car over BlueZ and Bluetooth LE, so no Tesla cloud account is needed. The Fleet API is optional. History lives in a local SQLite file under XDG paths. No telemetry, no update checks.

Install: Flatpak (needs `--system-talk-name=org.bluez`), plus Arch, Fedora and Debian packages, or `uv tool install socks-tesla`.

Limits: you need a Bluetooth LE adapter and to be within about 10 m of the car. GPL-3.0-or-later. Testers on other distros welcome.

https://github.com/stepspower/socks

Built with AI assistance (Claude), under my direction and review; testing on a real car and the security review were done by people.

Tesla is a trademark of Tesla, Inc.; not affiliated.

---

## 3. Tesla Motors Club, "Model 3/Y Software" forum

**Title**

SOCKS: open-source Linux desktop app for Model 3/Y (Bluetooth key, local history, testers wanted)

**Body** (about 190 words)

Hi all. SOCKS is a free Linux desktop app. The name is State Of Charge, Keys & Stats; the acronym came first, so it is what it is.

How it works: the app holds its own key, paired with your key card like a phone key, then talks to the car over Bluetooth LE. No Tesla cloud account is needed. The Fleet API is optional, for out of range.

What it does: status (charge, range, temperatures, locks, odometer); controls (lock/unlock, climate, charging, port, frunk/trunk, sentry, wake); local SQLite history (drives, charges, efficiency, battery health, learned places, automatic km register export).

What it can't do:
- The laptop has to be within about 10 m of the car for Bluetooth.
- Cloud use is paid per request beyond Tesla's $10/month discount. Off by default.
- Tesla does not expose the car's own trip history through the API, so history builds up only from what the app observes.

Every command needs confirmation. No telemetry. GPL-3.0-or-later. A security audit was done before release.

Testers welcome (Model 3/Y, S/X from 2021).

https://github.com/stepspower/socks

Built with AI assistance (Claude), under my direction and review; testing on a real car and the security review were done by people.

Tesla is a trademark of Tesla, Inc.; not affiliated.

---

## 4. Hacker News

**Title** (66 chars)

Show HN: SOCKS, a Linux desktop app for Tesla over Bluetooth, no cloud

**First comment** (about 190 words)

Hi, author here. I wanted my car's charge state and my own record of drives and charges on a laptop, without a service holding the data or paying for API access to read a battery percentage.

Tesla's BLE protocol lets any device act as a key: you generate a key pair, the car shows a prompt, and you tap your key card to authorise the public key. After that the laptop talks to the car directly over Bluetooth LE, with signed commands. SOCKS does not implement any crypto; it uses Tesla's open source `tesla-fleet-api` library. The private key stays in `~/.config/socks/` (0600); revoke it from the car's Locks screen.

The Fleet API is optional, for remote access; it needs a developer app and a domain, and costs money past Tesla's $10/month discount.

History is local SQLite. The car's own trip history is not available via the API, so it is built only from what the app sees.

Python 3.13, PySide6-Essentials, GPL-3.0-or-later, no telemetry. It had a security audit before release; the report is in docs/. The name is State Of Charge, Keys & Stats, and I'm aware.

https://github.com/stepspower/socks

Built with AI assistance (Claude), under my direction and review; testing on a real car and the security review were done by people.

---

## 5. Mastodon / X

SOCKS (State Of Charge, Keys & Stats): open-source Linux desktop app for Tesla. Bluetooth key, no cloud account needed, local history, no telemetry. GPL-3.0+. Built with AI assistance (Claude). Testers welcome. https://github.com/stepspower/socks #Linux #Tesla #OpenSource

(Tesla trademark line does not fit in 280 characters; put it in a reply or the profile bio: "Tesla is a trademark of Tesla, Inc.; not affiliated.")
