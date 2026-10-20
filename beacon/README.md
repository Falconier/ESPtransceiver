# Standalone CW beacon firmware

> **You must hold an amateur licence that covers the frequency, and its rules for beacons** (unattended or automatic operation,
> identification, frequency) **before you power a board that runs this.** Once flashed, the board transmits by itself every
> time it gets power.

This firmware runs a CW beacon on one ESP32-S3 with no PC: plug the board into any USB power source and it beacons. It sends
the same cycle as the station's Beacon button. Each 30 s period starts with `DE W4DQB/EXP` in CW at 20 WPM, and a steady carrier
fills the rest. A receiver set to **2394.250 MHz USB** hears it as a 700 Hz tone.

Tested on the third ESP32-S3 (COM17): written to flash and started by a reset, board A decoded `DE W4DQB/EXP [carrier]` three
times in a row over 100 s.

## LED and button

The board's RGB LED (WS2812) marks it as the beacon:

| LED | Meaning |
|---|---|
| blinking amber (once a second) | just powered up: 5 s hold-off before it transmits |
| purple | calibrating the radio |
| cyan | tuning the transmitter |
| **red** | **on the air.** The LED follows the key: it flashes with the ID, then stays solid red through the carrier |
| green | paused (carrier off) |
| blinking blue | the radio could not be set up, or the settings don't fit. Press RESET to try again |

**BOOT** pauses and resumes the beacon. Pressing BOOT during the amber hold-off makes it start paused. **RESET** restarts it.

## Files

| File | What it is |
|---|---|
| `beacon.c` | The firmware's program, with all settings at the top. It replaces the station firmware's command loop (`main.c`) |
| `firmware-hooks.patch` | Changes to the esp32-sdr-trx firmware tree: the `BEACON=1` build in its Makefile, direct keying in `radio.c`/`radio.h`, and the start-up fixes in `platform.c` that a boot from flash needs (see below) |
| `build.sh` | Applies the patch if needed, builds, and writes `release/esp32-beacon.bin`: one image for RAM loading and for flash address 0 |
| `flash_beacon.py` | Writes the beacon to flash address 0 of a board, after asking |
| `../scripts/beacon_check.py` | Test without touching the flash: loads the beacon into one board's RAM and decodes it with another board |

## How it works

`beacon.c` uses the same transmitter as the station. The difference is that the 8000 updates per second come from the chip
itself, not over USB.

- **Start:** it calibrates the radio and picks a PLL setting near the dial (moving the LO by up to ~15 kHz, as the host does). The
  carrier then sits at dial + 700 Hz.
- **Keying:** each key edge takes 5 ms with a raised-cosine shape, made by stepping the gain code through the measured gain curve.
  Key-down uses gain code 64 (strongest, roughly −17 dBm EIRP).
- **Key-up:** the chip can only turn the carrier down by ~18 dB, so between elements the weak carrier also moves 12 kHz away (the
  station's "Shift carrier in spaces").
- **Timing:** standard PARIS timing, the same as the station's Morse generator. At 20 WPM the ID takes 8.1 s and the carrier 21.9 s.
- **No session limit:** the PC-driven transmitter stops after 600 s, but the beacon does not. Expect the frequency to drift by
  kHz while the chip warms up after power-on. The station's receiver follows that drift.
- **Boot from flash:** the image holds RAM segments only and sits at flash address 0, where the ROM loads it directly, without
  ESP-IDF's second-stage bootloader. The upstream firmware was written for loading into RAM through download mode, which leaves
  the chip prepared in ways a flash boot does not. `platform.c` now does that set-up itself (all three in `firmware-hooks.patch`):
  - **Watchdogs:** a flash boot arms the timer-group and RTC watchdogs in *flash-boot mode*, which clearing their enable bit does
    not stop. Without clearing that mode too, the chip reset itself after about 0.3 s, over and over.
  - **Clocks:** the analog control bus to the BBPLL, the crystal frequency and the 8 MHz RC clock are set as ESP-IDF's
    `rtc_clk_init()` sets them. Without that, the CPU did not reach 240 MHz after a power-up.
  - **Baseband control bus:** `RTC_CNTL_BB_I2C_FORCE_PU` stays on, as download mode leaves it. Every PHY, PLL and transmitter
    register write goes over that bus.

## Change the settings

Edit the `#define`s at the top of `beacon.c`. **Put your own call sign in `BEACON_TEXT`.** You can also pass number settings to
the build:

```bash
beacon/build.sh BEACON_DEFS="-DBEACON_WPM=15 -DBEACON_PERIOD_S=60 -DBEACON_DIAL_HZ=2394250000u"
```

| Setting | Default | What it does |
|---|---|---|
| `BEACON_TEXT` | `"DE W4DQB/EXP"` | the ID (letters, digits, `/ ? . , =`) |
| `BEACON_DIAL_HZ` | `2394250000u` | receiver dial (USB), 2320.005–2449.995 MHz |
| `BEACON_TONE_HZ` | `700` | how far above the dial the carrier sits |
| `BEACON_WPM` | `20` | speed (5–40) |
| `BEACON_PERIOD_S` | `30` | one ID per period. The ID plus 2 s must fit, or the LED blinks blue |
| `BEACON_SPACE_HZ` | `12000` | carrier shift during key-up (`0` keeps it on frequency, only 18 dB down) |
| `BEACON_PEAK_CODE` | `64` | key-down gain code, 64 (strongest) to 127 (17.9 dB weaker) |
| `BEACON_HOLDOFF_S` | `5` | seconds between power-up and the first transmission |
| `LED_GPIO` | `48` | LED data pin: 48 on ESP32-S3-DevKitC-1 v1.0, **38 on v1.1** |
| `LED_LEVEL` | `40` | LED brightness, 0–255 |

## Build, test, flash

These commands use ESP-IDF v5.5.3 and the toolchain set up in [SETUP-CHECKLIST.md](../SETUP-CHECKLIST.md), and run in Git Bash
from the project folder.

```bash
beacon/build.sh
```

Test in RAM first; this doesn't touch the flash. The beacon goes on the board at COM17 and board A (COM4) decodes it for 80 s:

```bash
.venv/Scripts/python.exe scripts/beacon_check.py COM17 COM4 80
```

Then write it to the flash so it starts at every power-up. This replaces the program the board starts from flash:

```bash
.venv/Scripts/python.exe beacon/flash_beacon.py COM17
```

To check a flashed beacon, only listen (`-` instead of a port to load):

```bash
.venv/Scripts/python.exe scripts/beacon_check.py - COM4 100
```

The station still works on a flashed board, because it loads its own firmware into RAM and leaves the flash alone. After a reset or
power cycle the board beacons again. To make it quiet at power-up, erase the flash:

```bash
.venv/Scripts/python.exe -m esptool --chip esp32s3 --port COM17 erase-flash
```

## Limits

- The image's data area is 8 KB and is nearly full (8127 bytes). A much longer ID table or new strings may not fit.
- The power and spurious emissions are not measured, only estimated (see the main README).
- Flash boot was tested on one board (16 MB quad flash, 8 MB PSRAM). The flash set-up is DIO at 80 MHz; a board with octal flash
  may need other settings.
- `flash_beacon.py` writes over the start of the flash, ESP-IDF's bootloader included. Anything else that was flashed (an
  `espdr-rx --flash` receiver, an Arduino sketch) no longer starts.
