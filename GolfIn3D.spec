# pyinstaller GolfIn3D.spec  -> dist/GolfIn3D (folder app; .app bundle on macOS)
import sys
from PyInstaller.utils.hooks import collect_all

datas, binaries, hidden = collect_all("mediapipe")
datas += [("golfin3d/models", "golfin3d/models"), ("golfin3d/icon.png", "golfin3d")]
icon = {"win32": "assets/icon.ico", "darwin": "assets/icon.png"}.get(sys.platform)

a = Analysis(["golfin3d/__main__.py"], datas=datas, binaries=binaries, hiddenimports=hidden + ["sounddevice"], excludes=["tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GolfIn3D", console=False, icon=icon)
coll = COLLECT(exe, a.binaries, a.datas, name="GolfIn3D")
app = BUNDLE(coll, name="GolfIn3D.app", icon="assets/icon.png", bundle_identifier="app.golfin3d",
             version="0.1.0",
             info_plist={"NSCameraUsageDescription": "GolfIn3D records your swing from the studio cameras.",
                         "NSMicrophoneUsageDescription": "GolfIn3D listens for the sound of impact to capture swings.",
                         "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "13.0"})
