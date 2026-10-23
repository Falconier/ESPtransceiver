# Standalone beacon firmware (CW and Olivia)

> **You must hold an amateur licence that covers the frequency, and its rules for beacons** (unattended or automatic operation,
> identification, frequency) **before you power a board that runs this.** Once flashed, the board transmits by itself every
> time it gets power.

This firmware runs a beacon on one ESP32-S3 with no PC: plug the board into any USB power source and it beacons. Every 30 s it
sends `DE W4DQB/EXP`, in one of three modes that the BOOT button switches between:

| Mode | LED | Each 30 s period |
|---|---|---|
| **CW** (at power-up) | red | the ID in CW at 20 WPM, then a steady carrier to the end of the period (as the station's Beacon button) |
| **Olivia 16/500** | green | a 2 s lead tone, the ID in Olivia 16/500 (8.2 s), then the carrier off until the next period |
| **Both in turn** | purple | CW one period, Olivia the next |

A receiver set to **2304.350 MHz USB** hears the CW and the Olivia lead tone at 700 Hz; Olivia's 16 tones span 458–942 Hz. In
the station, pick CW or Olivia 16/500 to match.

Tested from flash on the third ESP32-S3 (COM17), decoded by board A or B:
- CW: `DE W4DQB/EXP [carrier]` three times in a row.
- Olivia: `DE W4DQB/EXP` three times in a row.
- Both in turn: the CW and the Olivia IDs each decode when the station is set to that mode (the first Olivia ID of one run was
  partly garbled; the next three were clean).

## LED and button

The board's RGB LED (WS2812) marks it as the beacon:

| LED | Meaning |
|---|---|
| blinking amber (once a second), mode colour dim in between | just powered up: 5 s hold-off before it transmits |
| white | calibrating the radio |
| cyan | tuning the transmitter (the first time) |
| **red / green / purple, bright** | **on the air** in CW / Olivia / both. In CW the LED follows the key: it flashes with the ID, then stays bright through the carrier |
| the mode's colour, dim | between transmissions (carrier off) |
| the mode's colour, blinking | paused (carrier off) |
| blinking blue | the radio could not be set up, or the settings don't fit. Press RESET to try again |

**BOOT, short press:** the next mode (CW → Olivia → both → CW). The new colour shows for half a second, then that mode starts with
a fresh ID. During the hold-off a short press picks the mode the beacon starts in.

**BOOT, held for 1.5 s:** pause; the carrier goes off. While paused, a short press changes the mode and another 1.5 s hold resumes.
A long press during the hold-off makes it start paused.

**RESET** restarts the beacon in CW (the mode is not kept across a reset or power-up).

## Files

| File | What it is |
|---|---|
| `beacon.c` | The firmware's program, with all settings at the top. It replaces the station firmware's command loop (`main.c`) |
| `firmware-hooks.patch` | Changes to the esp32-sdr-trx firmware tree: the `BEACON=1` build in its Makefile, direct keying in `radio.c`/`radio.h`, and the start-up fixes in `platform.c` that a boot from flash needs (see below) |
| `build.sh` | Applies the patch if needed, builds, and writes `release/esp32-beacon.bin`: one image for RAM loading and for flash address 0 |
| `gen_olivia.py` | Encodes `BEACON_TEXT` in Olivia 16/500 with the station's `olivia.py` and writes `olivia_symbols.h` (run by `build.sh`) |
| `olivia_symbols.h` | The generated Olivia symbols (256 tones, in instruction RAM: the data region has no room left) |
| `flash_beacon.py` | Writes the beacon to flash address 0 of a board, after asking |
| `../scripts/beacon_check.py` | Test without touching the flash: loads the beacon into one board's RAM and decodes it with another board (`... 100 - olivia-16/500` listens in Olivia) |

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
- **Olivia:** the symbols are encoded on the PC at build time, so the firmware only plays tones: 256 records (32 ms) per symbol, a
  steady level, and the tones phase-continuous. The records carry the rounding remainder forward, so each tone's average
  frequency is exact (31.25 Hz spacing on a 28.6 Hz record grid), as the station's `olivia.tx_records()` does. The carrier goes off
  between Olivia IDs. In the "both" mode it also goes off for 4 s before each Olivia ID: an Olivia receiver locks onto a *new*
  steady tone, and the lead tone would otherwise continue the CW carrier at the same frequency.
- **No session limit:** the PC-driven transmitter stops after 600 s, but the beacon does not. Expect the frequency to drift by
  kHz while the chip warms up after power-on. The station's receiver follows that drift.
- **Boot from flash:** the image holds RAM segments only and sits at flash address 0, where the ROM loads it directly, without
  ESP-IDF's second-stage bootloader. The upstream firmware was written for loading into RAM through download mode, which leaves
  the chip prepared in ways a flash boot does not. The firmware now does that set-up itself (in `platform.c`, part of `firmware-hooks.patch`, except the button):
  - **Watchdogs:** a flash boot arms the timer-group and RTC watchdogs in *flash-boot mode*, which clearing their enable bit does
    not stop. Without clearing that mode too, the chip reset itself after about 0.3 s, over and over.
  - **Clocks:** the analog control bus to the BBPLL, the crystal frequency and the 8 MHz RC clock are set as ESP-IDF's
    `rtc_clk_init()` sets them. Without that, the CPU did not reach 240 MHz after a power-up.
  - **BOOT button (beacon.c):** GPIO0 is also RTC pad 0. After a reset over USB (as at the end of `flash_beacon.py`) its digital
    input stayed cut off until a power cycle. `button_init()` routes the pad to the digital side and clears its pad hold.
  - **Baseband control bus:** `RTC_CNTL_BB_I2C_FORCE_PU` stays on, as download mode leaves it. Every PHY, PLL and transmitter
    register write goes over that bus.

## Change the settings

Edit the `#define`s at the top of `beacon.c`. **Put your own call sign in `BEACON_TEXT`;** `build.sh` encodes it for Olivia too.
You can also pass number settings to
the build:

```bash
beacon/build.sh BEACON_DEFS="-DBEACON_WPM=15 -DBEACON_PERIOD_S=60 -DBEACON_DIAL_HZ=2394250000u"
```

| Setting | Default | What it does |
|---|---|---|
| `BEACON_TEXT` | `"DE W4DQB/EXP"` | the ID (letters, digits, `/ ? . , =`) |
| `BEACON_DIAL_HZ` | `2304350000u` | receiver dial (USB), 2300.005–2449.995 MHz |
| `BEACON_TONE_HZ` | `700` | how far above the dial the carrier sits |
| `BEACON_WPM` | `20` | speed (5–40) |
| `BEACON_PERIOD_S` | `30` | one ID per period. The ID plus 2 s must fit, or the LED blinks blue |
| `BEACON_SPACE_HZ` | `12000` | carrier shift during key-up (`0` keeps it on frequency, only 18 dB down) |
| `BEACON_PEAK_CODE` | `64` | key-down gain code, 64 (strongest) to 127 (17.9 dB weaker) |
| `BEACON_HOLDOFF_S` | `5` | seconds between power-up and the first transmission |
| `BEACON_MODE` | `0` | mode at power-up: `0` CW, `1` Olivia 16/500, `2` both in turn |
| `BEACON_OLIVIA_LEAD_S` | `2` | steady tone before the Olivia symbols (the receiver's AFC locks on it) |
| `BEACON_BUTTON_TEST` | (unset) | diagnostic build: instead of beaconing, the LED is white while BOOT reads pressed, dim blue otherwise (`2`: after the radio's calibration, `3`: with the carrier keyed) |
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

## Receiving it from a distance

Tested with the beacon about 30 m (100 ft) away, through two walls, in a tree: one of the station boards picked it up about 20 dB
above the noise when averaged over a second (about 17 dB in the station's 65 ms frames). Settings that matter:

- **No Wi-Fi next to the receiving board.** An ESP32 running a Wi-Fi access point (here the C5 with ESP-WebSDR) beside the board
  overloads its receiver, which has no filtering in front of it: with the C5 next to the boards the beacon did not show at all;
  with it unplugged it did.
- **More receive gain:** `--rx-gain 40 --rx-shift 2` (the defaults, 0 and 4, suit boards on the same desk). With 8-bit samples the
  default left the noise at about 1-2 counts, so a weak carrier was rounded away. Gain 60 and above overloads the receiver.
- **Olivia, not CW, at the limit:** at that spot the station decoded the beacon's Olivia ID (one clean `DE W4DQB/EXP` in about
  three, the rest partial), but not its CW. The CW decoder looks at about 200 Hz of audio, which leaves this signal around 6 dB
  above the noise; Olivia decodes below the noise. Filtered to +-25 Hz offline, the CW ID did stand clear by about 10 dB, so a
  narrowband CW decoder could get it; the station does not have one yet.
- **The beacon's frequency:** the boards' crystals put it up to ~13 kHz off the dial (at 2304.350 board A saw it at 2304.342); the
  station's AFC searches +-16 kHz around the dial.

```bash
.venv/Scripts/python.exe espdr_trx_tk.py --dial 2304.350 --rx-gain 40 --rx-shift 2 --call <your call sign>
```

Then pick Olivia 16/500 in the window (or CW when the beacon is close). To check from a script:
`RX_GAIN=40 RX_SHIFT=2 .venv/Scripts/python.exe scripts/beacon_check.py - COM4 130 - olivia-16/500`.

## Limits

- The image's data area is 8 KB and is nearly full (8127 bytes). A much longer ID table or new strings may not fit.
- The power and spurious emissions are not measured, only estimated (see the main README).
- Flash boot was tested on one board (16 MB quad flash, 8 MB PSRAM). The flash set-up is DIO at 80 MHz; a board with octal flash
  may need other settings.
- `flash_beacon.py` writes over the start of the flash, ESP-IDF's bootloader included. Anything else that was flashed (an
  `espdr-rx --flash` receiver, an Arduino sketch) no longer starts.
