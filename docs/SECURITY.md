# Security

## Reporting a vulnerability

Email **security@stepspower.net**. Please do not open a public issue for security problems.
Include version, steps to reproduce and impact. Expect an acknowledgement within a few days.

## Threat model (summary)

Assets: the vehicle key (can unlock and drive the car), Fleet API tokens (remote access to
the car and its location), and the history database (personal movement and charging data).

In scope:
- Other local users or processes reading keys, tokens or the database.
- Malicious or malformed data from the car or from Tesla endpoints.
- A local web page or process abusing the OAuth loopback redirect.
- Accidental or tricked execution of a dangerous command.
- Secrets leaking through logs, exceptions, the database or crash reports.
- Vulnerable dependencies.

Out of scope: an attacker with code execution as your user (they can read your files anyway),
a compromised Tesla account or car, and physical access to an unlocked session.

## Key and token storage

- Everything lives under `$XDG_CONFIG_HOME/socks/`; the directory is mode 0700, files 0600.
- Private key and tokens are never logged, never included in exception messages, never written
  to the database. Public key material may be shown; the private key is never displayed.
- No system keyring is used (the Flatpak does not request `org.freedesktop.secrets`). The
  protection is file permissions plus full-disk encryption of the user's machine.

## Network

- No telemetry and no update checks. The only network use is the Tesla endpoints the user
  enabled; cloud access is off by default.
- OAuth uses the authorization code flow with PKCE, a loopback redirect bound to 127.0.0.1 on
  a random port, and a random `state` that is verified.
- Bluetooth commands use Tesla's signed vehicle-command protocol via `tesla-fleet-api`; SOCKS
  does not implement its own crypto.

## Command confirmation gate

Every command that changes the car goes through one `confirm()` function in the GUI. `unlock`,
open trunk/frunk and remote start additionally require typing the first letters of the action.
The CLI requires an explicit flag for dangerous commands.

## Data

All SQL is parameterised. The history store holds derived numbers (state of charge, odometer,
temperatures), not raw API payloads, tokens or keys.

## Pre-release audit checklist

- [ ] `pip-audit` clean; dependency versions reviewed; lockfile or Flatpak pins refreshed.
- [ ] `bandit -r socks -ll` clean; `ruff` with the `S` rules clean.
- [ ] Grep that no code path logs or formats a key, token or auth header (including `repr`
      of config/settings objects and exception chains).
- [ ] File modes verified on fresh install: config dir 0700, key and token files 0600, created
      with the right mode atomically (no window of wider permissions).
- [ ] OAuth: PKCE verifier and state checked, redirect bound to 127.0.0.1, listener closes after
      one use or timeout, no open redirect, token refresh failures do not leak responses.
- [ ] Command gate: no code path sends a state-changing command without `confirm()`; typed
      confirmation on unlock, trunk/frunk, remote start.
- [ ] Every SQL statement uses parameters; no string-built queries.
- [ ] Input from the car/cloud is validated before use (types, ranges, units).
- [ ] No network calls except enabled Tesla endpoints (check with the cloud toggle off).
- [ ] Flatpak permissions match the manifest list and nothing more.
- [ ] Release artifacts built from a clean tag; no keys, tokens or databases in the repo or
      sdist (`git ls-files`, inspect the built wheel).

## `socks-cli fleet charging --after-download` (FLEET)

The `--after-download CMD` option executes a command the user typed (or configured) themselves,
once, after new invoice PDFs were saved. It is split with `shlex.split` and run with
`subprocess.run(argv + files, shell=False)` - no shell, so file names (which come from
server-provided site names, slugged to `[a-z0-9-]`) cannot inject commands. SOCKS never runs a
command it received from the network, and the GUI does not expose this option.
