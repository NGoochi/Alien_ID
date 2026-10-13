#!/bin/bash
# Build the release package: dist/Alien-<version>.zip with Alien.gha, the installers and instructions (installer/).
# The version comes from src/AlienInfo.cs. Builds without deploying, so Rhino may stay open.
#   bash tools/package.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=$(sed -n 's/.*Version => "\(.*\)";/\1/p' "$ROOT/src/AlienInfo.cs")
NAME="Alien-$VERSION"
OUT="$ROOT/dist/$NAME"

echo "Packaging $NAME"
(cd "$ROOT/src" && dotnet build -c Release -p:GH_Libraries= -nologo -v q | grep -E "error|Build succeeded")
rm -rf "$OUT" "$ROOT/dist/$NAME.zip"
mkdir -p "$OUT"
cp "$ROOT/src/bin/Release/net7.0/Alien.gha" "$OUT/"
cp "$ROOT/installer/"{INSTALL.md,README.txt,install-mac.command,settings.example.json} "$OUT/"
for f in install-windows.bat install-windows.ps1; do  # Windows scripts with CRLF line endings
    sed 's/$/\r/' "$ROOT/installer/$f" > "$OUT/$f"
done
chmod +x "$OUT/install-mac.command"
(cd "$ROOT/dist" && zip -qrX "$NAME.zip" "$NAME" -x "*.DS_Store")
echo "Wrote dist/$NAME.zip ($(du -h "$ROOT/dist/$NAME.zip" | cut -f1))"
(cd "$ROOT/dist" && shasum -a 256 "$NAME.zip" | tee "$NAME.zip.sha256")
