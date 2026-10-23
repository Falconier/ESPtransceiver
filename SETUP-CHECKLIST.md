# ESP32 SDR transmit + receive: requirements & setup checklist

Replicates https://github.com/h0m3us3r/eSpDR/issues/3, which is implemented in
**jochenhammes/esp32-sdr-trx** (built on h0m3us3r/eSpDR).

## 0. Hardware reality check

| Board | Usable? | Why |
|---|---|---|
| ESP32-S3 (x2) | **Yes** | All firmware, register addresses and the `esptool --chip esp32s3` call are S3-specific |
| ESP32-C5 (x2) | **No** | Different chip (RISC-V, dual-band radio). No port exists; the undocumented S3 registers in issue #3 don't apply |

Your two S3s make a full TX/RX pair: **board A = `espdr-tx` transmitter, board B = `espdr-rx` receiver** (RX covers 1.84-2.79 GHz, which includes the 2320-2450 MHz TX range).

- [ ] Each S3 board ideally has **two USB ports**: native USB (303a:1001) + a USB-UART bridge (CH340/CH343/CP210x/FTDI). The tool loads the firmware through the bridge without buttons.
- [ ] Single-port boards (e.g. XIAO S3) work with `--native`: hold BOOT, tap RESET, then run the command.
- [ ] 2 USB data cables per two-port board (4 total for both S3s)
- [ ] Optional for checking the TX independently: a second SDR (HackRF / PlutoSDR / any 13 cm receiver)
- [ ] Optional (only for full 80 Msps eSpDR, not needed here): Alchitry Au FPGA + Br + FT600 board

## 1. Legal

- [ ] **Amateur radio licence** that covers 13 cm (2320-2450 MHz). Needed for `espdr-tx` (`--accept-licence` is your statement that you hold one).
- [ ] First tests: low power is already the default (microwatts). Keep metres apart or use a dummy load. Don't add amplifiers or gain antennas.

## 2. OS

| OS | Status |
|---|---|
| Linux (Ubuntu/Debian/Fedora/Arch) | Upstream-tested |
| **Windows 11 (this PC)** | Untested upstream; **installed and self-tested here** (pure Python: pyserial + esptool) |
| WSL2 + usbipd-win | Fallback if Windows misbehaves; usbipd is already installed, but no distro yet (`wsl --install -d Ubuntu`) |

## 3. Software packages

Host tools (no build needed; the firmware ships in the wheel):

