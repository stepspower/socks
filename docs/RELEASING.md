# Releasing SOCKS

Everything below is manual and needs the owner's accounts. CI never publishes to Flathub,
COPR or the AUR.

## 1. Prepare

1. Bump `__version__` in `socks/__init__.py`; update `packaging/debian/changelog`,
   the `%changelog` and `Version:` in `packaging/rpm/socks-tesla.spec`, `pkgver` in the
   PKGBUILDs, and add a `<release>` (with the real date) to the metainfo.
2. Complete the pre-release audit checklist in `docs/SECURITY.md`.
3. Refresh pins when dependencies changed: `uv lock`, then the vendored list
   (`uv export --frozen --no-dev --no-emit-project --format requirements-txt`, drop
   `pyside6-essentials` and `shiboken6`, save as `packaging/vendor/requirements.txt`),
   then the Flatpak list with `packaging/flatpak/gen-python-deps.py`.
4. `ruff check .`, `pytest -q`, `desktop-file-validate`, `appstreamcli validate`.

## 2. Tag and CI

```bash
git tag -s v0.1.0 -m "SOCKS 0.1.0" && git push origin v0.1.0
```

`release.yml` builds the sdist and wheel, the .deb (debian:trixie), the .rpm (fedora:42) and
`socks.flatpak`, then attaches them with a `SHA256SUMS` file to the GitHub release.
Check each artifact on a clean VM before announcing.

## 3. PyPI

`uv build` output is in the release artifacts; publish with `uv publish` (or a trusted
publisher) as `socks-tesla`.

## 4. Flathub

1. Put the commit of the tag in `packaging/flatpak/net.stepspower.Socks.yml`
   (`commit: <sha>` under the git source, next to `tag: v0.1.0`) and set the real release
   date in the metainfo. The manifest must build from a tag, with no `type: dir` sources.
2. Replace the placeholder screenshot URL with the tagged one; screenshots must be reachable.
3. Fork `flathub/flathub`, branch from `new-pr`, add `net.stepspower.Socks.yml`,
   `python3-deps.json` (and `gen-python-deps.py` is not needed there). Open a PR against
   `new-pr`, titled "Add net.stepspower.Socks". Reviewers will check the permissions
   (see `finish-args`: no filesystem, `--system-talk-name=org.bluez`), the metainfo, the
   app id (`net.stepspower.*` needs control of stepspower.net, verified by a file on the
   domain), and that dependencies come from sources, not prebuilt wheels: expect to be asked
   to build cryptography/aiohttp/etc. from sdists, regenerate with flatpak-pip-generator.
4. Test locally first: `flatpak-builder --user --install --force-clean build packaging/flatpak/net.stepspower.Socks.yml`
   and `flatpak run net.stepspower.Socks --fake`.
5. After merge, updates are PRs to the `flathub/net.stepspower.Socks` repository.

## 5. COPR (Fedora)

```bash
sudo dnf install copr-cli rpm-build
copr-cli create socks --chroot fedora-42-x86_64 --chroot fedora-43-x86_64 \
  --description "SOCKS for your Tesla" --enable-net on
rpmbuild -bs packaging/rpm/socks-tesla.spec     # needs the source tarball in ~/rpmbuild/SOURCES
copr-cli build socks ~/rpmbuild/SRPMS/socks-tesla-0.1.0-1.*.src.rpm
```

`--enable-net on` is required because the spec downloads the bundled Python wheels by hash at
install time. The API token comes from https://copr.fedorainfracloud.org/api/ into
`~/.config/copr`. Users then run `dnf copr enable stepspower/socks`.

## 6. AUR (Arch)

The dependency chain is `python-tesla-protocol` -> `python-tesla-fleet-api` -> `socks-tesla`;
the first two are not in the official repos or AUR, so publish all three, in that order.

```bash
# once: add your SSH public key at https://aur.archlinux.org/account
for p in python-tesla-protocol python-tesla-fleet-api socks-tesla; do
  git clone ssh://aur@aur.archlinux.org/$p.git aur-$p
  cp packaging/arch/$p/PKGBUILD aur-$p/
  (cd aur-$p && updpkgsums && makepkg --printsrcinfo > .SRCINFO && makepkg -s \
     && git add PKGBUILD .SRCINFO && git commit -m "Release 0.1.0" && git push)
done
```

The committed `.SRCINFO` files were written by hand (no makepkg here); always regenerate with
`makepkg --printsrcinfo`. `socks-tesla` uses `sha256sums=('SKIP')` until the tag exists; run
`updpkgsums`.

## Known dependency gaps (checked 2026-10-11)

| Distro | Missing or too old |
|---|---|
| Arch | `python-tesla-fleet-api`, `python-tesla-protocol` (packaged here); AUR has `python-bleak-retry-connector`, `python-aiolimiter`, `python-aiooui`, `python-bluetooth-adapters` |
| Fedora 42 | `tesla-fleet-api`, `tesla-protocol`, `bleak`, `bleak-retry-connector`, `aiooui`, `bluetooth-adapters`; `protobuf` is older than the required 6.33.5. So the rpm bundles all Python deps |
| Debian 13 | `tesla-fleet-api`, `tesla-protocol`; `python3-protobuf` is 3.21 (needs >= 6.33.5); `python3-bleak` 0.22.3 is borderline. So the deb bundles all Python deps |

The .deb and .rpm are therefore third-party packages for the GitHub release/COPR, not
candidates for the distro archives.
