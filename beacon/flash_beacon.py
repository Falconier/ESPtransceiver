"""Writes the standalone beacon to an ESP32-S3's flash, so that the board beacons by itself at every power-up.

  python beacon/flash_beacon.py COM17            (asks before writing)

Writes release/esp32-beacon.bin to flash address 0 over the board's native USB port; the ROM boots it directly (no ESP-IDF
bootloader). Whatever program starts from the flash now is replaced. The station keeps working on that board: it loads its own
firmware into RAM and never touches the flash. To make the board quiet at power-up again, erase the flash:
  python -m esptool --chip esp32s3 --port COM17 erase-flash
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("port", help="the board's native USB port, e.g. COM17")
p.add_argument("--image", default=str(ROOT / "release" / "esp32-beacon.bin"))
p.add_argument("--yes", action="store_true", help="do not ask")
a = p.parse_args()

if not Path(a.image).exists():
    sys.exit(f"{a.image}: missing (run beacon/build.sh first)")
print(f"This overwrites the start of the flash of the board on {a.port} with the CW beacon. From then on it TRANSMITS by itself on\n"
      "every power-up (after a 5 s hold-off with the LED blinking amber; press BOOT to pause). Transmitting needs your amateur licence.")
if not a.yes and input("Continue? [y/N] ").strip().lower() != "y":
    sys.exit("not written")
sys.exit(subprocess.call([sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", a.port, "--baud", "921600",
                          "--before", "default-reset", "--after", "hard-reset", "write-flash", "--flash-mode", "dio",
                          "--flash-freq", "80m", "--flash-size", "detect", "0x0", a.image]))
