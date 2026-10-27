"""Both station windows on the real boards, driven by a script: Olivia mode in each, then typing into one window's transmit
pane and pressing TX / RX, in turn A->B and B->A. Prints each window's receive pane and status line, and any Tk callback error.

  python scripts/olivia_window_air.py [MODE]      (default olivia-8/250)
"""
import argparse
import sys
import threading
import tkinter as tk
import traceback

sys.path.insert(0, ".")
import espdr_cw_tk                                              # noqa: E402
import trx_board                                                # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "olivia-8/250"
name = {v: k for k, v in espdr_cw_tk.MODES.items()}[mode]
root = tk.Tk()
root.report_callback_exception = lambda *e: print("TK ERROR\n" + "".join(traceback.format_exception(*e)), flush=True)
win = {}
for label, port in (("A", "COM4"), ("B", "COM5")):
    a = argparse.Namespace(port=port, image="release/esp32-sdr-trx-trx-decim16.bin", dial=2394.250, rx_gain=0, rx_shift=4,
                           rx_ppm=0.0, tx_ppm=0.0, power=0.0, tone=700.0, space_shift=12000.0, wpm=12.0, lead=2.0,
                           call="W4DQB/EXP", monitor_device=None)
    top = root if label == "A" else tk.Toplevel(root)
    r = trx_board.Transceiver(a)
    win[label] = espdr_cw_tk.Window(top, r, a)
    threading.Thread(target=r.start, daemon=True).start()


def report(tag):
    for k, w in win.items():
        print(f"[{tag}] {k} status: {w.status.cget('text')!r}   stats: {w.stats.cget('text')!r}", flush=True)
        print(f"[{tag}] {k} rx pane: {w.rxtext.get('1.0', 'end-1c')!r}", flush=True)


plan = []


def at(t, f):
    plan.append((t, f))


def setup():
    if not all(w.radio.ready for w in win.values()):
        root.after(500, setup)
        return
    for w in win.values():
        w.mode_name.set(name)
        w.set_mode()
    t = 3000
    for tx in "ABAB":
        w = win[tx]
        at(t, lambda w=w: (w.txtext.insert("end", "CQ DE W4DQB/EXP "), w.push_pending(), w.live_tx()))
        at(t + 4000, lambda w=w: (w.txtext.insert("end", "TEST K"), w.push_pending(), w.live_rx()))
        at(t + 32000, lambda tx=tx: report(f"after {tx} sent"))
        t += 36000
    at(t, root.destroy)
    for dt, f in plan:
        root.after(dt, f)


root.after(500, setup)
root.mainloop()
for w in win.values():
    w.radio.close()
