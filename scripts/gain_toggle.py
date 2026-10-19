"""Diagnostic: transmit a fixed tone offset whose gain code alternates every `period` seconds, at a given update rate.

  python scripts/gain_toggle.py COM5 2350.0 [rate] [period] [cycles] [code_hi] [code_lo]
"""
import queue
import sys

import numpy as np

from espdr import board, cli_tx, nb, txlink, txmodes

port, dial_mhz = sys.argv[1], float(sys.argv[2])
rate = int(sys.argv[3]) if len(sys.argv) > 3 else 8000
period = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
cycles = int(sys.argv[5]) if len(sys.argv) > 5 else 3
hi = int(sys.argv[6]) if len(sys.argv) > 6 else 64
lo = int(sys.argv[7]) if len(sys.argv) > 7 else 127
assert cli_tx._licence_accepted(), "run espdr-tx --accept-licence once"

port = board.ensure(board.TX, port=port, native=True, log=print)
s = txlink.Session(nb.open_link(port))
lo_hz, _ = txlink.choose_lo(dial_mhz * 1e6)
off = dial_mhz * 1e6 - txlink.word_hz(txlink.lo_word(lo_hz)) + 700.0
n = int(rate * period)
q = queue.Queue()
for _ in range(cycles):
    for code in (hi, lo):
        q.put(txmodes.pack(np.full(n, off / txmodes.Q4_HZ), np.full(n, code)))
q.put(None)
s.configure(lo_hz, rate, limit_s=60)
s.begin(expect_word=txlink.lo_word(lo_hz))
print(f"rate {rate}/s, codes {hi}/{lo}, {period} s each, {cycles} cycles, offset {off:.0f} Hz")
summary = s.stream(q, target_fill=min(8000, rate))
print("end:", txlink.END_REASONS.get(summary["reason"]), "underruns", summary["underruns"])
