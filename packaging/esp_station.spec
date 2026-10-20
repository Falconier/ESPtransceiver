# PyInstaller spec for the per-board station (espdr_trx_tk.py) as one executable: ESP-Station.exe on Windows, ESP-Station on Linux.
# Build with packaging/build.py (it runs PyInstaller with this file). PyInstaller builds for the system it runs on only, so the Linux
# executable is built on Linux (or by the GitHub Actions workflow).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "release" / "esp32-sdr-trx-trx-decim16.bin"), "release")]   # the transceiver firmware the station loads
datas += collect_data_files("esptool")      # flasher stubs and chip data: the board is loaded through esptool
datas += collect_data_files("espdr")        # the package's own firmware images and bootloader files

a = Analysis(
    [str(ROOT / "espdr_trx_tk.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=collect_submodules("espdr") + collect_submodules("esptool"),
    excludes=["matplotlib", "PIL", "IPython", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="ESP-Station",
    console=sys.platform != "win32",          # Windows: a window only; Linux: started from a terminal or a desktop file
    upx=False,
)
