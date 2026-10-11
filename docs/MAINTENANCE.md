# Maintenance

SOCKS is meant to need little care. Most of it is automated; the rest is a short list.

| Cadence | Who | What |
|---|---|---|
| Weekly | Dependabot (`.github/dependabot.yml`) | PRs for minor/patch dependency updates (one grouped PR), a separate PR each for `tesla-fleet-api` and `PySide6-Essentials`, and monthly for GitHub Actions. CI runs on each PR. |
| Monthly | `maintenance.yml` opens an issue "Monthly maintenance YYYY-MM" | Newest-dependency test run, `pip-audit` and Flatpak runtime check are filled in automatically; you tick the checklist below. |
| Quarterly | Human | Translation review (when translations exist); walk through `docs/HOWTO.md` on a fresh VM or a clean Flatpak install; read the security-relevant diffs since the last quarter (`socks/core/keys.py`, `socks/fleet/`, the command gate, SQL). |
| Yearly | Human | Full security review against the checklist in `docs/SECURITY.md`; review dependency floors in `pyproject.toml`; renew the details of the Tesla developer app (contact, origin, redirect URIs, billing card). |
| Never | | Let's Encrypt for `auth.stepspower.net` is auto-renewed by Greenhost. The Tesla key pair does not expire. |

## Monthly checklist

The workflow copies this block into the issue.

<!-- checklist:start -->
- [ ] Merge the green Dependabot PRs (read the changelog of `tesla-fleet-api` and PySide6 bumps first).
- [ ] Read the Tesla Fleet API news. There is no stable changelog page (`/docs/fleet-api/changelog` returns 404); check https://developer.tesla.com/docs/fleet-api and the releases of https://github.com/teslamotors/vehicle-command and https://github.com/Teslemetry/python-tesla-fleet-api.
- [ ] Verify the prices and the $10 monthly discount against https://developer.tesla.com/docs/fleet-api/billing-and-limits; fix `README.md`, `docs/HOWTO.md`, `docs/FLEET_API_SETUP.md` and the Settings text if they changed.
- [ ] Flatpak: if the runtime check above says a newer KDE runtime exists and the PySide BaseApp has the same branch, bump `runtime-version` and `base-version`.
- [ ] Python floor: still `>=3.13`? Check the oldest Python that Debian stable, Fedora and Ubuntu LTS ship.
- [ ] Screenshots in `docs/screenshots/` still match the UI (regenerate with `socks --fake --tab N --screenshot PATH`).
- [ ] If the newest-deps run failed: pin the offending version in `pyproject.toml` and open an issue upstream.
<!-- checklist:end -->

## Releases

See `docs/RELEASING.md` (tag, CI artifacts, PyPI, Flathub, COPR, AUR).

## Incident: tesla-fleet-api breaks after a car firmware update

1. Reproduce with the fake first (`socks-cli --fake status`) to separate a SOCKS bug from a
   library or car change; then one live read by the owner.
2. Pin the last good version in `pyproject.toml` (`tesla-fleet-api==X.Y.Z`), `uv lock`, release a
   patch version. Refresh the Flatpak and vendored pins (`docs/RELEASING.md`, step 1).
3. Open an issue upstream (https://github.com/Teslemetry/python-tesla-fleet-api/issues) with the
   firmware version, the error and a redacted log (no keys, tokens or VIN).
4. Remove the pin once a fixed release is out.
