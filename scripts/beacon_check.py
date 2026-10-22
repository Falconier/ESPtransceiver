"""Diagnostic: load the standalone beacon into one board's RAM (the flash is not touched) and decode it with another board.

  python scripts/beacon_check.py BEACON_PORT RX_PORT [SECONDS] [IMAGE] [MODE]

BEACON_PORT "-": only listen, e.g. to a flashed beacon. IMAGE "-": the default. MODE: the receiver's mode, cw (default) or
olivia-16/500.

The beacon keeps running after this script ends; press RESET on its board (or load other firmware) to stop it.
"""
import argparse
import sys
import time

sys.path.insert(0, ".")
import trx_board                                                 # noqa: E402
from espdr import board                                          # noqa: E402

beacon_port, rx_port = sys.argv[1], sys.argv[2]
seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 75
image = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "-" else "release/esp32-beacon.bin"
mode = sys.argv[5] if len(sys.argv) > 5 else "cw"

if beacon_port == "-":
    print("not loading: listening for a beacon that runs from flash")
else:
    print(f"loading the beacon into {beacon_port}'s RAM ...")
    board.load_ram(board.TX, native=True, image=image, log=print, port=beacon_port)   # not ensure(): the beacon answers no requests
t0 = time.monotonic()

args = argparse.Namespace(port=rx_port, image="release/esp32-sdr-trx-trx-decim16.bin", dial=2394.250, rx_gain=0, rx_ppm=0.0,
                          tx_ppm=0.0, power=0.0, tone=700.0, space_shift=12000.0, wpm=20.0, lead=2.0, call=None,
                          monitor_device=None)
radio = trx_board.Transceiver(args)
radio.start()
radio.set_mode(mode)
text = ""
while time.monotonic() - t0 < seconds:
    time.sleep(0.2)
    while not radio.log.empty():
        print(f"[{time.monotonic() - t0:5.1f} s] {radio.log.get()}")
    while not radio.text.empty():
        text += radio.text.get()
    sys.stdout.write(f"\r[{time.monotonic() - t0:5.1f} s] decoded: {text[-90:]}")
    sys.stdout.flush()
print()
radio.close()
print("decoded:", repr(text))