- [x] Python >= 3.10 (using 3.13 in `.venv`)
- [x] `esp32_sdr_trx-1.0.0rc4-py3-none-any.whl` (release v1.0.0-rc4, SHA256 verified) in `release/`
- [x] Pulled in automatically: `numpy>=1.21`, `pyserial>=3.5`, `esptool>=5.0` (got 5.6.0)
- [x] `sounddevice` (the `[audio]` extra) for transmitting from a sound card
- [ ] **SDR++** (Windows build from https://github.com/AlexandreRouma/SDRPlusPlus/releases) as the receiver UI
- [ ] USB-UART drivers if Windows doesn't show a COM port: CH343/CH340 (WCH) or CP210x (Silicon Labs)
- [ ] Linux only: udev rule `70-espdr.rules` (or `dialout` group), plus `libportaudio2` for audio

Only to rebuild firmware or use the research branch (raw 80 Msps I/Q TX, LoRa):

- [ ] ESP-IDF **v5.5.3+** (`make -C firmware images`; research build: `make -C firmware TX=1 IQTEST=1`)
- [ ] `make`, git, and Linux/WSL are recommended for this path
- [ ] Python dev extras: `pip install -e '.[dev]'` (scipy, pytest)

## 4. Repos (cloned into `repos/`)

- [x] https://github.com/jochenhammes/esp32-sdr-trx: `main` (espdr-rx / espdr-tx) and the `research/iq-tx` branch (I/Q playback engine, LoRa)
- [x] https://github.com/h0m3us3r/eSpDR: the original 80 Msps FPGA design (reference)

## 5. Run it (Windows, from `S:\Personal_Projects\ESPOlivia`)

```powershell
.\.venv\Scripts\Activate.ps1
```

Receiver (board B):

```powershell
espdr-rx                      # loads RX firmware into RAM (~8 s), starts rtl_tcp on 127.0.0.1:1234
```

SDR++: Source = RTL-TCP, host 127.0.0.1, port 1234, play, tune to 2350 MHz, NFM.

Transmitter (board A), in a second terminal, naming board A's ports explicitly:

```powershell
espdr-tx --accept-licence -f 2350 -m fm --test-tone 1000 --duration 10 --port COMa --bridge-port COMb
espdr-tx -f 2350 -m usb -i speech.wav --ppm <measured>   # SSB voice
```

With both boards plugged in, give each command its own `--port` (native) and `--bridge-port` (UART) or the tools will refuse ("several ... found").

## 6. Status on this PC (2026-10-17)

- [x] Windows 11, git 2.47, Python 3.13/3.14, usbipd-win present
- [x] venv + tools installed; `espdr-rx --selftest` PASSED; `espdr-tx --dry-run` runs
- [x] Boards: **A = COM4** (MAC C4:9E:7E:07:44:88), **B = COM5** (MAC C4:9E:7E:08:6F:10). ESP32-S3 QFN56 rev v0.2, 8 MB PSRAM, 40 MHz crystal. Native USB only (no UART bridge), loads without buttons via `usb-reset`
- [x] **Receiver running on board A**: `espdr-rx --native --port COM4`, rtl_tcp 127.0.0.1:1234, tuned 2437 MHz, Wi-Fi visible ~30 dB over noise, survives reconnects
- [ ] SDR++ not installed yet
- [x] **TX -> RX loop works** (licence accepted by user): board B (COM5) sent FM, 1 kHz test tone, 2350 MHz, 10 s; board A (COM4) received it
  (capture `captures/rx_2350.u8`, 250 ksps u8 I/Q): carrier exactly 10 s long, +11.6 dB rms, at +4..+8 kHz (combined crystal error),
  drifting about -1.5 kHz over 10 s (thermal). FM demod over the first 5 s: **999 Hz tone, 26 dB over noise**
- [ ] **Open issue: Windows USB throughput.** TX buffer drained 107 -> 0 ms in 5.6 s, then underruns (counter 255) and the tone fell apart
  (the carrier stayed). RX dropped ~56% of samples during long runs ("units dropped by the ESP"). Both point to the native USB CDC path on
  Windows being slower than on Linux. Next: try WSL2 + usbipd, or investigate usbser.sys buffering / write sizes

## 8. Decision: native Windows only (proof of concept)

WSL dropped: the VM creation error persisted even after the admin fixes and a reboot. The PoC goal (S3 transmits, other S3 receives and decodes) is already met on Windows.
Run 2 (after the BOOT/RESET recovery): carrier exactly 10 s, +12.0 dB, offset -0.12 kHz; TX buffer empty after 2.1 s; RX again lost ~5.6M samples;
FM demod shows a tone near 890 Hz (not 999 Hz) at 19-21 dB, likely distorted by the sample loss.

| Run | TX firmware load | TX buffer empty at | Carrier | Tone while buffer OK | Tone after underrun | RX samples lost |
|---|---|---|---|---|---|---|
| 1 | yes | 5.6 s | 10 s, +11.6 dB | 999 Hz, 26 dB | 420 Hz junk | (start-up only) |
| 2 | yes | 2.1 s | 10 s, +12.0 dB | (window overlapped underrun) | ~890 Hz | 5.64M / 40 s |
| 3 | no (already in RAM) | 1.6 s | ~10 s, +11.6 dB | **999 Hz, 20 dB** | ~885 Hz | 5.65M / 40 s |

Takeaway: the RF link is solid and repeatable; audio is correct exactly as long as the host keeps the TX buffer fed. The underrun artefact is consistent (~885 Hz).
RX loss is very consistent (~23,500 units dropped per 40 s, about 56%), so it looks systematic rather than random.

Known PoC limits: TX underruns after about 5 s and RX sample drops on long runs (Windows USB CDC throughput). Not investigated further.

**Board recovery:** the firmware in the boards' flash runs TinyUSB (enumerates as 303a:4001) and ignores esptool's reset. To get back to the
ROM loader (303a:1001): hold **BOOT**, tap **RESET**, release BOOT; then `espdr-rx --native --port COMx` / `espdr-tx --native --port COMy`.
Optional: `espdr-rx --native --port COMx --flash` writes the receiver to flash so it boots straight into it.

## 9. fldigi CW bridge: `espdr_fldigi.py` (work in progress)

Found on the way: **`--update-rate 8000` streams with 0 underruns on Windows** (40000 underruns within 1.6-5.6 s). The Windows 1 ms timer and a
1 MB serial buffer did not help (RX loss unchanged at ~56%).

- [x] Program written: RX = rtl_tcp -> USB demod -> sound device / WAV; TX = fldigi audio -> CW keyer (envelope -> gain code, tone -> fixed offset)
  at 8000 records/s; PTT from fldigi XML-RPC (`main.get_trx_state`)
- [x] Offline checks: keyer tracks a 700 Hz tone (699 Hz); USB demod puts +700 Hz at 700 Hz audio, opposite sideband -93 dB
- [x] fldigi 4.2.13 XML-RPC reachable (state RX). Set fldigi's Op Mode to **CW**
- [x] Hardware: board B transmits with 0 underruns; board A receives the carrier
- [x] **Bug fixed: TX never started.** The firmware turns the carrier on only once its ring holds `TX_PREFILL` = 4000 records
  (`firmware/src/radio.c:637`); the bridge kept only 2400 in flight. Now 4800 (600 ms). Transmissions end "finished", full length, 0 underruns
- [x] Gain keying verified: `scripts/gain_toggle.py` (code 64/127, 1 s each) gives ~20 dB on/off at the receiver; "T T" at 3 WPM via the bridge keys cleanly
- [x] Receiver overload fixed: default `--rx-gain` is now 15 (60 overloads with the boards side by side, rms ~90 of 127)
- [x] RX AGC decay is now per second (it was per socket read and filled CW spaces)
- [x] **Frequency drift between boards: several kHz, run to run** (+7.1, -2.0, +5.2, -2.1 kHz from the dial within minutes). A fixed
  `--tx-ppm` does not hold. `--rx-shift` retunes the receiver; `--selftest` prints the value to use ("To hear it at 700 Hz: --rx-shift ...")
- [ ] At 6-12 WPM my own envelope decoders give inconclusive results (carrier drifts hundreds of Hz during a message; RX still loses ~59%
  of samples). Next real test: **fldigi's CW decoder** (AFC, tracking) on the bridge's audio
- [ ] MIXLINE still passes no audio (0.0000 rms on both inputs, tested three times)

### fldigi test (VB-CABLE, call W4DQB/EXP)

- [x] VB-CABLE loops back (CABLE Input -> CABLE Output). One cable is enough half-duplex: fldigi capture = CABLE Output, playback = CABLE Input;
  bridge `--rx-out "CABLE Input" --tx-in "CABLE Output"` (RX audio muted while fldigi transmits)
- [x] fldigi XML-RPC: state, mode (set to CW, 700 Hz), RX text all work; AFC needs the button (set_afc via XML-RPC is ignored)
- [x] Bug fixed: `--tx-in` was not resolved through `find_device` (crashed with several "CABLE Output" devices)
- [x] **Drift while transmitting: ~190-600 Hz/s, kHz per message** (-1190 -> +2014 Hz in one 17 s message). Added a receive AFC in the
  bridge (`RxAfc`, on by default, `--afc-tone 700`): offline it holds a carrier drifting -824..+1247 Hz at 705-709 Hz audio, ignoring fixed spurs
- [x] fldigi receives and decodes characters from board B through the bridge
- [ ] **Decodes are wrong** at 15 and 5 WPM ("VVV DE W4DQB/EXP" -> `SBT**EUE7 I`, `RBT*E6TAEE E T`, `7 B ETG T D`)
- [ ] Root cause open: **keying contrast at the receiver is ~4 dB at CW speeds** (80 ms gain toggles, `scripts/gain_toggle.py`),
  ~20 dB with 1 s toggles. The firmware applies each record's gain immediately (`radio.c:707`), so either the chip's output does not follow
  fast gain changes cleanly (a second line ~350 Hz below the carrier appears too) or the receive side smears it (~59% sample loss on Windows).
  An independent receiver (RTL-SDR / HackRF / PlutoSDR with SDR++) would tell which
- [x] **`--space-shift 12000` fixes the keying contrast**: during spaces the (weakest, -18 dB) carrier moves 12 kHz away, out of the receive
  passband and AFC span. Received audio now shows clean `...-` for V. fldigi at 12 WPM decoded `4VI EI D USTBDE T TP` from
  "VVV VVV DE W4DQB/EXP": V, DE and parts of the call come through. Note the spaces are a weak emission 12 kHz up (in band)
- [ ] Remaining decode errors: set fldigi's CW speed to the sending speed (it starts at its own default and tracks), and the ~59% RX sample
  loss still nicks elements
- [ ] fldigi -> board B (TX path) not tested yet

### Independent receiver check (ESP-WebSDR on a separate ESP32, 2394.250 MHz)

- [x] RTL-SDR cannot reach 2350 MHz (tops out ~1.7 GHz); used ESP-WebSDR (snapshot spectrum/waterfall) instead, test moved to 2394.250 MHz
- [ ] 1 s vs 80 ms gain toggles: hard to tell apart on the snapshot display
- [x] 5 s solid carrier + "DE W4DQB/EXP" at 10 WPM (no space shift), sent twice: **CW keying with clear gaps seen** on the independent receiver
- Conclusion (visual, moderate confidence): **board B keys properly; the keying is mostly lost on board A's receive path**, most likely the
  ~59% Windows sample loss. Next: reduce or fix the RX loss (fewer bytes per sample, a narrower firmware decimation, or board A on Linux);
  `--space-shift` remains a working mitigation meanwhile
- Note: at 2394.250 the PLL puts the LO ~15 kHz below the dial; records carry +15.3 kHz, so a faint LO line near 2394.235 MHz is expected

### RX sample loss on Windows: root cause

- [x] **Measured: Windows' usbser.sys moves 0.275 MB/s** from the S3's USB-Serial/JTAG port (firmware NB_BENCH, both write modes; and
  `scripts/usb_raw_bench.py` with raw reads of 4 KB..1 MB and driver buffers up to 4 MB: always 0.275). Linux: ~0.87 MB/s (author).
  The receiver needs 0.5 MB/s (250 ksps x 2 bytes), so ~45% gets through: the host is not the bottleneck, the driver path is
- [x] Firmware minimum rate is 250 ksps (`NB_SET_DECIM` 2..4, `firmware/src/stream.c:67`)
- Options:
  1. **Firmware: add decimation 8 (125 ksps = 250 KB/s, under the limit).** Taps come from `firmware/tools/design_taps.py`; also `dsp.c`
     (tap/PIE selection), `stream.c` (range), host `rtltcp.SRC_RATES`. Needs ESP-IDF v5.5.3 to build. Plenty for CW (+-50 kHz)
  2. WinUSB + libusb with several transfers in flight (Zadig): replaces the COM port, and both boards share VID:PID 303a:1001, so it
     would break board B's firmware loading. Not recommended
  3. Run board A's receiver on a Linux machine
  4. Keep `--space-shift 12000` as the workaround

### Fix: receiver firmware with decimation 16 (62.5 ksps)

- [x] ESP-IDF **v5.5.3** at `S:\esp\esp-idf` (C: has little space); Xtensa GCC 14.2.0 via `idf_tools.py install xtensa-esp-elf` from
  **PowerShell** (it refuses Git Bash) with `IDF_TOOLS_PATH=S:\esp\.espressif`; GNU make 4.4.1 via `scoop install make`.
  Build from Git Bash (recipes need sed/awk):
  ```bash
  export PATH="/s/esp/.espressif/tools/xtensa-esp-elf/esp-14.2.0_20251107/xtensa-esp-elf/bin:$PATH" IDF_PATH="S:/esp/esp-idf"
  make -C repos/esp32-sdr-trx/firmware TX=0 NARROWBAND=1 BUILD=build-rx ESPTOOL="$PWD/.venv/Scripts/python.exe -m esptool"
  ```
- [x] Baseline (unmodified, tag v1.0.0-rc4, branch `decim8` (name kept; it now holds decim 16)) builds and runs on board A: same ~56% loss
- [x] Firmware change (`decim16-firmware.patch`): R2 = 16 in `tools/design_taps.py` (81 taps, +-15 kHz, -47 dB stopband), `dsp.c` tap
  selection, `dsp.h` output count and wrap constant (4294967040 = 768 x 5592405: keeps the block phase mod 2/3/4/16), `stream.c` range.
  R2 = 8 (125 ksps) was tried first: verified on chip (SIMD = C), but **still lost 18.6%** (270 of 275 KB/s); RAM fits only one extra table
- [x] On chip: stock R2 = 4 still passes SIMD-vs-C (0 bytes differ). R2 = 16 cannot be encoded in NB_DSPBENCH's 4-bit field; it runs the
  same code as R2 = 8 (same 81-tap length), which passed
- [x] **Result: 0 samples lost in 20 s at 62.5 ksps** (was ~56% at 250 ksps). Known carrier at the expected offset, clean 1 s keying
- [x] At 62.5 ksps the 8-bit output clipped (70% of key-down samples) with RX gain 15: use **gain 0** (0% clipping, 15.6 dB keying contrast)
- [x] Host (`espdr-windows.patch`): `espdr-rx --decim 16`, `rtltcp.SRC_RATES[16]`; image saved as `release/esp32-sdr-trx-rx-decim16.bin`
- [x] Bridge: `--decim 16 --rx-image ...` (RX gain defaults to 0 then); AFC scales its FFT (66 ms) and ignores signals >12 dB below the
  recent peak, so it no longer locks onto the space carrier (it did once on air, at ~16 s)
- [ ] fldigi decode still poor (`R E<AS>T T U T *ISTT E<AS>`), but the audio fldigi hears (recorded from CABLE Output: only 10 ms of
  gaps) decodes with a simple timing decoder as `?EUVV VVV DE W4DQB/EIUI E?`: the call comes through. Remaining: dit lengths vary
  40-110 ms (should be 100); fldigi's own speed tracking / settings (set its CW WPM to the sending speed)

### fldigi decoder tuning

- [x] Via XML-RPC toggles: squelch off (was on at 30), AFC on (`set_*` calls are ignored; `toggle_*` work). Bandwidth, WPM and speed
  tracking are GUI-only: user set 250 Hz, 12 WPM, tracking 8-20, SOM on
- [x] Live RX test: the audio is now essentially perfect (dits 105 ms, dahs 305 ms, gaps 95 ms; a simple decoder reads
  `?IUVV VVV DE W4DQB/EXP`), but fldigi decoded `TVV ZUDE W$`
- [x] **fldigi also garbles that recorded audio (`.V DE$`) and a perfect synthetic CW signal (`$ <BT>B`)** played into CABLE Input:
  the radio chain is fine; the problem is fldigi's audio input or decoder configuration
- [x] SOM off, 48 kHz (user): fldigi still garbles the perfect synthetic signal (`A AE E`; with a 2 s lead tone `T V)*DEP`)
- [x] **Test frequency is now 2394.250 MHz** (where the user's ESP-WebSDR watches; experimental work). There the PLL puts the LO
  14.6 kHz below the dial, so `--space-shift` now picks the direction that fits the records' +-19.2 kHz reach (spaces go -12 kHz)
- [x] Self-test sends a **2 s lead tone** (`--lead`, default 2) before the message: the first characters now decode cleanly.
  2394.250 run: simple decoder `[2s tone] V I U VV DE W4DQB/EXP` (one dropout); fldigi `W V U<AS>/G`
- [ ] fldigi itself: needs a look at its window (waterfall trace, displayed WPM, status bar) while the synthetic signal plays

### Own CW station instead of fldigi: `espdr_cw_tk.py` (Tk window) + `cw_decoder.py`

- [x] `cw_decoder.py`: streaming decoder (5 ms envelope at the AFC tone, peak/floor trackers, adaptive dot length, bridges drop-outs
  < 0.35 dot, shows lead/tune carriers as `[carrier]`). Offline: synthetic 8/12 WPM perfect (light and heavy noise), 18/25 WPM after
  1-2 characters; real recordings: all decode the call (2394.250 run: `VSV VVV DE W4DQB/EXP`)
- [x] Window: frequency selector (2320.005-2449.995 MHz, Apply retunes **both** boards), RX/TX indicator, key light;
  Receive/decode pane (speed, signal, tone); Transmit/encode (text + live Morse preview, Send, WPM, lead tone, space shift, Call,
  Send ID, Tune 2 s). Board A keeps listening while B sends, so your transmission is decoded in the window
- [x] Bridge: `RtlClient.retune()`, `RxAfc.reset()`, `Transmitter.set_dial()` (replans LO and space direction between transmissions)
- [x] Offline window test: decoded `VVV DE W4DQB/EXP`, preview correct, out-of-band frequency refused
- [x] Live: 2394.250 `VV DE W4DQB/EXP`; retune to 2394.300 and back worked; at 2394.300 `E[carrier]DE I*DQ /EXP` (cause not pinned:
  AFC just reset by the retune, or the other space direction there)
- [x] **Monitor**: tickbox + Vol slider in Receive/decode plays the receive audio (noise and CW) on the default speakers
  (`--monitor-device` to pick another). Confirmed by the user
- (the first web-interface attempt was cut off: `espdr_cw.py.incomplete`, not used)

### Each board sends and receives: `espdr_trx_tk.py` + `trx_board.py`

- [x] The esp32-sdr-trx **TX image also holds the receiver** (build flags 3 = narrowband RX + TX) and sets the receiver up again after
  each transmission (`TX_END_FAILED` if it cannot). Built with decimation 16: `release/esp32-sdr-trx-trx-decim16.bin` (77,424 bytes, fits)
- [x] One board, one link (`scripts/trx_cycle.py`): RX 4 s (62.0 ksps, 0 lost) -> TX "DE W4DQB/EXP" (finished, 0 underruns) -> RX 4 s
  (62.4 ksps, 0 lost)
- [x] `trx_board.Transceiver`: one board, half duplex, no espdr-rx/rtl_tcp: a thread keeps `nb.Link.run` streaming and only queues
  packets; a DSP thread conjugates, removes DC, applies the LO's fine offset, then AFC, demodulator, decoder, monitor. Transmit stops the
  stream, runs a transmitter session on the same link, then resumes
- [x] Headless, both boards at 2394.250 MHz: **B->A `[carrier]VVV DE W4DQB/EXP`, A->B `[carrier]VVV DE W4DQB/EXP`** (both perfect)
- [x] `espdr_trx_tk.py`: **board selector** (lists ESP boards on native USB with port and MAC; Board A/B known by MAC; warns before an
  unknown board: a third ESP board is on COM13, MAC 38:44:BE:A9:FD:A8), then the station window for that board. Run it twice, one per board
  ```powershell
  .\.venv\Scripts\python.exe espdr_trx_tk.py --call W4DQB/EXP        # pick Board A; run again and pick Board B
  ```

### Feld Hellschreiber mode

- [x] `hell.py`: 5x7 font in 7 columns (A-Z, 0-9, / ? . , - = + space); Feld Hell timing (2.5 char/s, 57.14 ms per column,
  14 elements of 4.08 ms, bottom-up); `HellRx` measures the 700 Hz tone in 28 slots per column with a slow AGC. No decoding: the image is read
  by eye, two stacked copies so a glyph split by the start offset reads across the join
- [x] Offline round trip (`scripts/hell_png.py` renders columns to PNG): aligned and offset cases read correctly
- [x] TX: `CwKeyer` envelope window is now a parameter (`env_taps`, default 40 = 5 ms for CW); Hell uses 8 (1 ms): 72 records
  mid-transition instead of 229 for "E"
- [x] Window: **Mode** selector (CW / Feld Hell) in the per-board station; Hell mode shows the scrolling image instead of the text,
  checks characters against the Hell font, previews the transmit time. Bugs found in testing: method name clashed with the RX/TX
  indicator (`mode` -> `cur_mode`); Tk 8.6.15 takes raw PGM bytes for PhotoImage, not base64
- [x] **Live, 2394.250 MHz: B->A without space shift: "CQ DE W4DQB/EXP" perfectly clear (key-up shows light grey); A->B with space shift:
  white background, readable across the copies.** For Hell, leave the space shift **off**: readable, and the emission stays on one frequency
- [x] **Column phase** (letters split across the copies; not slant: the crystals drift ~0.1 ms over a 10 s message):
  - **Auto phase** (`HellRx.auto`, on by default): the end of a steady carrier >= 1 s (our 2 s lead tone) re-aligns the columns; the first
    column starts 0.5 s later. The end is placed inside its slot from that slot's level; a constant +1 slot makes up for the lead's 5 ms
    fade-out and slot averaging. Measured offline against the known glyphs: 0 slots error at 10 start offsets (middle-of-slot guess: 0/-1;
    sub-slot without the +1: constant -1)
  - **Phase slider** (0-27 slots) rolls everything shown, for signals without a lead tone or fine-tuning
  - Live both ways: re-aligned once per message, letters whole in both copies

```powershell
.\.venv\Scripts\python.exe espdr_cw_tk.py --call W4DQB/EXP
```

Current best command:

```powershell
python espdr_fldigi.py --dial 2350.000 --rx-port COM4 --tx-port COM5 --decim 16 --rx-image release/esp32-sdr-trx-rx-decim16.bin --space-shift 12000 --rx-out "CABLE Input" --tx-in "CABLE Output"
```
- [ ] **Needs a virtual audio cable pair** for fldigi (MIXLINE does not loop back): e.g. VB-Audio VB-CABLE + a second cable

Planned use once the cables exist (fldigi: audio capture = cable 1 output, playback = cable 2 input):

```powershell
python espdr_fldigi.py --dial 2350.000 --rx-port COM4 --tx-port COM5 --rx-gain 15 --rx-out "CABLE Input" --tx-in "CABLE-B Output"
python espdr_fldigi.py --dial 2350.000 --rx-port COM4 --tx-port COM5 --selftest "TEST" --wpm 8 --rx-wav captures\cw.wav   # no fldigi needed
```

## 10. WSL2 attempt (abandoned)

- [x] Found the boards' UART ports: FTDI 0403:6001, **board A = COM9** (serial A5069RR4A), **board B = COM10**. Both are in the tool's bridge list
- [ ] Native USB ports (COM4/COM5) disappeared from Windows after the receiver was stopped; the tools need native + UART per board
- [ ] `wsl --install -d Ubuntu` fails at VM creation: `Wsl/InstallDistro/Service/RegisterDistro/CreateVm/0x8007273f`
  (virtualization on, hypervisor present, vmcompute running, no third-party Winsock providers; `wsl --shutdown` + retry did not help)
- [ ] Fix needs an **admin** PowerShell, then a reboot:
  `dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart`, `dism /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart`,
  `netsh winsock reset`, `wsl --update`
- [ ] After that: `usbipd bind --busid <id>` (admin, once per device), then `usbipd attach --wsl --busid <id>` for all 4 ports

## 7. Windows fixes (local patch: `espdr-windows.patch`, applied to `.venv`)

1. **Firmware died on every disconnect.** Upstream relies on Linux dropping DTR+RTS together on close; Windows drops them one at a time,
   and DTR=0/RTS=1 resets the S3 into its ROM loader. Fix: `nb.open_link` opens the port with DTR/RTS deasserted, so the lines never change.
2. **`--native` refused with two boards attached.** Fix: `board.load_ram` honours `--port` in native mode.

Re-installing the wheel wipes these: re-apply with `patch -p0` or copy the edited files. Worth reporting upstream (jochenhammes/esp32-sdr-trx).

Use with two boards: always pass `--native --port COMx`:

```powershell
espdr-rx --native --port COM4                                                  # board A: receiver
espdr-tx --native --port COM5 --accept-licence -f 2350 -m fm --test-tone 1000 --duration 10   # board B: transmitter
```

### Olivia MFSK (8/250 and 16/500)

- [x] Reference: fldigi's `src/include/jalocha/pj_mfsk.h` (Pawel Jalocha's Olivia), read and reimplemented in numpy (`olivia.py`):
  7-bit character -> 64 chips by inverse Walsh-Hadamard; scrambling with 0xE257E6D0291574EC from bit 13*k; chip t of character k sets
  bit (k+t) mod bits of symbol t; Gray-coded tones 31.25 Hz apart (both modes), 32 ms symbols; band placed as fldigi does around the
  centre (700 Hz here). Codec checks: transforms exact inverses; round trip exact in both modes; decodes with soft-bit noise sigma 1.0
- [x] `olivia_rx.py`: spectra every 1/8 symbol (512-point Hann, 3.9 Hz bins); acquisition = tone comb vs the band just outside it (signal
  6-50x, noise ~1; the gaps between tones are no help), stable for 8 symbols, needs half the tones present (not a lead tone), drift rate
  from a line through the 8 offsets; decoding at 8 timing phases x tone shifts -2..+2 from kept spectra; block taken at the best
  quality within +-32 symbols; squelch (block quality >= 5, character >= 4); second-order tracking on the cleanest spectrum of each symbol;
  complex-baseband input at 16 kHz in the station (room for kHz of drift)
- [x] Offline (reference audio, `scripts/olivia_offline.py`): all 10 cases OK in both modes (clean, 0 dB, +23 Hz, 100 Hz/s drift, offset +
  drift + 3 dB). Sensitivity: 8/250 full message at -6 dB SNR (3 kHz), loses the first blocks at -9; 16/500 loses the first blocks at -6
  (acquisition is the limit; published Olivia figures are ~5 dB better)
- [x] TX: `Mode.tx_records()` sends one tone per symbol (the chip makes one carrier at a time), phase-continuous, after a 2 s lead at the
  centre. Record frequency steps are 28.6 Hz, so the fields carry the rounding error forward: every tone's average within 0.11 Hz (plain
  rounding: up to 14.1 Hz)
