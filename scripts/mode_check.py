"""Live check of every station mode between two boards: each sends to the other, the other decodes.

  python scripts/mode_check.py [PORT_A] [PORT_B] [MODES...]      (default COM4 COM5, all modes)

Text modes print what was decoded; Feld Hell saves the received columns as captures/check_hell_<from>_to_<to>.png.
"""
import argparse
import sys
import threading
import time

sys.path.insert(0, ".")
import trx_board                                                 # noqa: E402
from scripts.hell_png import save_png                           # noqa: E402

port_a = sys.argv[1] if len(sys.argv) > 1 else "COM4"
port_b = sys.argv[2] if len(sys.argv) > 2 else "COM5"
modes = sys.argv[3:] or ["cw", "hell", "olivia-8/250", "olivia-16/500"]
TEXT = "CQ DE W4DQB/EXP"
DIAL = float(__import__("os").environ.get("DIAL_MHZ", "2304.350"))   # DIAL_MHZ=2394.250 to test another frequency


def station(port):
    a = argparse.Namespace(port=port, image="release/esp32-sdr-trx-trx-decim16.bin", dial=DIAL, rx_gain=0, rx_ppm=0.0,
                           tx_ppm=0.0, power=0.0, tone=700.0, space_shift=12000.0, wpm=12.0, lead=2.0, call="W4DQB/EXP",
                           monitor_device=None)
    return trx_board.Transceiver(a)


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get())
    return out


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
        time.sleep(2.0)                                          # receivers settle (AFC history, decoder state)
        drain(st[rx].text), drain(st[rx].hell_cols)
        err = st[tx].send(TEXT, 12.0, 2.0, True)
        if err:
            print(f"{mode} {tx}->{rx}: send refused: {err}")
            continue
        t0 = time.monotonic()
        while st[tx].sending and time.monotonic() - t0 < 180:
            time.sleep(0.2)
        time.sleep(4.0 if mode.startswith("olivia") else 2.0)   # decoder latency
        got = "".join(drain(st[rx].text))
        cols = drain(st[rx].hell_cols)
        for name, s in st.items():
            for line in drain(s.log):
                print(f"  {name}: {line}")
        if mode == "hell":
            path = f"captures/check_hell_{tx}_to_{rx}.png"
            if cols:
                save_png(cols, path)
            print(f"{mode:14s} {tx}->{rx}: {len(cols)} columns -> {path}")
            results.append((mode, tx, rx, f"{len(cols)} columns", None))
        else:
            ok = TEXT in " ".join(got.split())
            print(f"{mode:14s} {tx}->{rx}: {'OK ' if ok else 'BAD'} {got!r}")
            results.append((mode, tx, rx, got, ok))

for s in st.values():
    s.close()
print("\nsummary")
for mode, tx, rx, got, ok in results:
    print(f"  {mode:14s} {tx}->{rx}: {'see PNG' if ok is None else 'OK' if ok else 'BAD'}")
