Name:           socks-tesla
Version:        0.1.1
Release:        1%{?dist}
Summary:        Tesla companion for Linux: Bluetooth control, charge status and history
License:        GPL-3.0-or-later
URL:            https://github.com/stepspower/socks
Source0:        %{url}/archive/refs/tags/v%{version}/socks-%{version}.tar.gz

# Bundled Python dependencies (tesla-fleet-api, bleak, protobuf >= 6.33.5, ...) are fetched
# from PyPI by hash at %%install time: most are not packaged in Fedora. Build with network
# access (GitHub release CI or COPR with `enable_net on`), not in the Fedora archive.
BuildRequires:  python3-devel >= 3.13
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-pip
BuildRequires:  python3-setuptools
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
BuildRequires:  appstream

Requires:       python(abi) = 3.13
Requires:       python3-pyside6
Requires:       bluez
Requires:       hicolor-icon-theme

# Prebuilt wheels carry their own compiled libraries.
%global __requires_exclude_from ^%{_prefix}/lib/socks-tesla/.*$
%global __provides_exclude_from ^%{_prefix}/lib/socks-tesla/.*$
%global _debugsource_packages 0
%global debug_package %{nil}

%description
SOCKS (State Of Charge, Keys and Stats) talks to a Tesla over Bluetooth LE, optionally
through the Tesla Fleet API, shows charge status and range, sends everyday commands behind a
confirmation gate, and keeps a local history of distance driven, charging sessions,
efficiency and battery health. No telemetry.

Tesla is a trademark of Tesla, Inc. This project is not affiliated with it.

%prep
%autosetup -n socks-%{version}
%if 0%{?fedora} && 0%{?fedora} < 43
# Fedora 42 ships setuptools 74, which predates PEP 639 (SPDX license string, license-files).
sed -i -e 's/setuptools>=77/setuptools>=61/' \
       -e 's/^license = "GPL-3.0-or-later"/license = { text = "GPL-3.0-or-later" }/' \
       -e '/^license-files/d' pyproject.toml
%endif

%generate_buildrequires
# -R: the runtime requirements (tesla-fleet-api, PySide6-Essentials, platformdirs) are not
# build dependencies: they are vendored (all but PySide6) or provided by python3-pyside6.
%pyproject_buildrequires -R

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files socks
rm -f %{buildroot}%{_bindir}/socks %{buildroot}%{_bindir}/socks-cli

%{python3} -m pip install --no-deps --require-hashes --only-binary=:all: \
    --disable-pip-version-check --target %{buildroot}%{_prefix}/lib/socks-tesla/vendor \
    -r packaging/vendor/requirements.txt
rm -rf %{buildroot}%{_prefix}/lib/socks-tesla/vendor/bin

install -d %{buildroot}%{_bindir}
sed 's#@VENDOR@#%{_prefix}/lib/socks-tesla/vendor#' packaging/wrapper/socks > %{buildroot}%{_bindir}/socks
sed 's#@VENDOR@#%{_prefix}/lib/socks-tesla/vendor#' packaging/wrapper/socks-cli > %{buildroot}%{_bindir}/socks-cli
chmod 0755 %{buildroot}%{_bindir}/socks %{buildroot}%{_bindir}/socks-cli

install -Dm644 packaging/net.stepspower.Socks.desktop -t %{buildroot}%{_datadir}/applications
install -Dm644 packaging/net.stepspower.Socks.metainfo.xml -t %{buildroot}%{_metainfodir}
install -Dm644 socks/gui/icons/socks.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/net.stepspower.Socks.svg
install -Dm644 socks/gui/icons/socks-symbolic.svg \
    %{buildroot}%{_datadir}/icons/hicolor/symbolic/apps/net.stepspower.Socks-symbolic.svg
mkdir -p %{buildroot}%{_datadir}/icons/hicolor
cp -r data/icons/hicolor/. %{buildroot}%{_datadir}/icons/hicolor/

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/net.stepspower.Socks.desktop
appstreamcli validate --no-net %{buildroot}%{_metainfodir}/net.stepspower.Socks.metainfo.xml

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/socks
%{_bindir}/socks-cli
%{_prefix}/lib/socks-tesla/
%{_datadir}/applications/net.stepspower.Socks.desktop
%{_metainfodir}/net.stepspower.Socks.metainfo.xml
%{_datadir}/icons/hicolor/*/apps/net.stepspower.Socks*

%changelog
* Sun Oct 11 2026 stepspower <security@stepspower.net> - 0.1.1-1
- Bluetooth: re-scan for the car after BlueZ drops the device following an idle disconnect

* Sun Oct 11 2026 stepspower <security@stepspower.net> - 0.1.0-1
- Initial package
