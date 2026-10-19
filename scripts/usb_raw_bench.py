"""Diagnostic: raw USB throughput from the receiver firmware's NB_BENCH stream, read without parsing.

  python scripts/usb_raw_bench.py COM4 [seconds] [read_size] [rx_buffer]

Compares against `python -m espdr.nb -p COM4 bench`, whose reader parses every 64-byte block in Python.
"""
import sys
import time

from espdr import nb

port = sys.argv[1]
secs = int(sys.argv[2]) if len(sys.argv) > 2 else 5
size = int(sys.argv[3]) if len(sys.argv) > 3 else 65536
rxbuf = int(sys.argv[4]) if len(sys.argv) > 4 else 0

link = nb.open_link(port)
ser = link.ser
if rxbuf and hasattr(ser, "set_buffer_size"):
    ser.set_buffer_size(rx_size=rxbuf)
ser.timeout = 0.01
link.send(nb.NB_BENCH, secs)
got, t0, last = 0, None, time.monotonic()
while time.monotonic() - last < 1.0:          # until the stream has been quiet for 1 s
    chunk = ser.read(size)
    if chunk:
        if t0 is None:
            t0 = time.monotonic()
        got += len(chunk)
        last = time.monotonic()
dt = last - t0 if t0 else 0
print(f"read size {size}, rx buffer {rxbuf or 'default'}: {got} bytes in {dt:.2f} s = {got / dt / 1e6 if dt else 0:.3f} MB/s")
ser.close()
