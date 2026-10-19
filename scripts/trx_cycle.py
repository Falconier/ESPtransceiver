"""Diagnostic: one board, one link: receive, transmit, receive again (the TX image holds both).

  python scripts/trx_cycle.py COM5 IMAGE DIAL_MHZ "TEXT"
"""
import sys
import threading
import time

import numpy as np

sys.path.insert(0, ".")
import espdr_fldigi as br                                        # noqa: E402
from espdr import board, nb                                      # noqa: E402

port, image, dial, text = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
board.ensure(board.TX, port=port, native=True, load="always", image=image, log=print)
link = nb.open_link(port)
print("firmware flags", board.probe.__name__ and link.command(nb.CTL_INFO, 3)[1])


def receive(seconds):
    got = dict(samples=0, packets=0)
    t_end = time.monotonic() + seconds

    def sink(h, payload):
        got["samples"] += h["count"]
        got["packets"] += 1

    nb.configure_receiver(link, dial * 1e6, 16, nb.FORMATS["cs8"], 4, 0)
    status, value, stats = link.run(0, sink, stop_flag=lambda: time.monotonic() > t_end)
    rate = got["samples"] / seconds
    print(f"  RX {seconds} s: {got['samples']} samples ({rate / 1e3:.1f} ksps), lost {stats['lost']}, dropped {stats['dropped']}, "
          f"status {status} value {value}")


print("RX before")
receive(4)
print("TX", repr(text))
tx = br.Transmitter.__new__(br.Transmitter)                     # reuse the open link instead of opening the port again
tx.power_db, tx.tone_hz, tx.ppm, tx.space_hz = 0.0, 700.0, 0.0, 0.0
tx.set_dial(dial * 1e6, 12000)
tx.session = br.txlink.Session(link)
t = np.arange(2 * br.TX_RATE) / br.TX_RATE
audio = np.concatenate([0.5 * np.minimum(1, np.minimum(t, t[::-1]) / 0.005) * np.sin(2 * np.pi * 700 * t),
                        np.zeros(br.TX_RATE // 2), br.morse_audio(text, wpm=12, tone=700)])
tx.transmit((audio[i:i + 160] for i in range(0, len(audio), 160)), threading.Event())
link.ser.timeout = 0.05
print("RX after")
receive(4)
link.ser.close()