- [x] AFC hold mode for Olivia (`RxAfc.hold_after_lock`): locks only on a steady carrier (>= 15 dB, same frequency frame to frame for 5
  frames: not noise, not Olivia's tones), measures the lead tone's drift line while it lasts (+150.2 for +150 Hz/s, -119.6 for -120 at 0 dB)
  and mixes along that line through the message; unlocks after ~3 s quiet. CW/Hell AFC unchanged (regression OK)
- [x] Station: Mode list has Olivia 8/250 and 16/500; lower case allowed; preview shows the time; status shows lock, offset and quality
- [x] **Live at 2394.250 MHz: 8/250 B->A and A->B, 16/500 B->A and A->B: all "CQ DE W4DQB/EXP", 0 underruns.** The boards drifted about
  -110 Hz/s while transmitting; the AFC's drift line took it out
- [ ] Interop with fldigi not tested: VB-CABLE's endpoints went missing (one of two driver instances in Error state); repair, then play our
  Olivia into fldigi and fldigi's into ours

### Beacons

- [x] **CW beacon** (`Transceiver.start_beacon`, window "Beacon" row): each cycle is "DE <call>" in CW, then a steady carrier to the end of
  the period (default 30 s at 20 WPM: the ID takes 8.1 s, carrier 21.9 s). Cycles go out back to back in one transmission (a new session
  after the firmware's 600 s limit); Stop or closing the window ends it, and the firmware drops the carrier within 0.5 s if the PC stops
  feeding it. A period shorter than the ID + 2 s is refused. Receiving pauses on that board while it beacons
- [x] Offline: three cycles decode as `DE W4DQB/EXP [carrier]` x3 at 20.0 WPM
- [x] Live, 2394.250 MHz, B beaconing 2 cycles, A in CW: `W4DQB/EXP [carrier]DE W4DQB/EXP [carrier]` (the first "DE" fell into A's
  settling), 0 underruns, stopped cleanly
- [ ] **WSPR: skipped for now (owner's decision).** Blocker: WSPR's 1.46 Hz tones over 110 s need drift of a few Hz at most; the boards
  drift ~110 Hz/s while transmitting (kHz per transmission). If it comes back: send **W4DQB** (WSPR cannot encode "/EXP"), grid/power
  placeholder **AA00, 0 dBm** for tests between our boards (the boards' microwatts are below WSPR's 0 dBm minimum). Options then: a
  per-board drift model on the transmitter, or our own drift-removing WSPR receiver
- [x] **Standalone beacon firmware** (`beacon/beacon.c`, built with `make BEACON=1` in the firmware tree after `beacon/firmware-hooks.patch`):
  replaces main.c's command loop; calibrates, picks the LO like `txlink.choose_lo`, and plays 8000 updates/s itself through new
  `radio_tx_key_on/update/off` (the same error feedback and clamps as `radio_tx_run`). Same cycle as the station beacon; 5 ms
  raised-cosine edges through the gain curve; 12 kHz space shift. WS2812 LED on GPIO48: amber hold-off 5 s, red follows the key,
  green paused (BOOT toggles), blinking blue on failure
- [x] First link failed: `.data` overflowed the 8 KB region by 411 bytes; the Morse table was packed into 16-bit codes (now 8127 / 8192)
- [x] `load-ram` of the normal image crashed the ROM loader (StoreProhibited): with less code, esptool put a PADDING segment at
  0x00000000 before the IROM segment. RAM tests use an `elf2image --ram-only-header` build (now the only image, `release/esp32-beacon.bin`)
- [x] Live from RAM, 2394.250 MHz, beacon on B (COM5), A (COM4) in CW: `E E W4DQB/EXP [carrier]DE W4DQB/EXP [carrier]DE W4DQB/EXP`
  (the first ID partly before A had settled)
- [x] Receiver AFC fix found on the way: after ~1 min of beacon, IDs decoded as E/T noise. Raw I/Q showed the carrier was clean
  (~70 dB, ~1 Hz/s drift). The AFC's 4 s median had absorbed the steady carrier and it chased other lines. `RxAfc` now locks
  onto the strongest plain-spectrum peak >= 40 dB over the floor (away from DC), follows it within +-400 Hz (>= 20 dB), holds
  ~3 s through key-up, and searches +-16 kHz (COM17 sits 12.4 kHz from A's dial; 20 kHz took in other lines). Replayed
  captures decode `[carrier]DE W4DQB/EXP`; live, COM17 from RAM for 100 s: 3 IDs in a row clean. Beacon ID now starts after a
  word space (firmware and `beacon_cycle`)
- [x] Third S3 (COM17, C4:9E:7E:08:79:C0, 16 MB quad flash, 8 MB PSRAM): beacon from RAM ~70 dB at A, decodes
- [x] **Flash boot works** (ROM direct boot of a `--ram-only-header` image at flash address 0, no ESP-IDF bootloader). The way there:
  - IDF layout: the small image got a PADDING segment at 0x0 (bootloader refuses it); upstream's own rx image did not answer
    after a flash boot either, so that route was dropped
  - Visibility without a UART: LED stage colours, plus register snapshots and progress markers in RTC fast memory (0x600FE800),
    read back with `esptool --no-stub dump-mem` after a USB reset (RTC memory survives it)
  - Boot loop (USB drop every ~2.6 s): progress stopped 0.27 s into the hold-off. **Timer-group and RTC watchdogs in flash-boot
    mode**: `disable_watchdogs()` cleared only `WDT_EN`; it now also clears `TIMG_WDT_FLASHBOOT_MOD_EN` / `RTC_CNTL_WDT_FLASHBOOT_MOD_EN`
  - CPU not at 240 MHz after power-up (LED blinks stretched): `platform_init()` now enables the BBPLL's analog bus, sets the XTAL
    frequency and the 8 MHz clock as `rtc_clk_init()` does (measured 240.08 MHz after a flash boot)
  - Stage-0 register diff RAM vs flash boot: `RTC_CNTL_OPTIONS0` bit 7 `BB_I2C_FORCE_PU` set after download mode, clear after a
    flash boot; now forced on (the PHY/PLL/SDM writes go over that bus). The super watchdog was already auto-fed by the ROM
  - Live: flashed with `beacon/flash_beacon.py COM17`, started by reset, A decoded `DE W4DQB/EXP [carrier]` 3x in 100 s
  - User check: RESET button and power cycle both go amber blinks, purple, cyan, red keying; no USB drop-outs
- [x] LED: amber/red seen by the user on COM17 (GPIO48 is right for these boards)

### Re-test of all modes after the AFC change (2026-10-20, boards A COM4 / B COM5, 2394.250 MHz, `scripts/mode_check.py`)

- [x] First run: Olivia 8/250 and 16/500 OK both ways; Feld Hell readable both ways (`captures/check_hell_*.png`); **CW failed both
  ways**. Replaying B's I/Q: the old AFC decoded it, the new one locked on a one-frame transient at the key-on, sat there through the
  2 s lead (3 s key-up hold), and later jumped to the 12 kHz space carrier (in a mostly key-up frame the mark looked weak)
- [x] Fix: `RxAfc.track_level`, the tracked carrier's recent strength. A new signal takes over only when comparable to it (>= 1/4;
  a CW space at -18 dB is not), and while the carrier is present only a peak 10 dB stronger. All four saved captures (CW message,
  three beacons) decode offline
- [x] Live after the fix: Olivia 8/250 and 16/500 OK both ways; Hell readable; CW 8 of 9 directions OK over four runs (one B->A
  garbled in the first run, then six in a row clean)

### Beacon modes: CW, Olivia 16/500, both in turn (2026-10-22, beacon on COM17, receiver board B on COM5)

- [x] BOOT short press cycles CW (red) -> Olivia 16/500 (green) -> both in turn (purple); a 1.5 s hold pauses (LED blinks) and
  another resumes. Radio calibration is now white (purple is a mode)
- [x] Olivia in firmware: `beacon/gen_olivia.py` encodes `BEACON_TEXT` + " " with `olivia.py` at build time into
  `beacon/olivia_symbols.h` (256 symbols, in IRAM as 32-bit words; `.data` stays 8127 / 8192). Playback: 2 s lead at 700 Hz, 256
  records per symbol, remainder carried forward as `olivia.tx_records()`, carrier off until the period ends
- [x] From RAM: Olivia-only `DE W4DQB/EXP` x3 clean. Both-in-turn: CW half decoded; the Olivia half did not, because its lead tone
  continued the CW carrier and the receiver's Olivia lock wants a new tone. Fix: carrier off 4 s before an Olivia ID that follows
  CW. Then Olivia x3 clean (one earlier first ID partly garbled)
- [x] BOOT ignored after `flash_beacon.py` (USB reset) but fine after a power cycle. LED pin tests (`-DBEACON_BUTTON_TEST=1/2/3`):
  the pin read fine from RAM in every radio state, but not from flash after a USB reset. Cause: GPIO0 is RTC pad 0, and the RTC pad
  mux / pad hold survive that reset. `button_init()` now clears `RTC_IO_TOUCH_PAD0_MUX_SEL` and `RTC_CNTL_TOUCH_PAD0_HOLD`
- [x] Flashed, started by USB reset: the user confirmed the mode cycle and pause/resume on the board

### Move to 2304.350 MHz (2026-10-22, board B on COM5 and COM17)

- [x] Upstream refuses to transmit below 2320 MHz (a guard; the PLL's normal mode covers 2210-2790 MHz). `protocol/transmit.h`
  `TX_MIN_HZ` is now 2300 MHz, in the beacon and in a rebuilt station image (`release/esp32-sdr-trx-trx-decim16.bin`, which also
  gets the flash-boot start-up fixes in `platform.c`). Python: `txlink.TX_MIN_HZ` overridden in `espdr_fldigi.py`, band checks
  2300.005-2449.995, default dial 2304.350 (stations, scripts, beacon)
- [x] At 2304.350 the LO lands 177 Hz below the dial (at 2394.250: 14.6 kHz), so the CW space shift goes 12 kHz *up*. With COM17's
  crystal ~12 kHz off board B's, that key-up carrier sat inside B's band and the AFC jumped to it near the end of a message
  (COM17->B lost the last characters 3 of 3; fine at 2394.250). `RxAfc(space_hz=...)` now never jumps to a line one space shift
  from its carrier. Then CW 6 of 6 clean both ways
- [x] Station modes at 2304.350: Olivia 8/250 and 16/500 OK both ways (one 16/500 run short, the next two clean), Hell clean both ways
- [x] Beacon at 2304.350: CW from flash `DE W4DQB/EXP [carrier]` x3; Olivia from RAM x3 clean
- [x] Band plan (owner's decision): the stations' default dial is back at **2394.250** for experimental transmissions; only the
  beacon uses **2304.350**. The 2300 MHz floor, the wider band checks and the AFC fix stay, so a station can tune to 2304.350 to hear
  the beacon (`scripts/beacon_check.py` listens there; `scripts/mode_check.py` tests at 2394.250, `DIAL_MHZ=` for others)
- [x] Station test back at 2394.250 (A COM4 / B COM5, rebuilt image): Olivia 8/250 and 16/500 OK both ways, Hell clean both ways,
  CW B->A clean; A->B `CG DE ...` once (the known first-letter slip), then clean both ways on the repeat

### Decoding the beacon from ~30 m (2026-10-22, beacon in a tree through two walls; board A COM4 receiving, B COM5)

- [x] Neither board decoded it, though the ESP-WebSDR (C5) beside them showed it at "2304.32". Gain/shift sweep on A at 2304.350:
  the stream's 8-bit samples had only ~1-2 counts of noise RMS at gain 0 / shift 4 (a weak carrier rounds away); `--rx-shift` added
  (trx_board, espdr_trx_tk; beacon_check via RX_GAIN/RX_SHIFT). Gain >= 60 overloads (RMS falls, peaks hit the limit); gain 80 gives
  nonsense. No beacon line at any gain while the C5 (Wi-Fi AP) was beside the boards
- [x] C5 unplugged: the beacon at 2304.3419 MHz (-8.05 kHz from the dial), ~20-26 dB in 1 s averages, ~17 dB per 65 ms frame,
  fading 5-10 dB (tree)
- [x] AFC fixes found on the way (all replays of earlier captures still decode, plus one CW capture the committed AFC got wrong):
  - it locked on the receiver's own LO leakage, which sits at the fine-tuning offset (`dc_hz`, -177 Hz at 2304.350, +31 Hz at
    2394.250), not at 0 Hz. The station now passes `dc_hz`; +-40 Hz around it is left out (boards A and B, with close crystals, put
    each other's carrier only ~130 Hz from it: +-150 Hz cut them off); in the +-2 kHz spur comb around it nothing is locked directly
    and a carrier is followed only at 20 dB
  - a new signal must show at the same frequency in two consecutive frames (noise bins pass 10 dB every frame, never twice)
  - a weak carrier (< 30 dB) is followed with gain 0.1 instead of 0.5, and kept down to 10 dB over the floor, but only within 15 dB
    of its own recent level (else noise in a strong station's key-up pulled the AFC off between letters)
- [x] Live with the C5 unplugged, gain 40 / shift 2: Olivia 16/500 decoded the distant beacon (`4DQB/EXP DE W4DQB/EXP`,
  `/EXP DE W`, then `DE W4DQB/EXP` in 190 s); CW did not. Offline, the CW ID filtered to +-25 Hz stands ~10 dB clear, but the
  decoder sees ~200 Hz (~6 dB); a narrow filter needs the tone within ~10 Hz, and on cold transmitters the tone wanders +-100 Hz
  through a message. Two attempts (coherent 15-20 ms average, self-tuning tone) broke strong decodes and were reverted
- [x] Station test at 2394.250 after the AFC changes: Olivia 8/250 and 16/500 OK both ways, Hell clean both ways, CW A->B clean,
  B->A one character wrong (`WH` for `W4`)
