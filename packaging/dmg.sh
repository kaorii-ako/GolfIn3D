#!/usr/bin/env bash
# Package dist/GolfIn3D.app into a drag-to-Applications disk image.
set -euo pipefail
STAGE=build/dmg
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R dist/GolfIn3D.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname GolfIn3D -srcfolder "$STAGE" -ov -format UDZO dist/GolfIn3D-macOS-arm64.dmg
