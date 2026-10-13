#!/bin/bash
# Alien installer for Rhino 8 on macOS: copies Alien.gha (next to this script) into Grasshopper's Libraries folder
# and clears the download quarantine flag. ALIEN_GH_LIBRARIES overrides the destination (for testing).
set -e
cd "$(dirname "$0")"
SRC="$PWD/Alien.gha"
DEST="${ALIEN_GH_LIBRARIES:-$HOME/Library/Application Support/McNeel/Rhinoceros/8.0/Plug-ins/Grasshopper (b45a29b1-4343-4035-989e-044e8580d9cf)/Libraries}"

finish() { [ -t 0 ] && read -n 1 -s -r -p "Press any key to close." && echo; exit "$1"; }  # keeps a double-clicked window open

echo "Alien installer (Rhino 8, macOS)"
if [ ! -f "$SRC" ]; then
    echo "Alien.gha is not next to this script. Unzip the whole package first."
    finish 1
fi
if pgrep -xq Rhinoceros; then
    echo "Rhino is running. Quit it (Cmd+Q) and run this again."
    finish 1
fi
mkdir -p "$DEST"
[ -f "$DEST/Alien.gha" ] && echo "Replacing the installed Alien.gha."
cp -f "$SRC" "$DEST/Alien.gha"
xattr -d com.apple.quarantine "$DEST/Alien.gha" 2>/dev/null || true  # a quarantined plugin won't load
echo "Installed: $DEST/Alien.gha"
echo "Next: start Rhino, type Grasshopper, and place an Alien node (Alien tab). See INSTALL.md."
finish 0
