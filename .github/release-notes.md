> **Transmitting needs an amateur radio licence** that covers the frequency (the station transmits only inside 2300–2450 MHz),
> and you are responsible for what you send. A board flashed with the beacon transmits by itself whenever it has power.

An ESP32-S3 used as a 13 cm (2.4 GHz) transceiver, built on [esp32-sdr-trx](https://github.com/jochenhammes/esp32-sdr-trx). It
sends and receives CW, Feld Hellschreiber and Olivia 8/250 and 16/500, and has a CW beacon. See the
[README](https://github.com/Falconier/ESPtransceiver/blob/main/README.md) for what it does and how.

## Files

| File | What it is |
|---|---|
| `ESP-Station-…-windows-x86_64.exe` | The station for Windows 10/11, one file, no Python needed |
| `ESP-Station-…-linux-x86_64` | The station for Linux x86_64 (glibc 2.35+: Ubuntu 22.04, Debian 12 or newer) |
| `70-espdr.rules` | Linux udev rules for access to the board's USB port without root |
| `esp32-beacon-….bin` | Standalone CW beacon firmware for an ESP32-S3 (ID `DE W4DQB/EXP`; build your own with your call sign, see `beacon/README.md`) |
| `SHA256SUMS` | Checksums of the files above |

## Start

1. Plug in an ESP32-S3 board by its native USB port.
2. Accept the transmitter's licence notice once: `ESP-Station --accept-licence`.
3. Run `ESP-Station --call <your call sign>` and pick the board. Run it once per board to work two boards against each other.

On Linux, first run `chmod +x ESP-Station-*-linux-x86_64`. Then install the udev rules
(`sudo cp 70-espdr.rules /etc/udev/rules.d/`) and replug the board. The monitor needs `libportaudio2`. Details are in
[packaging/README.md](https://github.com/Falconier/ESPtransceiver/blob/main/packaging/README.md).
