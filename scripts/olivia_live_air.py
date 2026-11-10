"""On-air check of live Olivia transmit between two boards: text is "typed" in bursts while the transmitter runs (with pauses that
send idle blocks and a typo fixed before it goes out), as the window's transmit pane does; the other board decodes.

  python scripts/olivia_live_air.py [PORT_A] [PORT_B] [MODES...]      (default COM4 COM5, olivia-8/250 olivia-16/500)
"""
import argparse
import sys
import threading
import time

sys.path.insert(0, ".")
import trx_board                                                 # noqa: E402

port_a = sys.argv[1] if len(sys.argv) > 1 else "COM4"
port_b = sys.argv[2] if len(sys.argv) > 2 else "COM5"
modes = sys.argv[3:] or ["olivia-8/250", "olivia-16/500"]
# (seconds after TX, action): typing arrives in bursts, with a pause long enough for idle blocks between them
SCRIPT = [(0.0, "type", "CQ CQ DE W4DQB/EXP "), (9.0, "type", "LIVE TEZT"), (9.5, "back", 2), (10.0, "type", "ST "),
          (22.0, "type", "PSE K")]
WANT = "CQ CQ DE W4DQB/EXP LIVE TEST PSE K"


def station(port):
    a = argparse.Namespace(port=port, image="release/esp32-sdr-trx-trx-decim16.bin", dial=2394.250, rx_gain=0, rx_ppm=0.0,
                           tx_ppm=0.0, power=0.0, tone=700.0, space_shift=12000.0, wpm=12.0, lead=2.0, call="W4DQB/EXP",
                           monitor_device=None)
    return trx_board.Transceiver(a)


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get())
    return out


class Pane:
    """What the window does: holds the text after the sent mark, offers it, and marks what the transmitter took."""
    def __init__(self, trx):
        self.trx, self.text, self.acked = trx, "", 0

    def poll(self):
        n = self.trx.live_sent - self.acked
        self.text, self.acked = self.text[n:], self.acked + n
        self.trx.set_pending(self.text, self.acked)


def main():
    st = {"A": station(port_a), "B": station(port_b)}
    threads = [threading.Thread(target=s.start) for s in st.values()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for name, s in st.items():
        for line in drain(s.log):
            print(f"{name}: {line}")
    if not all(s.ready for s in st.values()):
        sys.exit("a board did not start")

    results = []
    for mode in modes:
        for tx, rx in (("A", "B"), ("B", "A")):
            for s in st.values():
                s.set_mode(mode)
            time.sleep(2.0)
            drain(st[rx].text)
            err = st[tx].start_live(2.0)
            if err:
                print(f"{mode} {tx}->{rx}: refused: {err}")
                continue
            pane, t0, steps, ended = Pane(st[tx]), time.monotonic(), list(SCRIPT), False
            while st[tx].sending and time.monotonic() - t0 < 180:
                el = time.monotonic() - t0
                while steps and steps[0][0] <= el:
                    _, what, arg = steps.pop(0)
                    pane.text = pane.text + arg if what == "type" else pane.text[:-arg]
                pane.poll()
                if not steps and not ended:
                    st[tx].end_live()
                    ended = True
                time.sleep(0.1)
            secs = time.monotonic() - t0
            time.sleep(4.0)
            got = "".join(drain(st[rx].text))
            for name, s in st.items():
                for line in drain(s.log):
                    print(f"  {name}: {line}")
            ok = WANT in " ".join(got.split())
            print(f"{mode:14s} {tx}->{rx}: {'OK ' if ok else 'BAD'} {secs:.0f} s on air, decoded {got!r}")
            results.append((mode, tx, rx, ok))

    for s in st.values():
        s.close()
    print("\nsummary")
    for mode, tx, rx, ok in results:
        print(f"  {mode:14s} {tx}->{rx}: {'OK' if ok else 'BAD'}")


if __name__ == "__main__":
    main()
