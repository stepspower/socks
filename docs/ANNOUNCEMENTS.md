# SOCKS announcement drafts

Each section below is ready to paste.
Repo: https://github.com/stepspower/socks
Release: https://github.com/stepspower/socks/releases/tag/v0.1.0

## Do not post before

- [x] v0.1.0 is tagged and the release is published on GitHub.
- [x] Install assets on the release: Flatpak bundle, .deb, wheel. (Not on Flathub, by choice: its policy excludes AI-assisted submissions. The posts say so in one clause.)
- [ ] rpm asset attached to the release (optional; the posts say "rpm coming", so post without it if it is not ready, and edit that phrase if it lands).
- [x] Screenshots current (they use `--fake`, and the posts say "screenshots use a simulated car" only where relevant).
- [x] Security review done; wording used is "in-project security review by a separate reviewer, report in the repo". Do not call it an independent or third-party audit.
- [x] Security contact mailbox exists (see docs/SECURITY.md).
- [ ] Be online for the first few hours to answer comments.

---

## 1. r/teslamotors

**Title** (77 chars)

SOCKS: open-source Linux app for Tesla over Bluetooth, no cloud account needed

**Body** (about 167 words)

I built a Linux desktop app for Tesla. It talks to the car over Bluetooth with its own key, paired with your key card, so no Tesla cloud account is needed. The Fleet API is optional, for when you are out of range.

SOCKS = State Of Charge, Keys & Stats. SoC is the battery term for state of charge, hence the sock with a lightning bolt.

Status has slide switches for lock, sentry, port, frunk/trunk and climate, plus click-to-change values. There is a one-off "Depart by" (time, charge %, cabin temperature). History is local SQLite: drives, charges, efficiency, battery health, learned places, and a yearly km-register export. Location is opt-in; so are OpenStreetMap labels.

Limits: the laptop must be within Bluetooth range (about 10 m). Cloud costs money beyond Tesla's $10/month discount. Tesla's trip history isn't in the API, so history only grows from what the app sees.

No telemetry. GPL-3.0-or-later. Six languages (machine-drafted). Built with AI assistance (Claude), under my review. In-project security review, report in the repo.

Flatpak bundle and .deb on the release. Testers wanted:
https://github.com/stepspower/socks/releases/tag/v0.1.0

Tesla is a trademark of Tesla, Inc.; not affiliated.

---

## 2. r/linux

**Title**

SOCKS 0.1.0: Qt desktop companion for Tesla cars, Flatpak bundle and .deb

**Body** (about 164 words)

SOCKS (State Of Charge, Keys & Stats; the acronym came first, the app followed) is a Tesla companion for the Linux desktop. Python 3.13 and PySide6-Essentials.

It is plain Qt 6, so it looks the same on KDE, GNOME and XFCE, X11 and Wayland, and follows system theme and dark mode. No daemon; the window opens in under a second. It has a setup wizard and six languages (machine-drafted, review welcome).

It talks to the car over BlueZ and Bluetooth LE, so no Tesla cloud account is needed; the Fleet API is optional. History is a local SQLite file under XDG paths. No telemetry, no update checks.

Install: Flatpak bundle, .deb or wheel from the GitHub release. It is not on Flathub because Flathub's policy excludes AI-assisted submissions, and this project was built with AI assistance (Claude), under review.

Needs a Bluetooth LE adapter and about 10 m to the car. GPL-3.0-or-later.

https://github.com/stepspower/socks/releases/tag/v0.1.0

Tesla is a trademark of Tesla, Inc.; not affiliated.

---

## 3. Tesla Motors Club, "Model 3/Y Software" forum

**Title**

SOCKS: open-source Linux desktop app for Model 3/Y (Bluetooth key, local history, testers wanted)

**Body** (about 215 words)

SOCKS is a free Linux desktop app. The name is State Of Charge, Keys & Stats; the acronym came first, so it is what it is.

SOCKS = State Of Charge, Keys & Stats. SoC is the battery term for state of charge, hence the sock with a lightning bolt.

How it works: the app holds its own key, paired with your key card like a phone key, then talks to the car over Bluetooth LE. No Tesla cloud account is needed. The Fleet API is optional, for out of range.

What it does: status and slide switches (lock, sentry, port, frunk/trunk, climate); one-off "Depart by" (time, charge %, cabin temp); local SQLite history (drives, charges, efficiency, battery health, learned places, yearly km-register export). Location and OpenStreetMap labels are opt-in. With the cloud on, it can fetch Supercharger invoice PDFs.

What it can't do:
- The laptop has to be within about 10 m for Bluetooth.
- Cloud use is paid per request beyond Tesla's $10/month discount. Off by default.
- Tesla does not expose the car's own trip history, so history builds only from what the app observes.

No telemetry. GPL-3.0-or-later. Built with AI assistance (Claude) under my review; in-project security review, report in the repo. Flatpak bundle and .deb on the release, not on Flathub (its policy excludes AI-assisted submissions).

Testers welcome (Model 3/Y, S/X from 2021): https://github.com/stepspower/socks/releases/tag/v0.1.0

Tesla is a trademark of Tesla, Inc.; not affiliated.

---

## 4. Hacker News

**Title** (66 chars)

Show HN: SOCKS, a Linux desktop app for Tesla over Bluetooth, no cloud

**First comment** (about 195 words)

Hi, author here. I wanted my car's charge state and my own record of drives and charges on a laptop, without a service holding the data or paying for API access to read a battery percentage.

SOCKS = State Of Charge, Keys & Stats. SoC is the battery term for state of charge, hence the sock with a lightning bolt.

Tesla's BLE protocol lets any device act as a key: you generate a key pair, the car shows a prompt, and you tap your key card to authorise the public key. After that the laptop talks to the car directly over Bluetooth LE, with signed commands. SOCKS implements no crypto; it uses Tesla's open source `tesla-fleet-api`. The private key stays in `~/.config/socks/` (0600); revoke it from the car's Locks screen.

The Fleet API is optional, for remote access; it needs a developer app and a domain, and costs money past Tesla's $10/month discount. The car's own trip history is not available via the API, so history is built only from what the app sees.

Python 3.13, PySide6-Essentials, GPL-3.0-or-later, no telemetry. It was built with AI assistance (Claude) under my review. A separate reviewer did an in-project security review; the report is in docs/, and it is not a third-party audit. The name is State Of Charge, Keys & Stats, and I'm aware.

https://github.com/stepspower/socks/releases/tag/v0.1.0

---

## 5. Mastodon / X

SOCKS (State Of Charge, Keys & Stats): open-source Linux desktop app for Tesla. Bluetooth key, no cloud account needed, local history, no telemetry. GPL-3.0+. Testers welcome: https://github.com/stepspower/socks #Linux #Tesla #OpenSource

(The trademark line does not fit in 280 characters; put "Tesla is a trademark of Tesla, Inc.; not affiliated." in a reply or the profile bio.)

## Posted (2026-10-11)

- r/teslamotors, r/linux, r/linuxapps — posted by the owner.
- TMC, Show HN, Mastodon/X — not posted (no accounts); texts kept here for later.
