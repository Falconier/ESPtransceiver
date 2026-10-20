# ESPtransceiver

> [!WARNING]
> **You must hold an amateur radio licence to transmit with this project.** The licence has to cover the frequency you use
> (the 13 cm band, 2320–2450 MHz in this setup), the mode (CW), and your licence conditions on power and identification.
> Identify with your own call sign; the software does not do it for you. The transmitter is a Wi-Fi chip's test tone: its power
> is not calibrated (microwatts at a dev board's antenna), its harmonics and spurious emissions have not been measured, and its
> frequency drifts by kilohertz. Do not add an amplifier or a gain antenna without measuring what comes out. Receiving needs no
> licence.

**A Morse code (CW) transceiver built from two ESP32-S3 dev boards, with no radio hardware besides the chips' own Wi-Fi radios.**
One board transmits, the other receives, and a small Windows program decodes, sends and lets you listen.

This is experimental, proof-of-concept work: one pair of boards on one Windows 11 PC, tested in the 13 cm amateur band
(default 2394.250 MHz).

## Objective

The ESP32-S3's 2.4 GHz Wi-Fi radio has undocumented paths that make it usable as a software-defined radio:
[eSpDR](https://github.com/h0m3us3r/eSpDR) found its raw I/Q receive stream, and
[esp32-sdr-trx](https://github.com/jochenhammes/esp32-sdr-trx) built a receiver (an rtl_tcp server for SDR++) and an FM/SSB
voice transmitter on it ([eSpDR issue #3](https://github.com/h0m3us3r/eSpDR/issues/3)). Both are Linux tools.

This project set out to reproduce that on Windows and turn it into a usable **CW station**: type text, have one board send it
in Morse, have the other board receive it, decode it on screen and play it through the speakers.

## What works

- **Both boards send and receive** (half duplex, one window per board): A→B and B→A both decoded `VVV DE <call>` on the air.
- **Feld Hellschreiber** as a second mode: text sent as a dot-matrix picture and shown as a scrolling image that you read by eye
  (`CQ DE <call>` read clearly both ways on the air).
- **Olivia 8/250 and 16/500** (multi-tone FSK with strong error correction): `CQ DE <call>` decoded both ways in both modes on the air.
- **CW beacon:** a steady carrier with `DE <call>` in CW every 30 s at 20 WPM (both adjustable), decoded live by the other board.
- **Standalone beacon firmware** (`beacon/`): the same CW beacon running on a board by itself, with no PC, and the RGB LED lit red
  while it transmits. Written to flash it starts by itself at power-up; board A decoded `DE W4DQB/EXP [carrier]` from it three
  times in a row (see beacon/README.md).
- **AFC for long carriers:** the receiver now follows a steady carrier in the plain spectrum (only a comparable or stronger signal
  takes it over, not a CW space) and searches +-16 kHz, so a beacon
  that has warmed up (drifting only ~1 Hz/s) and boards whose crystals differ by ~12 kHz still decode.
- A Tk window with a frequency selector, RX/TX indicator, receive/decode and transmit/encode sections, and a speaker monitor
  with volume.
- Receiving without sample loss on Windows, using a custom receiver firmware (see below).
- The frequency selector retunes both boards while the program runs.

Known limits: the boards' frequencies drift by several kHz (the receiver follows it with an AFC); the first character after a
retune can decode wrongly; tested with one pair of boards only.

## How it works

```
 typed text ─▶ Morse ─▶ CW keyer ─▶ board B (ESP32-S3) ════ 2.4 GHz ════▶ board A (ESP32-S3) ─▶ USB ─▶ AFC ─▶ USB demodulator ─┬─▶ CW decoder ─▶ window
               (8 kHz)  records 8000/s  test tone, steered                      16 Msps, decimated                              └─▶ monitor (speakers)
                                        in frequency and gain                    to 62.5 ksps on the chip
```

### Transmit (board B)

The ESP32-S3 has no I/Q transmit input that this project uses. The esp32-sdr-trx transmitter firmware instead steers the PHY's
**test tone**: every record sent over USB sets the carrier's frequency offset (in 28.6 Hz steps) and its gain code
(0.28 dB steps over 18 dB). The CW keyer (`espdr_fldigi.CwKeyer`) turns Morse audio into those records:

- **Key down:** full gain at *dial + tone* (700 Hz by default), like the carrier of an SSB rig sending CW.
- **Key up:** the chip cannot switch its carrier off inside a transmission; the weakest gain is only 18 dB down. So during
  spaces the carrier is also moved 12 kHz away (`--space-shift`), out of the receiver's passband. This is a weak emission
  inside the band; between transmissions the carrier is off.
- **Lead tone:** each message starts with a 2 s steady carrier, so the receiver's AFC and decoder lock on before the first
  character.
- Records go out at **8000 per second**. The esp32-sdr-trx default (40000) runs out of buffer within seconds on Windows.

### Receive (board A)

The chip samples its Wi-Fi receiver at 16 Msps and decimates on chip. Stock firmware sends 250 ksps of 8-bit I/Q (0.5 MB/s), but
**Windows' USB serial driver only carries about 0.275 MB/s** from the ESP32-S3 (Linux: about 0.87 MB/s), so more than half of the
samples were lost. This project adds a **decimation-16 mode (62.5 ksps, about 0.14 MB/s)** to the receiver firmware: no
samples are lost, and ±15 kHz is plenty for CW.

On the PC (`espdr_fldigi.py`):

- `espdr-rx` (from esp32-sdr-trx) serves the stream as rtl_tcp; the program connects to it.
- **AFC:** the boards drift by up to hundreds of Hz per second while transmitting. The AFC finds the strongest new carrier
  within ±8 kHz (it ignores the receiver's fixed spurs and anything 12 dB weaker than the recent peak, such as the shifted
  spaces) and keeps it at a 700 Hz audio tone.
- **USB demodulator:** 48 kHz audio, 200–2800 Hz, with a slow AGC.

### Feld Hellschreiber (`hell.py`)

Feld Hell keys the same carrier on and off, but sends a picture of the text: each character is a 5×7 dot-matrix glyph in 7
columns, sent column by column from the bottom up, 2.5 characters per second (a column every 57.14 ms, 14 elements of 4.08 ms).
The transmitter uses a 1 ms envelope window instead of CW's 5 ms so the short elements stay sharp. The receiver measures the tone
in 28 slots per column and draws each column; the window shows two stacked copies, as on any Hell display. The chip's 18 dB key-up
shows as light grey and reads fine, so for Hell the space shift can stay off.

A receiver does not know where the sender's columns begin, so letters can come out cut at an arbitrary height (column phase).
**Auto phase** fixes that for this project's transmissions: when a steady carrier of at least 1 s ends (the lead tone), the columns
are re-aligned so the first one starts 0.5 s later, as the transmitter sends it. The **Phase** slider shifts the whole image by hand,
for signals without a lead tone.

### Olivia (`olivia.py`, `olivia_rx.py`)

Olivia follows Pawel Jalocha's design as implemented in fldigi (reimplemented here): each 7-bit character becomes 64 chips through a
Walsh-Hadamard transform, the chips are scrambled and spread over 64 symbols, and each symbol is a Gray-coded tone. 8/250 uses 8 tones
in 250 Hz and 16/500 16 tones in 500 Hz, both 31.25 Hz apart with 32 ms symbols.

- **Transmit:** one tone per symbol, phase-continuous, after a 2 s lead tone. The chip's frequency records move in 28.6 Hz steps, so
  the rounding error is carried from record to record: every tone's average frequency is exact (within 0.1 Hz).
- **Receive:** the AFC locks on the lead tone, measures its drift (the boards drift ~110 Hz/s while sending) and follows that line
  through the message instead of chasing the tones. The Olivia receiver works on the complex baseband (room for kHz of drift),
  finds the tone comb, tracks what drift is left, and decodes each 64-symbol block at the timing and tone alignment that decode best.
  It needs no lead tone itself, so it can also receive other stations.

### Decode (`cw_decoder.py`)

The decoder measures the level at the 700 Hz tone every 5 ms, sets its threshold between a peak tracker and the noise floor, and
times marks and spaces against a dot length that follows the sending speed. It bridges short drop-outs inside a mark and shows a
long steady carrier (lead or tune tone) as `[carrier]` instead of decoding it. It replaced fldigi, which in this setup garbled
even a perfect synthetic signal.

## Hardware

- 2 × ESP32-S3 dev boards (tested: ESP32-S3 QFN56 rev v0.2, 8 MB PSRAM, 40 MHz crystal), each with its native USB port connected.
  Board A receives (COM4 here), board B transmits (COM5 here).
- A Windows PC. Optional: a second receiver to watch your signal (an RTL-SDR does **not** reach 2.4 GHz; a HackRF, PlutoSDR or
  an ESP32 running ESP-WebSDR does).

## Setup (Windows)

1. **Python 3.10+** and a virtual environment with the esp32-sdr-trx wheel (in `release/`, v1.0.0-rc4), plus `scipy` only if you
   rebuild filters:
   ```powershell
   py -3.13 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install ".\release\esp32_sdr_trx-1.0.0rc4-py3-none-any.whl[audio]"
   ```
2. **Windows fixes** to the installed `espdr` package (`espdr-windows.patch`): keep DTR/RTS low so that closing the port does not
   reset the chip, let `--native` work with two boards attached, and add `--decim 16`. Apply it to
   `.venv\Lib\site-packages\espdr\` (re-apply after reinstalling the wheel).
3. **Receiver firmware:** `release/esp32-sdr-trx-rx-decim16.bin` (built from esp32-sdr-trx v1.0.0-rc4 plus
   `decim16-firmware.patch` with ESP-IDF v5.5.3). It is loaded into the board's RAM at start; nothing is written to flash.
   The transmitter uses the firmware inside the wheel.
4. **Licence confirmation**, once: `.\.venv\Scripts\espdr-tx.exe --accept-licence --dry-run -f 2394.25 -m fm --test-tone 700`
   (read the notice it prints).

`SETUP-CHECKLIST.md` records every step, measurement and dead end in detail.

## Use

**Each board as its own transceiver** (recommended): run the station once per board and pick the board in the window that opens.
Both boards send and receive (half duplex), so they work each other like two stations.

```powershell
.\.venv\Scripts\python.exe espdr_trx_tk.py --call <your call sign>
```

It loads `release/esp32-sdr-trx-trx-decim16.bin`: the esp32-sdr-trx transmitter image, which also contains the receiver, built with
the decimation-16 change. The board selector lists the ESP boards on USB by port and MAC; only ESP32-S3 boards run this firmware.

**One receiver board and one transmitter board** in a single window (the first version):

```powershell
.\.venv\Scripts\python.exe espdr_cw_tk.py --call <your call sign>
```

In both, the window works like this:

- **Beacon** (per-board station): "ID every … s at … WPM" and Start/Stop. Each cycle sends `DE <call>` in CW and then a steady
  carrier until the next ID. It runs until you press Stop or close the window; the board does not receive meanwhile. Beacon
  operation has its own rules in your licence conditions (automatic control, frequencies, identification): check them first.
  To run a beacon with no PC at all, see the standalone firmware in [beacon/README.md](beacon/README.md).
- **Mode** (per-board station): CW, Feld Hell, Olivia 8/250 or Olivia 16/500. In Hell mode the receive area shows the scrolling
  Hell image instead of text, with **Auto phase** and a **Phase** slider to keep letters whole. In Olivia the status line shows
  lock, offset and decode quality; keep the lead tone on (the receiver's AFC needs it).
- **Frequency:** type MHz (2320.005–2449.995) and press Apply; both boards retune.
- **Receive / decode:** decoded text, speed (WPM), signal (dB) and the AFC tone; **Monitor** and **Vol** play the receive audio
  (noise and CW) on the default speakers (`--monitor-device` picks another).
- **Transmit / encode:** type text (the Morse preview updates as you type) and press Send; **Send ID** sends `DE <call>`;
  **Tune 2 s** sends a steady carrier. The indicator shows **TX** while board B transmits; board A keeps receiving, so your
  own transmission appears decoded.
- Options: `--dial`, `--rx-port`, `--tx-port`, `--wpm`, `--lead`, `--space-shift`, `--power` (see `--help`).

## Files

| File | What it is |
|---|---|
| `espdr_trx_tk.py` | Station for one board that sends and receives, with the board selector |
| `trx_board.py` | One board as a half-duplex transceiver over one USB link |
| `espdr_cw_tk.py` | The CW station window (also the two-board version: one board receives, one transmits) |
| `cw_decoder.py` | Streaming Morse decoder |
| `hell.py` | Feld Hellschreiber: font, encoder, receiver (image columns) |
| `olivia.py` | Olivia: codec, tone plan, transmitter records, reference modulator |
| `olivia_rx.py` | Olivia receiver: acquisition, drift tracking, timing and block sync |
| `espdr_fldigi.py` | Receive chain (rtl_tcp client, AFC, demodulator), CW keyer and transmitter control; also an fldigi bridge |
| `decim16-firmware.patch` | Receiver firmware change: decimation 16 (62.5 ksps) |
| `espdr-windows.patch` | Fixes and additions to the installed `espdr` package for Windows |
| `beacon/` | Standalone CW beacon firmware (`beacon.c`), its firmware-tree patch, build and flash scripts: see [beacon/README.md](beacon/README.md) |
| `release/` | esp32-sdr-trx v1.0.0-rc4 release files and the decimation-16 receiver image |
| `scripts/` | Measurement tools: gain keying test, I/Q capture and analysis, USB throughput |
| `SETUP-CHECKLIST.md` | Full setup log with every measurement |

## Credits and licences

Built on [eSpDR](https://github.com/h0m3us3r/eSpDR) by h0m3us3r and
[esp32-sdr-trx](https://github.com/jochenhammes/esp32-sdr-trx) by jochenhammes, both under the Zero-Clause BSD licence (0BSD).
The firmware images link Espressif's PHY library from ESP-IDF (Apache-2.0). Much of this project's code was written with Claude
(Anthropic) as the coding assistant.
