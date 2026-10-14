#!/bin/bash
# Build the release downloads into dist/ (the version comes from src/AlienInfo.cs):
#   Alien-<version>-Setup.exe   Windows installer (NSIS: brew install makensis)
#   Alien-<version>.pkg         macOS installer (pkgbuild + productbuild, part of macOS)
#   Alien-<version>.zip         manual install: Alien.gha, INSTALL.md and the install scripts
#   Alien-<version>.sha256      checksums of the three
# Builds without deploying, so Rhino may stay open. Artwork is drawn from docs/brand/alien-mark.svg.
#   bash tools/package.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=$(sed -n 's/.*Version => "\(.*\)";/\1/p' "$ROOT/src/AlienInfo.cs")
NUMERIC=$(sed -n 's/.*<Version>\(.*\)<\/Version>.*/\1/p' "$ROOT/src/Alien.csproj")
NAME="Alien-$VERSION"
DIST="$ROOT/dist"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Packaging $NAME"
(cd "$ROOT/src" && dotnet build -c Release -p:GH_Libraries= -nologo -v q | grep -E "error|Build succeeded")
GHA="$ROOT/src/bin/Release/net7.0/Alien.gha"
mkdir -p "$DIST"
rm -rf "$DIST/$NAME" "$DIST/$NAME".* "$DIST/$NAME-Setup.exe"

# ── artwork ──
ART="$WORK/art"
swift "$ROOT/installer/art/render.swift" "$ROOT" "$ART" 2>/dev/null
python3 "$ROOT/installer/art/make_ico.py" "$ART/alien.ico" "$ART"/icon-{16,24,32,48,64,128,256}.png >/dev/null

# ── Windows: Setup.exe ──
if command -v makensis >/dev/null; then
    SIZEKB=$(( $(stat -f%z "$GHA") / 1024 ))
    # makensis dies with std::bad_alloc when the shell has no locale set
    LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 makensis -V2 -DVERSION="$VERSION" -DVIVERSION="$NUMERIC.0" -DGHA="$GHA" -DART="$ART" \
        -DSRC="$ROOT/installer/windows" -DOUT="$DIST/$NAME-Setup.exe" -DSIZEKB="$SIZEKB" \
        "$ROOT/installer/windows/Alien.nsi"
    echo "Wrote dist/$NAME-Setup.exe ($(du -h "$DIST/$NAME-Setup.exe" | cut -f1))"
else
    echo "Skipped the Windows installer: makensis not found (brew install makensis)" >&2
fi

# ── macOS: .pkg (current user only; Rhino must be closed, checked by the installer) ──
LIB="Library/Application Support/McNeel/Rhinoceros/8.0/Plug-ins/Grasshopper (b45a29b1-4343-4035-989e-044e8580d9cf)/Libraries"
mkdir -p "$WORK/root/$LIB" "$WORK/pkgs" "$WORK/res"
cp "$GHA" "$WORK/root/$LIB/"
pkgbuild --quiet --root "$WORK/root" --install-location / --identifier com.alien.grasshopper --version "$NUMERIC" \
    "$WORK/pkgs/alien-core.pkg"
cp "$ROOT/installer/mac/"{welcome.html,conclusion.html} "$ART"/pkg-background{,-dark}.png "$WORK/res/"
sed -e "s/__VERSION__/$VERSION/g" -e "s/__PKGVERSION__/$NUMERIC/g" "$ROOT/installer/mac/distribution.xml" > "$WORK/distribution.xml"
productbuild --quiet --distribution "$WORK/distribution.xml" --resources "$WORK/res" --package-path "$WORK/pkgs" "$DIST/$NAME.pkg"
echo "Wrote dist/$NAME.pkg ($(du -h "$DIST/$NAME.pkg" | cut -f1))"

# ── manual zip ──
OUT="$DIST/$NAME"
mkdir -p "$OUT"
cp "$GHA" "$OUT/"
cp "$ROOT/installer/"{INSTALL.md,README.txt,install-mac.command,settings.example.json} "$OUT/"
for f in install-windows.bat install-windows.ps1; do  # Windows scripts with CRLF line endings
    sed 's/$/\r/' "$ROOT/installer/$f" > "$OUT/$f"
done
chmod +x "$OUT/install-mac.command"
(cd "$DIST" && zip -qrX "$NAME.zip" "$NAME" -x "*.DS_Store")
echo "Wrote dist/$NAME.zip ($(du -h "$DIST/$NAME.zip" | cut -f1))"

(cd "$DIST" && shasum -a 256 $(ls "$NAME-Setup.exe" "$NAME.pkg" "$NAME.zip" 2>/dev/null) | tee "$NAME.sha256")
