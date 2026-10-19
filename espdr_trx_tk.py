"""espdr_trx_tk: one window per ESP32-S3 board, each a half-duplex CW transceiver (send and receive on the same board).

Pick the board in the window that opens (or pass --port). Run the program twice, choosing a different board each time, and the two
boards work each other like two stations. The window is espdr_cw_tk's (frequency, RX/TX indicator, receive/decode, transmit/encode,
monitor); trx_board.Transceiver drives the board.

Transmitting needs an amateur radio licence (espdr-tx's licence confirmation, `espdr-tx --accept-licence`, once). The firmware
only transmits inside 2320 .. 2450 MHz.

  python espdr_trx_tk.py --call W4DQB/EXP
"""
import argparse
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import espdr_cw_tk
import trx_board

# Boards known from this project (chip MAC -> name). Others are listed too, with a warning: only ESP32-S3 boards run this firmware.
KNOWN = {"C4:9E:7E:07:44:88": "Board A", "C4:9E:7E:08:6F:10": "Board B"}


def label(port, mac):
    name = KNOWN.get(mac.upper())
    return f"{port}   {name or 'unknown board'}   ({mac or 'no serial'})"


class Selector(ttk.Frame):
    """Lists the ESP boards on native USB; calls on_pick(port, mac) for the chosen one."""

    def __init__(self, root, on_pick):
        super().__init__(root, padding=16)
        self.on_pick = on_pick
        ttk.Label(self, text="Select the board for this station", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(self, text="Only ESP32-S3 boards work. A board already used by another window is busy.",
                  foreground="gray40").pack(anchor="w", pady=(2, 10))
        self.choice = tk.StringVar()
        self.combo = ttk.Combobox(self, textvariable=self.choice, state="readonly", width=56)
        self.combo.pack(fill="x")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(row, text="Connect", command=self.pick).pack(side="right")
        self.refresh()

    def refresh(self):
        self.ports = trx_board.esp_ports()
        self.combo["values"] = [label(p, m) for p, m in self.ports]
        known = [i for i, (_, m) in enumerate(self.ports) if m.upper() in KNOWN]
        if self.ports:
            self.combo.current(known[0] if known else 0)

    def pick(self):
        i = self.combo.current()
        if i < 0:
            return
        port, mac = self.ports[i]
        if mac.upper() not in KNOWN and not messagebox.askyesno(
                "Unknown board", f"{port} ({mac}) is not one of the known ESP32-S3 boards.\n\n"
                                 "Loading the firmware resets it and only works on an ESP32-S3. Continue?"):
            return
        self.on_pick(port, mac)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="the board's native USB port; omit to choose in the window")
    p.add_argument("--image", default="release/esp32-sdr-trx-trx-decim16.bin", help="transceiver firmware (TX image, decimation 16)")
    p.add_argument("--dial", type=float, default=2394.250, help="start frequency in MHz (default 2394.250)")
    p.add_argument("--rx-gain", type=int, default=0, help="receiver gain selector (default 0: the boards are close)")
    p.add_argument("--rx-ppm", type=float, default=0.0)
    p.add_argument("--tx-ppm", type=float, default=0.0)
    p.add_argument("--power", type=float, default=0.0, help="transmit power in dB (0 .. -17.9)")
    p.add_argument("--tone", type=float, default=700.0, help="CW tone / AFC target in Hz (default 700)")
    p.add_argument("--space-shift", type=float, default=12000.0, help="carrier shift during spaces in Hz (default 12000)")
    p.add_argument("--wpm", type=float, default=12.0, help="sending speed and the decoder's starting speed (default 12)")
    p.add_argument("--lead", type=float, default=2.0, help="lead tone before each message in seconds (default 2)")
    p.add_argument("--call", help="your call sign, for Send ID")
    p.add_argument("--monitor-device", help="sound device for the monitor (default: Windows' default output)")
    args = p.parse_args()

    root = tk.Tk()
    root.title("ESP CW Station")

    def open_station(port, mac=""):
        args.port = port
        for w in root.winfo_children():
            w.destroy()
        radio = trx_board.Transceiver(args)
        espdr_cw_tk.Window(root, radio, args)
        root.title(f"ESP CW Station - {KNOWN.get(mac.upper(), port)} ({port})")
        threading.Thread(target=radio.start, daemon=True).start()

    if args.port:
        open_station(args.port, dict(trx_board.esp_ports()).get(args.port, ""))
    else:
        Selector(root, open_station).pack(fill="both", expand=True)
    root.mainloop()


if __name__ == "__main__":
    main()
