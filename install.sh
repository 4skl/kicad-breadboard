#!/usr/bin/env bash
# Breadboard Builder — KiCad plugin installer
# Works on Linux and macOS with KiCad 9 or 10.

set -e

PLUGIN_NAME="breadboard"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/plugins/$PLUGIN_NAME"

# Determine OS and candidate plugin directories (ordered: newest first)
if [[ "$OSTYPE" == darwin* ]]; then
    CANDIDATES=(
        "$HOME/Library/Preferences/kicad/10.0/scripting/plugins"
        "$HOME/Library/Preferences/kicad/9.0/scripting/plugins"
    )
else
    CANDIDATES=(
        "$HOME/.config/kicad/10.0/scripting/plugins"
        "$HOME/.local/share/kicad/9.0/scripting/plugins"
    )
fi

# Pick the first candidate whose parent kicad/<version> directory exists
TARGET=""
for dir in "${CANDIDATES[@]}"; do
    version_dir="$(dirname "$(dirname "$dir")")"
    if [[ -d "$version_dir" ]]; then
        TARGET="$dir"
        break
    fi
done

if [[ -z "$TARGET" ]]; then
    echo ""
    echo "ERROR: Could not find a KiCad installation directory."
    echo "Please install KiCad 9 or 10 first, then re-run this script."
    echo ""
    echo "If KiCad is installed but not found, copy the plugins/breadboard/"
    echo "folder manually into KiCad's scripting/plugins/ directory."
    echo "(KiCad → Preferences → Configure Paths… shows the exact path.)"
    exit 1
fi

DEST="$TARGET/$PLUGIN_NAME"

echo ""
echo "Breadboard Builder — installer"
echo "=============================="
echo "Source : $SOURCE"
echo "Target : $DEST"
echo ""

# Remove stale install if present
if [[ -e "$DEST" || -L "$DEST" ]]; then
    echo "Removing existing installation…"
    rm -rf "$DEST"
fi

# Create target directory if needed
mkdir -p "$TARGET"

# Try symlink first; fall back to copy (some network/FAT file systems)
if ln -s "$SOURCE" "$DEST" 2>/dev/null; then
    echo "Symlink created."
else
    echo "Could not create symlink — copying instead…"
    cp -r "$SOURCE" "$DEST"
    echo "Files copied."
fi

echo ""
echo "Done! Next steps:"
echo "  1. Open KiCad and open your project in the PCB Editor."
echo "  2. In the menu: Tools → External Plugins → Refresh Plugins."
echo "  3. A breadboard icon will appear in the right-hand toolbar."
echo ""
