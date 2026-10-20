"""Builds the station as a single executable for the system this runs on: dist/ESP-Station.exe (Windows) or dist/ESP-Station (Linux).

  python packaging/build.py

Needs the project's Python environment with the patched espdr package (see SETUP-CHECKLIST.md / espdr-windows.patch) and
PyInstaller (`pip install pyinstaller`). PyInstaller cannot cross-build: run this on each system, or let the GitHub Actions workflow
(.github/workflows/build.yml) build both.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
dist = ROOT / "dist"
rc = subprocess.call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                      "--distpath", str(dist), "--workpath", str(ROOT / "packaging" / "work"),
                      str(ROOT / "packaging" / "esp_station.spec")])
if rc:
    sys.exit(rc)
if sys.platform.startswith("linux"):
    shutil.copy(ROOT / "release" / "70-espdr.rules", dist)    # udev rules: native USB port access without root
exe = dist / ("ESP-Station.exe" if sys.platform == "win32" else "ESP-Station")
print(f"built {exe} ({exe.stat().st_size / 1e6:.1f} MB)")
