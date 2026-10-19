"""Diagnostic: capture rtl_tcp I/Q for N seconds, or analyse a capture: where the transmitter is and how its level moves.

  python scripts/iq_track.py capture FILE SECONDS [dial_mhz]
  python scripts/iq_track.py analyse FILE [step_ms]
"""
import socket
import struct
import sys
import time

import numpy as np

FS = 250_000

if sys.argv[1] == "capture":
    path, secs = sys.argv[2], float(sys.argv[3])
    dial = float(sys.argv[4]) if len(sys.argv) > 4 else 2350.0
    s = socket.create_connection(("127.0.0.1", 1234), timeout=30)
    s.recv(12)
    s.sendall(struct.pack(">BI", 2, FS) + struct.pack(">BI", 1, int(dial * 1e6)))
    end = time.time() + secs
    with open(path, "wb") as f:
        while time.time() < end:
            f.write(s.recv(65536))
    s.close()
    sys.exit()

path = sys.argv[2]
step = (float(sys.argv[3]) if len(sys.argv) > 3 else 50) / 1000
a = np.frombuffer(open(path, "rb").read(), np.uint8).astype(np.float32) - 127.5
iq = a[0::2] + 1j * a[1::2]
n = 1 << 15
f = np.fft.fftshift(np.fft.fftfreq(n, 1 / FS))
spec = np.array([np.abs(np.fft.fftshift(np.fft.fft(iq[i:i + n] * np.hanning(n)))) ** 2 for i in range(0, len(iq) - n, n)])
base = np.median(spec, axis=0)                      # the spurs and noise that are always there
ratio = spec / (base + 1e-9)
band = (f > -20_000) & (f < 20_000)
k = np.unravel_index(np.argmax(ratio[:, band]), ratio[:, band].shape)
fc = f[band][k[1]]
print(f"{len(iq) / FS:.1f} s; strongest new signal at {fc:+.0f} Hz ({10 * np.log10(ratio[k]):.0f} dB over its bin's median)")
# narrowband level around fc (+-60 Hz) in steps, following a slow drift
m = int(step * FS)
t = np.arange(len(iq)) / FS
y = iq * np.exp(-2j * np.pi * fc * t)
win = int(FS / 120)
lvl = np.abs(np.convolve(y, np.ones(win) / win, "same"))[m // 2::m]
db = 20 * np.log10(lvl / lvl.max() + 1e-9)
print(f"level at {fc:+.0f} Hz per {step * 1000:.0f} ms (dB rel. max):")
for i in range(0, len(db), 20):
    print(f"{i * step:5.1f}s " + " ".join(f"{v:4.0f}" for v in db[i:i + 20]))
