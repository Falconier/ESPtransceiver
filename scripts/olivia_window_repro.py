"""Window conditions for live Olivia: the mode is set once, the receivers run untouched between transmissions; the receiving
side's AFC and Olivia receiver state are printed every second.

  python scripts/olivia_window_repro.py [MODE] [ORDER]      (default olivia-8/250 ABAB: A->B, B->A, A->B, B->A)
"""
import sys
import threading
import time

sys.path.insert(0, ".")
from scripts.olivia_live_air import Pane, drain, station    # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "olivia-8/250"
order = sys.argv[2] if len(sys.argv) > 2 else "ABAB"
st = {"A": station("COM4"), "B": station("COM5")}
th = [threading.Thread(target=s.start) for s in st.values()]
[t.start() for t in th]
[t.join() for t in th]
for s in st.values():
    s.set_mode(mode)


def state(n):
    s = st[n]
    a, o = s.afc, s.olivia_rx
    return (f"{n}: afc {'L' if a.locked else '-'} shift {a.shift:+7.0f} peak {a.peak:7.0f} | olivia "
            f"{'L' if o.locked else '-'} df {o.df:+6.0f} q {o.quality:4.1f}")


def watch(secs, rx):
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        print(f"  {time.monotonic() - t0:5.1f}  {state(rx)}", flush=True)
        time.sleep(1.0)


watch(20, "B")                                         # idle, as when the windows sit there
for tx in order:
    rx = "B" if tx == "A" else "A"
    drain(st[rx].text)
    print(f"--- {tx}->{rx}, before: {state(rx)}")
    st[tx].start_live(2.0)
    pane = Pane(st[tx])
    pane.text = "CQ DE W4DQB/EXP TEST K"
    t0 = time.monotonic()
    while st[tx].sending:
        pane.poll()
        if time.monotonic() - t0 > 1:
            st[tx].end_live()
        if int((time.monotonic() - t0) * 10) % 10 == 0:
            print(f"  {time.monotonic() - t0:5.1f}  {state(rx)}", flush=True)
        time.sleep(0.1)
    watch(5, rx)
    print(f"{tx}->{rx}: decoded {''.join(drain(st[rx].text))!r}", flush=True)
    watch(10, rx)
for s in st.values():
    s.close()
