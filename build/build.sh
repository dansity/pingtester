#!/usr/bin/env bash
# Build pingtester packages: RPM, DEB, and Linux standalone binary.
# Run on Linux (Fedora/Debian/etc).  For Windows EXE see build_windows.bat.
set -euo pipefail

VERSION="1.0"
HERE="$(cd "$(dirname "$0")" && pwd)"   # the build/ directory
SRC="$(dirname "$HERE")"                # project root (pingtester.py, report.py)
BUILD="$HERE/work"                      # intermediate build work
DIST="$HERE/dist"                       # final artifacts

mkdir -p "$DIST"

# ── helpers ───────────────────────────────────────────────────────────────────

need() {
    command -v "$1" &>/dev/null
}

# ── RPM ───────────────────────────────────────────────────────────────────────

build_rpm() {
    echo "▸ Building RPM..."
    need rpmbuild || { echo "  rpmbuild not found — install rpm-build"; return 1; }

    local top="$BUILD/rpm"
    mkdir -p "$top"/{SPECS,SOURCES,BUILD,BUILDROOT,RPMS,SRPMS}

    # source tarball expected by %setup
    local src="$top/SOURCES/pingtester-$VERSION"
    mkdir -p "$src"
    cp "$SRC/pingtester.py" "$src/"
    cp "$SRC/report.py"     "$src/"
    tar -czf "$top/SOURCES/pingtester-$VERSION.tar.gz" -C "$top/SOURCES" "pingtester-$VERSION"

    cat > "$top/SPECS/pingtester.spec" << SPEC
Name:           pingtester
Version:        $VERSION
Release:        1%{?dist}
Summary:        CLI network latency monitor
License:        MIT
BuildArch:      noarch
Requires:       python3
Source0:        pingtester-%{version}.tar.gz

%description
Terminal-based network latency monitor using Python curses.
Displays a live bar chart of ping times with statistics and optional CSV logging.

%prep
%setup -q

%install
mkdir -p %{buildroot}%{_datadir}/pingtester
mkdir -p %{buildroot}%{_bindir}
install -m 755 pingtester.py %{buildroot}%{_datadir}/pingtester/pingtester.py
install -m 755 report.py     %{buildroot}%{_datadir}/pingtester/report.py
printf '#!/bin/sh\nexec python3 /usr/share/pingtester/pingtester.py "\$@"\n' \
    > %{buildroot}%{_bindir}/pingtester
chmod 755 %{buildroot}%{_bindir}/pingtester

%files
%{_datadir}/pingtester/pingtester.py
%{_datadir}/pingtester/report.py
%{_bindir}/pingtester
SPEC

    rpmbuild \
        --define "_topdir $top" \
        -bb "$top/SPECS/pingtester.spec" \
        --quiet

    find "$top/RPMS" -name "*.rpm" -exec cp {} "$DIST/" \;
    echo "  RPM → $(ls "$DIST"/*.rpm | tail -1 | xargs basename)"
}

# ── DEB ───────────────────────────────────────────────────────────────────────
# .deb is an ar(1) archive: debian-binary + control.tar.gz + data.tar.gz
# We build it manually so dpkg-deb is not required.

build_deb() {
    echo "▸ Building DEB..."

    local pkg="pingtester_${VERSION}_all"
    local root="$BUILD/deb/$pkg"
    rm -rf "$root"

    # data tree — both scripts go to /usr/share/pingtester/, launcher in /usr/bin/
    mkdir -p "$root/data/usr/share/pingtester" "$root/data/usr/bin"
    cp "$SRC/pingtester.py" "$root/data/usr/share/pingtester/pingtester.py"
    cp "$SRC/report.py"     "$root/data/usr/share/pingtester/report.py"
    chmod 755 "$root/data/usr/share/pingtester/pingtester.py" \
              "$root/data/usr/share/pingtester/report.py"
    printf '#!/bin/sh\nexec python3 /usr/share/pingtester/pingtester.py "$@"\n' \
        > "$root/data/usr/bin/pingtester"
    chmod 755 "$root/data/usr/bin/pingtester"

    # control
    mkdir -p "$root/control"
    cat > "$root/control/control" << EOF
Package: pingtester
Version: $VERSION
Architecture: all
Maintainer: pingtester
Depends: python3
Priority: optional
Section: net
Description: CLI network latency monitor
 Terminal-based network latency monitor using Python curses.
 Displays a live bar chart of ping times with statistics and optional CSV logging.
EOF

    # assemble archives
    tar -czf "$root/control.tar.gz" -C "$root/control" .
    tar -czf "$root/data.tar.gz"    -C "$root/data"    .
    echo "2.0" > "$root/debian-binary"

    # pack with ar
    local out="$DIST/${pkg}.deb"
    rm -f "$out"
    ar rcs "$out" "$root/debian-binary" "$root/control.tar.gz" "$root/data.tar.gz"

    echo "  DEB → ${pkg}.deb"
}

# ── Linux standalone binary ───────────────────────────────────────────────────

build_binary() {
    echo "▸ Building Linux standalone binary..."
    if ! need pyinstaller; then
        echo "  pyinstaller not found — installing..."
        pip3 install --quiet pyinstaller
    fi

    pyinstaller \
        --onefile \
        --name "pingtester-linux-x86_64" \
        --distpath "$DIST" \
        --workpath "$BUILD/pyinstaller" \
        --specpath "$BUILD/pyinstaller" \
        --hidden-import report \
        --log-level WARN \
        "$SRC/pingtester.py"

    # report generator is bundled into the binary (invoked via --generate-report)
    echo "  BIN → pingtester-linux-x86_64 (report bundled)"
}

# ── Windows EXE (via Wine) ────────────────────────────────────────────────────

build_exe() {
    echo "▸ Building Windows EXE (via Wine)..."
    if ! need wine; then
        echo "  wine not found — skipping EXE build"
        return
    fi
    if ! WINEDEBUG=-all wine python --version &>/dev/null 2>&1; then
        echo "  Python not installed in Wine — run once manually:"
        echo "    wine <python-installer.exe> /quiet InstallAllUsers=0 PrependPath=1"
        return
    fi
    WINEDEBUG=-all wine pip install --quiet pyinstaller windows-curses 2>/dev/null

    WINEDEBUG=-all wine pyinstaller \
        --onefile \
        --name "pingtester-1.0-windows" \
        --distpath "$DIST" \
        --workpath "$BUILD/pyinstaller-win" \
        --specpath "$BUILD/pyinstaller-win" \
        --hidden-import report \
        --log-level WARN \
        "$SRC/pingtester.py" 2>/dev/null

    # report generator is bundled into the EXE (invoked via --generate-report)
    echo "  EXE → pingtester-1.0-windows.exe (report bundled)"
}

# ── main ──────────────────────────────────────────────────────────────────────

build_rpm
build_deb
build_binary
build_exe

echo ""
echo "Done. Packages in dist/:"
ls -lh "$DIST/"
