# ESP-Station executables

> **Transmitting needs an amateur licence** that covers the frequency. The station only transmits inside 2320–2450 MHz, and only
> after you have accepted the transmitter's licence notice (below).

The per-board station (`espdr_trx_tk.py`) is built as one file that runs without Python:

| System | File | Built by |
|---|---|---|
| Windows 10/11 x64 | `ESP-Station.exe` | `packaging/build.py` on Windows, or the workflow |
| Linux x86_64 (glibc 2.35 or newer, e.g. Ubuntu 22.04+, Debian 12+) | `ESP-Station` | `packaging/build.py` on Linux, or the workflow |

Each file holds the station, the transceiver firmware (decimation 16), esptool and the patched `espdr` package. It loads the
firmware into the board's RAM on every start, as running from Python does.

## Get the executables

- **From GitHub:** every push and pull request runs `.github/workflows/build.yml`. Open the run under **Actions** and download
  **ESP-Station-windows-x86_64** or **ESP-Station-linux-x86_64** from its Artifacts.
- **Build locally** (in the project's environment, with the patched `espdr`):

  ```bash
  .venv/Scripts/python.exe -m pip install pyinstaller
  .venv/Scripts/python.exe packaging/build.py
  ```

  The result is in `dist/`. PyInstaller only builds for the system it runs on, so a Linux build needs Linux.

## Run

Windows:

```bash
ESP-Station.exe --call W4DQB/EXP
```

Linux (once: `chmod +x ESP-Station`):

```bash
./ESP-Station --call W4DQB/EXP
```

Pick the board in the window, or pass `--port COM4` (Windows) or `--port /dev/ttyACM0` (Linux). Run it twice to work two boards
against each other. All the options of the Python version work (`--help`).

**Before the first transmission**, accept the licence notice once. A dialog shows the notice:

```bash
ESP-Station --accept-licence
```

The acceptance is stored in `~/.config/espdr/` (on Windows under your user folder). The Python tools share it, so if you have
already accepted with `espdr-tx --accept-licence`, you don't need to do it again.

## Linux set-up

- **USB access:** install the udev rules shipped next to the executable, then unplug and replug the board:

  ```bash
  sudo cp 70-espdr.rules /etc/udev/rules.d/ && sudo udevadm control --reload
  ```

  Or add yourself to the `dialout` group (`sudo usermod -aG dialout $USER`, then log in again).
- **Monitor (speaker):** needs PortAudio: `sudo apt install libportaudio2`. Everything else works without it.
- **ModemManager** can grab the board's port for a few seconds after it appears. If loading fails with "port busy", wait and retry,
  or stop ModemManager.

## How the build works

- `esp_station.spec` bundles the firmware image, esptool's data files and the `espdr` package.
- `espdr` runs esptool as `sys.executable -m esptool`. Inside the executable that is the program itself, so `espdr_trx_tk.py`
  answers `-m esptool` by running esptool in-process.
- `resource()` in `espdr_trx_tk.py` finds the bundled firmware when frozen, and the files next to the script otherwise.

Tested: the Windows build on board A (COM4), started from another folder. It loaded the firmware and decoded the flashed beacon on
COM17. The Linux build is checked only by the workflow's smoke test (`--help` and `-m esptool version`), not yet on a board.
