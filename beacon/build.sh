#!/usr/bin/env bash
# Builds the standalone beacon (beacon.c) inside the esp32-sdr-trx firmware tree and writes release/esp32-beacon.bin: one image
# for both uses, loaded into RAM over USB (scripts/beacon_check.py) or written to flash address 0, where the ROM boots it
# directly without ESP-IDF's bootloader (beacon/flash_beacon.py). It holds RAM segments only (esptool's --ram-only-header).
# Needs the firmware tree with beacon/firmware-hooks.patch applied, ESP-IDF v5.5.x and its Xtensa toolchain.
# Settings other than the call sign can be passed through, e.g.:  beacon/build.sh BEACON_DEFS="-DBEACON_WPM=15 -DLED_GPIO=38"
set -euo pipefail
cd "$(dirname "$0")/.."
export IDF_PATH="${IDF_PATH:-S:/esp/esp-idf}"
export PATH="/s/esp/.espressif/tools/xtensa-esp-elf/esp-14.2.0_20251107/xtensa-esp-elf/bin:$PATH"
PY="$PWD/.venv/Scripts/python.exe"
FW=repos/esp32-sdr-trx/firmware
grep -q ESPDR_BEACON "$FW/src/radio.c" || git -C repos/esp32-sdr-trx apply "$PWD/beacon/firmware-hooks.patch"
"$PY" beacon/gen_olivia.py         # the ID in Olivia, from BEACON_TEXT in beacon.c
rm -f "$FW/build-beacon/beacon.o"   # BEACON_DEFS or the Olivia table may have changed
make -C "$FW" BEACON=1 BEACON_SRC="$PWD/beacon/beacon.c" ESPTOOL="$PY -m esptool" "$@"
"$PY" -m esptool --chip esp32s3 elf2image --ram-only-header --flash-mode dio --flash-freq 80m \
    "$FW/build-beacon/iq-source.elf" -o release/esp32-beacon.bin
ls -l release/esp32-beacon.bin
