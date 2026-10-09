#!/usr/bin/env bash
# Wrap the PyInstaller folder build in an AppImage. Usage: packaging/appimage.sh
set -euo pipefail
APPDIR=build/AppDir
rm -rf "$APPDIR" && mkdir -p "$APPDIR/usr/bin"
cp -r dist/GolfIn3D/. "$APPDIR/usr/bin/"
cp assets/icon.png "$APPDIR/golfin3d.png"
cat > "$APPDIR/golfin3d.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=GolfIn3D
Comment=Two-camera golf swing capture with 3D pose
Exec=GolfIn3D
Icon=golfin3d
Categories=Video;Sports;
DESK
cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/GolfIn3D" "$@"
RUN
chmod +x "$APPDIR/AppRun"
[ -x build/appimagetool ] || curl -fsSL -o build/appimagetool https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x build/appimagetool
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 build/appimagetool "$APPDIR" dist/GolfIn3D-Linux-x86_64.AppImage
