"""espdr_cw_tk: a CW station for two ESP32-S3 boards (esp32-sdr-trx) in a Tk window, with its own Morse decoder.

Receive: board A runs espdr-rx (62.5 ksps with the decimation-16 firmware); espdr_fldigi's chain (USB demodulator, AFC that keeps
the carrier at 700 Hz) feeds cw_decoder. Transmit: typed text is keyed on board B, after an optional steady lead tone, with the
carrier moved away during spaces (the chip's key-up is only 18 dB down). Board A keeps listening while B sends, so your own
transmission shows up decoded: an end-to-end check.

Transmitting needs an amateur radio licence (espdr-tx's licence confirmation, `espdr-tx --accept-licence`, once). The firmware
only transmits inside 2300 .. 2450 MHz.

  python espdr_cw_tk.py --call W4DQB/EXP
"""
import argparse
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

import numpy as np


import espdr_fldigi as br
import hell
from cw_decoder import MORSE, CwDecoder

MODES = {"CW": "cw", "Feld Hell": "hell", "Olivia 8/250": "olivia-8/250", "Olivia 16/500": "olivia-16/500"}
OLIVIA_CHARS = {chr(c) for c in range(32, 127)}   # Olivia carries 7-bit ASCII, lower case included
HELL_ROW_PX, HELL_COL_PX = 2, 6           # a glyph row is 4 receive slots, a glyph column 1 slot wide: keep letters near their shape

BAND_MHZ = (2300.005, 2449.995)
ENCODE = {ch: code for code, ch in MORSE.items() if len(ch) == 1}


class Radio:
    """The two boards behind a small interface. Text and log lines come out through queues; nothing here touches Tk."""

    def __init__(self, args):
        self.a = args
        self.text = queue.Queue()               # decoded characters
        self.log = queue.Queue()                # status lines
        self.decoder = CwDecoder(rate=br.AUDIO_RATE, tone=args.tone, wpm=args.wpm, emit=self.text.put)
        self.rx = self.tx = self.server = None
        self.monitor = None                     # br.AudioOut while the monitor is on
        self.volume = 0.5
        self.ready = False
        self.sending = False
        self.dial_mhz = args.dial

    def start(self):
        """Loads the firmware and connects both boards (takes some seconds: run it off the Tk thread)."""
        a = self.a
        try:
            br.RTL_RATE = br.DECIM_RATES[a.decim]
            self.log.put(f"starting the receiver on {a.rx_port} ...")
            cmd = [sys.executable, "-c", "from espdr.cli_rx import main; main()", "--native", "--port", a.rx_port,
                   "--gain", str(a.rx_gain), "--ppm", str(a.rx_ppm), "--listen", a.rtl, "--decim", str(a.decim)]
            if a.rx_image:
                cmd += ["--image", a.rx_image]
            self.server = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in self.server.stdout:
                if "rtl_tcp server" in line:
                    break
                if "espdr-rx:" in line or "Error" in line:
                    raise RuntimeError(line.strip())
            threading.Thread(target=lambda: [None for _ in self.server.stdout], daemon=True).start()
            host, _, port = a.rtl.rpartition(":")
            self.rx = br.RtlClient((host, int(port)), self.dial_mhz * 1e6, self._audio, threading.Event())
            self.rx.afc = br.RxAfc(a.tone)
            self.rx.start()
            self.log.put(f"receiving on {self.dial_mhz:.3f} MHz")
            if a.tx_port:
                self.log.put(f"starting the transmitter on {a.tx_port} ...")
                self.tx = br.Transmitter(a.tx_port, self.dial_mhz * 1e6, a.tx_ppm, a.power, a.tone, a.space_shift)
                self.space_hz = self.tx.space_hz
            self.ready = True
            self.log.put("ready" + ("" if self.tx else " (receive only)"))
        except (RuntimeError, SystemExit, OSError) as e:
            self.log.put(f"start failed: {e}")

    def _audio(self, x):
        """Receive audio (48 kHz) from the RX thread: to the decoder, and to the speakers while the monitor is on."""
        self.decoder.process(x)
        m = self.monitor
        if m is not None:
            m.write((np.asarray(x) * self.volume).astype(np.float32))

    def set_monitor(self, on):
        """Plays the receive audio (noise and CW) on the sound device --monitor-device (default: Windows' default output)."""
        if on and self.monitor is None:
            try:
                self.monitor = br.AudioOut(br.find_device(self.a.monitor_device, False) if self.a.monitor_device else None)
            except Exception as e:                               # noqa: BLE001 - shown in the window
                return f"monitor: {e}"
        elif not on and self.monitor is not None:
            m, self.monitor = self.monitor, None
            m.close()
        return None

    def set_frequency(self, mhz):
        if self.sending:
            return "wait until the transmission ends"
        if not BAND_MHZ[0] <= mhz <= BAND_MHZ[1]:
            return f"outside {BAND_MHZ[0]:.3f} .. {BAND_MHZ[1]:.3f} MHz"
        if self.tx:
            try:
                self.tx.set_dial(mhz * 1e6, abs(self.space_hz))
            except ValueError as e:
                return str(e)
            self.space_hz = self.tx.space_hz
        if self.rx:
            self.rx.retune(mhz * 1e6)
        self.dial_mhz = mhz
        return None

    def send(self, text, wpm, lead_s, shift_spaces):
        """Starts one transmission in the background: a lead tone (if lead_s), then the Morse for `text` (if any)."""
        if not self.tx:
            return "no transmitter"
        if self.sending:
            return "already transmitting"
        self.sending = True
        threading.Thread(target=self._send, args=(text, wpm, lead_s, shift_spaces), daemon=True).start()
        return None

    def _send(self, text, wpm, lead_s, shift_spaces):
        rate, tone = br.TX_RATE, self.a.tone
        parts = []
        if lead_s:
            t = np.arange(int(lead_s * rate)) / rate
            parts.append(0.5 * np.minimum(1, np.minimum(t, t[::-1]) / 0.005) * np.sin(2 * np.pi * tone * t))
            if text:
                parts.append(np.zeros(rate // 2))
        if text:
            parts.append(br.morse_audio(text, wpm=wpm, tone=tone))
        audio = np.concatenate(parts)
        self.tx.space_hz = self.space_hz if shift_spaces else 0.0
        self.log.put(f"TX {self.dial_mhz:.3f} MHz: " + (repr(text) + f" at {wpm:g} WPM" if text else f"{lead_s:g} s tone"))
        try:
            self.tx.transmit((audio[i:i + 160] for i in range(0, len(audio), 160)), threading.Event())
        except Exception as e:                                   # noqa: BLE001 - shown in the window
            self.log.put(f"transmit failed: {e}")
        finally:
            self.sending = False

    def close(self):
        self.set_monitor(False)
        if self.rx:
            self.rx.stop.set()
        if self.server:
            self.server.terminate()


class Window:
    def __init__(self, root, radio, args):
        self.root, self.radio, self.args = root, radio, args
        root.title("ESP CW Station")
        root.minsize(640, 520)
        pad = dict(padx=8, pady=4)

        top = ttk.Frame(root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Frequency (MHz)").pack(side="left")
        self.freq = tk.StringVar(value=f"{args.dial:.3f}")
        spin = ttk.Spinbox(top, from_=BAND_MHZ[0], to=BAND_MHZ[1], increment=0.001, format="%.3f", width=10,
                           textvariable=self.freq)
        spin.pack(side="left", padx=4)
        spin.bind("<Return>", lambda e: self.apply_freq())
        ttk.Button(top, text="Apply", command=self.apply_freq).pack(side="left")
        self.mode_name = tk.StringVar(value="CW")
        if hasattr(radio, "set_mode"):                   # the per-board transceiver has modes; the two-board station is CW only
            ttk.Label(top, text="Mode").pack(side="left", padx=(16, 4))
            mode_box = ttk.Combobox(top, textvariable=self.mode_name, values=list(MODES), state="readonly", width=10)
            mode_box.pack(side="left")
            mode_box.bind("<<ComboboxSelected>>", lambda e: self.set_mode())
        self.mode = tk.Label(top, text="OFF", width=5, font=("Segoe UI", 14, "bold"), fg="white", bg="gray40")
        self.mode.pack(side="right")
        self.keylight = tk.Canvas(top, width=18, height=18, highlightthickness=0)
        self.keylight.pack(side="right", padx=8)
        self.keydot = self.keylight.create_oval(2, 2, 16, 16, fill="gray70", outline="")
        ttk.Label(top, text="key").pack(side="right")

        rxf = ttk.LabelFrame(root, text="Receive / decode")
        rxf.pack(fill="both", expand=True, **pad)
        self.rxtext = tk.Text(rxf, height=12, wrap="word", font=("Consolas", 13), state="disabled")
        sb = ttk.Scrollbar(rxf, command=self.rxtext.yview)
        self.rxtext.configure(yscrollcommand=sb.set)
        self.rxtext.tag_configure("tx", foreground="#c2410c")
        self.rxtext.tag_configure("note", foreground="gray50")
        sb.pack(side="right", fill="y")
        self.rxtext.pack(fill="both", expand=True, padx=4, pady=4)
        # Feld Hell display (shown instead of the text in Hell mode): two stacked copies of each received column, newest at right
        self.hell_canvas = tk.Canvas(rxf, height=2 * hell.ROWS_RX * HELL_ROW_PX, bg="white", highlightthickness=0)
        self.hell_item = self.hell_canvas.create_image(0, 0, anchor="nw")
        self.hell_data = np.zeros((0, hell.ROWS_RX))
        self.hell_photo = None
        row = ttk.Frame(rxf)
        row.pack(fill="x", padx=4, pady=(0, 4))
        self.rx_row = row
        self.stats = ttk.Label(row, text="speed - WPM   signal - dB   tone - Hz")
        self.stats.pack(side="left")
        # Hell column phase: automatic re-alignment on our lead tone, plus a manual shift (receive slots) for everything shown
        self.hell_ctl = ttk.Frame(self.rx_row)
        hrx = getattr(radio, "hell_rx", None)
        self.hell_auto = tk.BooleanVar(value=bool(hrx and hrx.auto))
        ttk.Checkbutton(self.hell_ctl, text="Auto phase", variable=self.hell_auto,
                        command=lambda: setattr(hrx, "auto", self.hell_auto.get()) if hrx else None).pack(side="left")
        ttk.Label(self.hell_ctl, text="Phase").pack(side="left", padx=(10, 4))
        self.hell_phase = tk.IntVar(value=0)
        ttk.Scale(self.hell_ctl, from_=0, to=hell.ROWS_RX - 1, length=140, variable=self.hell_phase,
                  command=lambda v: (self.hell_phase.set(round(float(v))), self.draw_hell())).pack(side="left")
        ttk.Button(row, text="Clear", command=self.clear_rx).pack(side="right")
        self.vol = tk.DoubleVar(value=radio.volume * 100)
        ttk.Scale(row, from_=0, to=100, variable=self.vol, length=110,
                  command=lambda v: setattr(radio, "volume", float(v) / 100)).pack(side="right", padx=(4, 12))
        ttk.Label(row, text="Vol").pack(side="right")
        self.mon = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Monitor", variable=self.mon, command=self.toggle_monitor).pack(side="right", padx=(12, 6))

        txf = ttk.LabelFrame(root, text="Transmit / encode")
        txf.pack(fill="x", **pad)
        self.txf = txf
        # Olivia, as in fldigi: type into the transmit pane before or while transmitting. Text in grey has gone out; the
        # rest is sent block by block, and can still be changed until then.
        self.live = ttk.Frame(txf)
        self.txtext = tk.Text(self.live, height=4, wrap="word", font=("Consolas", 13), undo=False)
        tsb = ttk.Scrollbar(self.live, command=self.txtext.yview)
        self.txtext.configure(yscrollcommand=tsb.set)
        self.txtext.tag_configure("sent", foreground="gray55")
        self.txtext.mark_set("sent", "1.0")
        self.txtext.mark_gravity("sent", "left")
        self.txtext.bind("<Key>", self.tx_key)
        self.txtext.bind("<KeyRelease>", lambda e: self.push_pending())
        self.txtext.bind("<Control-t>", lambda e: (self.live_tx(), "break")[1])
        self.txtext.bind("<Control-r>", lambda e: (self.live_rx(), "break")[1])
        self.txtext.bind("<Escape>", lambda e: (self.live_abort(), "break")[1])
        lbtn = ttk.Frame(self.live)
        lbtn.pack(side="right", fill="y", padx=(6, 0))
        self.txbtn = ttk.Button(lbtn, text="TX", width=7, command=self.live_tx)
        self.txbtn.pack(fill="x")
        self.rxbtn = ttk.Button(lbtn, text="RX", width=7, command=self.live_rx)
        self.rxbtn.pack(fill="x", pady=2)
        self.abortbtn = ttk.Button(lbtn, text="Abort", width=7, command=self.live_abort)
        self.abortbtn.pack(fill="x")
        ttk.Button(lbtn, text="Clear", width=7, command=self.clear_tx).pack(fill="x", pady=(2, 0))
        tsb.pack(side="right", fill="y")
        self.txtext.pack(side="left", fill="both", expand=True)
        self.live_on = False                     # a live transmission from this pane is running
        self.acked = 0                           # characters of it marked as sent
        line = ttk.Frame(txf)
        line.pack(fill="x", padx=4, pady=4)
        self.line = line
        self.msg = tk.StringVar()
        self.msg.trace_add("write", lambda *_: self.preview())
        entry = ttk.Entry(line, textvariable=self.msg, font=("Consolas", 13))
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self.send())
        self.sendbtn = ttk.Button(line, text="Send", command=self.send)
        self.sendbtn.pack(side="left", padx=(6, 0))
        self.code = ttk.Label(txf, text="", font=("Consolas", 11), foreground="gray40")
        self.code.pack(fill="x", padx=4)
        opts = ttk.Frame(txf)
        opts.pack(fill="x", padx=4, pady=4)
        ttk.Label(opts, text="WPM").pack(side="left")
        self.wpm = tk.StringVar(value=f"{args.wpm:g}")
        ttk.Spinbox(opts, from_=5, to=30, increment=1, width=4, textvariable=self.wpm).pack(side="left", padx=(4, 12))
        self.lead = tk.BooleanVar(value=args.lead > 0)
        ttk.Checkbutton(opts, text=f"{args.lead or 2:g} s lead tone", variable=self.lead).pack(side="left")
        self.shift = tk.BooleanVar(value=args.space_shift != 0)
        ttk.Checkbutton(opts, text="Shift carrier in spaces", variable=self.shift).pack(side="left", padx=12)
        ttk.Label(opts, text="Call").pack(side="left")
        self.call = tk.StringVar(value=args.call or "")
        ttk.Entry(opts, textvariable=self.call, width=12).pack(side="left", padx=4)
        ttk.Button(opts, text="Send ID", command=self.send_id).pack(side="left", padx=4)
        ttk.Button(opts, text="Tune 2 s", command=self.tune).pack(side="left")
        self.beacon_btn = None
        if hasattr(radio, "start_beacon"):              # the per-board station: CW beacon (carrier with the ID every period)
            brow = ttk.Frame(txf)
            brow.pack(fill="x", padx=4, pady=(0, 4))
            ttk.Label(brow, text="Beacon: ID every").pack(side="left")
            self.beacon_period = tk.StringVar(value="30")
            ttk.Spinbox(brow, from_=10, to=600, increment=5, width=5, textvariable=self.beacon_period).pack(side="left", padx=4)
            ttk.Label(brow, text="s, carrier between, at").pack(side="left")
            self.beacon_wpm = tk.StringVar(value="20")
            ttk.Spinbox(brow, from_=5, to=30, increment=1, width=4, textvariable=self.beacon_wpm).pack(side="left", padx=4)
            ttk.Label(brow, text="WPM").pack(side="left")
            self.beacon_btn = ttk.Button(brow, text="Start beacon", command=self.toggle_beacon)
            self.beacon_btn.pack(side="left", padx=12)

        self.status = ttk.Label(root, text="starting ...", relief="sunken", anchor="w")
        self.status.pack(fill="x", side="bottom")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll()

    # -- receive side
    def append(self, s, tag=None):
        self.rxtext.configure(state="normal")
        self.rxtext.insert("end", s, tag)
        self.rxtext.see("end")
        self.rxtext.configure(state="disabled")

    def toggle_monitor(self):
        err = self.radio.set_monitor(self.mon.get())
        if err:
            self.mon.set(False)
            self.say(err)
        else:
            self.say("monitor on" if self.mon.get() else "monitor off")

    def clear_rx(self):
        self.rxtext.configure(state="normal")
        self.rxtext.delete("1.0", "end")
        self.rxtext.configure(state="disabled")

    # -- transmit side
    def cur_mode(self):
        return MODES[self.mode_name.get()]

    def set_mode(self):
        err = self.radio.set_mode(self.cur_mode())
        if err:
            self.say(err)
            return
        if self.cur_mode().startswith("olivia-") and hasattr(self.radio, "start_live"):
            self.line.pack_forget()
            self.live.pack(fill="x", padx=4, pady=4, before=self.code)
            self.txtext.focus_set()
        else:
            self.live.pack_forget()
            self.line.pack(fill="x", padx=4, pady=4, before=self.code)
        if self.cur_mode() == "hell":
            self.rxtext.pack_forget()
            self.hell_canvas.pack(fill="both", expand=True, padx=4, pady=4, before=self.rx_row)
            self.hell_ctl.pack(side="left", padx=(16, 0), after=self.stats)
        else:
            self.hell_canvas.pack_forget()
            self.hell_ctl.pack_forget()
            self.rxtext.pack(fill="both", expand=True, padx=4, pady=4, before=self.rx_row)
        self.preview()
        self.say(f"mode {self.mode_name.get()}")

    # -- Olivia transmit pane
    def tx_key(self, e):
        """Keeps the text that has gone out as it is: typing there goes to the end instead, deleting into it is refused."""
        t = self.txtext
        if e.keysym in ("Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next") or e.state & 0x4:
            return None
        sel = t.tag_ranges("sel")
        if sel and t.compare(sel[0], "<", "sent"):
            if e.keysym in ("BackSpace", "Delete"):
                return "break"
            t.tag_remove("sel", "1.0", "end")
            t.mark_set("insert", "end")
        elif t.compare("insert", "<", "sent") or (e.keysym == "BackSpace" and t.compare("insert", "==", "sent")):
            if e.keysym in ("BackSpace", "Delete"):
                return "break"
            t.mark_set("insert", "end")
        if e.char and e.keysym not in ("BackSpace", "Delete") and e.char not in "\r\n\t" and not 32 <= ord(e.char) < 127:
            self.say("Olivia carries 7-bit ASCII only")
            return "break"
        return None

    def pending_text(self):
        return self.txtext.get("sent", "end-1c")

    def push_pending(self):
        if self.live_on:
            self.radio.set_pending(self.pending_text(), self.acked)
        self.preview()

    def live_tx(self):
        if self.live_on:
            return
        err = self.radio.start_live(self.args.lead if self.lead.get() else 0)
        if err:
            self.say(err)
            return
        self.live_on, self.acked = True, 0
        self.push_pending()
        self.append("\n", "tx")

    def live_rx(self):
        if self.live_on:
            self.radio.end_live()
            self.say("ending after the typed text (idle blocks are not sent any more)")

    def live_abort(self):
        if self.live_on:
            self.radio.abort_live()
            self.say("aborting: the block on its way still goes out")

    def clear_tx(self):
        if self.live_on:                          # keep what is waiting to go out
            self.txtext.delete("1.0", "sent")
        else:
            self.txtext.delete("1.0", "end")
            self.txtext.mark_set("sent", "1.0")

    def live_poll(self):
        """Marks the characters the transmitter took as sent (and echoes them in the receive pane, as fldigi does)."""
        r = self.radio
        n = getattr(r, "live_sent", 0) - self.acked
        if self.live_on and n > 0:
            t = self.txtext
            new = t.index(f"sent + {n} chars")
            self.append(t.get("sent", new), "tx")
            t.tag_add("sent", "sent", new)
            t.mark_set("sent", new)
            self.acked += n
            self.push_pending()
        if self.live_on and not r.sending:
            self.live_on = False
            self.append("\n", "tx")
            left = len(self.pending_text())
            self.say("transmission over" + (f": {left} characters not sent" if left else ""))
        state = "disabled" if not r.ready else "normal"
        self.txbtn.configure(state="disabled" if r.sending or not r.tx else state)
        self.rxbtn.configure(state="normal" if self.live_on else "disabled")
        self.abortbtn.configure(state="normal" if self.live_on else "disabled")

    def draw_hell(self):
        width = max(self.hell_canvas.winfo_width(), 200)
        n = width // HELL_COL_PX
        self.hell_data = self.hell_data[-n:]
        a = np.zeros((n, hell.ROWS_RX))
        if len(self.hell_data):
            a[-len(self.hell_data):] = self.hell_data
        a = np.roll(a, -self.hell_phase.get(), axis=1)          # manual phase: move the image down by that many slots
        g =(255 * (1 - np.clip(a.T[::-1], 0, 1))).astype(np.uint8)     # rows top-down, ink dark on white
        g = np.kron(np.vstack([g, g]), np.ones((HELL_ROW_PX, HELL_COL_PX), np.uint8))
        h, w = g.shape
        pgm = f"P5 {w} {h} 255\n".encode() + g.tobytes()
        self.hell_photo = tk.PhotoImage(data=pgm, format="PPM")      # Tk 8.6 takes raw PGM bytes (not base64)
        self.hell_canvas.itemconfigure(self.hell_item, image=self.hell_photo)

    def preview(self):
        if self.cur_mode().startswith("olivia-"):
            import olivia
            live = self.live.winfo_manager() != ""
            text = self.pending_text() if live else " ".join(self.msg.get().split())
            m = olivia.Mode(self.cur_mode().split("-", 1)[1])
            if live:
                secs = -(-len(text) // m.bits) * olivia.BLOCK * m.symbol_s
                self.code.configure(text=f"Olivia {m.name}: {len(text)} characters to send, {secs:.1f} s   "
                                         "(Ctrl+T transmit, Ctrl+R back to receive after the text, Esc abort)")
                return
            secs = -(-len(text) // m.bits) * olivia.BLOCK * m.symbol_s if text else 0
            self.code.configure(text=f"Olivia {m.name}: {len(text)} characters, {secs:.1f} s after the lead tone" if text else "")
            return
        if self.cur_mode() == "hell":
            text = " ".join(self.msg.get().upper().split())
            self.code.configure(text=f"Feld Hell: {len(text)} characters, {hell.seconds(text):.1f} s" if text else "")
            return
        words = self.msg.get().upper().split()
        self.code.configure(text="  /  ".join(" ".join(ENCODE.get(c, "?") for c in w) for w in words))

    def _wpm(self):
        try:
            return min(30.0, max(5.0, float(self.wpm.get())))
        except ValueError:
            return self.args.wpm

    def _start_tx(self, text, lead_s):
        olivia_mode = self.cur_mode().startswith("olivia-")
        text = " ".join(text.split() if olivia_mode else text.upper().split())
        alphabet, what = ((OLIVIA_CHARS, "not 7-bit ASCII") if olivia_mode else
                          (hell.CHARS, "no Hell glyph for") if self.cur_mode() == "hell" else (ENCODE, "no Morse for"))
        bad = sorted({c for c in text if c != " " and c not in alphabet})
        if bad:
            self.say(f"{what}: " + " ".join(bad))
            return False
        wpm = self._wpm()
        self.radio.decoder.set_wpm(wpm)              # we hear ourselves: start the decoder at the right speed
        err = self.radio.send(text, wpm, lead_s, self.shift.get())
        if err:
            self.say(err)
            return False
        self.append(("\n" if text else "") + (f"[TX] {text}\n" if text else "[TX tune]\n"), "tx")
        return True

    def send(self):
        if self._start_tx(self.msg.get(), self.args.lead if self.lead.get() else 0):
            self.msg.set("")

    def send_id(self):
        call = self.call.get().strip().upper()
        if not call:
            self.say("enter your call sign first")
            return
        if self.live.winfo_manager():              # Olivia: into the transmit pane, as an fldigi macro would
            t = self.txtext
            t.insert("end", ("" if t.get("end-2c", "end-1c") in ("", " ", "\n") else " ") + f"DE {call} ")
            t.see("end")
            if not self.live_on:
                self.live_tx()
                self.radio.end_live()
            self.push_pending()
            return
        self._start_tx(f"DE {call}", self.args.lead if self.lead.get() else 0)

    def toggle_beacon(self):
        if self.radio.beacon_stop is not None:
            self.radio.stop_beacon()
            self.say("stopping the beacon (the buffer plays out first)")
            return
        call = self.call.get().strip().upper()
        if not call:
            self.say("enter your call sign first: the beacon sends DE <call>")
            return
        try:
            period, wpm = float(self.beacon_period.get()), float(self.beacon_wpm.get())
        except ValueError:
            self.say("period and WPM must be numbers")
            return
        err = self.radio.start_beacon(f"DE {call}", wpm, period, self.shift.get())
        if err:
            self.say(err)
            return
        self.append(f"\n[beacon: DE {call} at {wpm:g} WPM every {period:g} s, carrier between]\n", "tx")

    def tune(self):
        self._start_tx("", 2.0)

    def apply_freq(self):
        try:
            mhz = round(float(self.freq.get()), 6)
        except ValueError:
            self.say("not a frequency")
            return
        err = self.radio.set_frequency(mhz)
        if err:
            self.say(err)
            return
        self.append(f"\n[{mhz:.3f} MHz]\n", "note")
        self.say(f"tuned to {mhz:.3f} MHz")

    # -- housekeeping
    def say(self, s):
        self.status.configure(text=s)

    def poll(self):
        r = self.radio
        cols = getattr(r, "hell_cols", None)
        if cols is not None and not cols.empty():
            new = []
            while not cols.empty():
                new.append(cols.get_nowait())
            self.hell_data = np.vstack([self.hell_data, np.array(new)])
            if self.cur_mode() == "hell":
                self.draw_hell()
        while not r.text.empty():
            self.append(r.text.get_nowait())
        self.live_poll()
        while not r.log.empty():
            self.say(r.log.get_nowait())
        if r.sending:
            self.mode.configure(text="TX", bg="#dc2626")
        elif r.ready:
            self.mode.configure(text="RX", bg="#16a34a")
        self.sendbtn.configure(state="disabled" if r.sending or not r.tx else "normal")
        if self.beacon_btn is not None:
            on = r.beacon_stop is not None
            self.beacon_btn.configure(text="Stop beacon" if on else "Start beacon",
                                      state="normal" if on or (r.tx and not r.sending) else "disabled")
        d = r.decoder
        self.keylight.itemconfigure(self.keydot, fill="#f59e0b" if d.key else "gray70")
        afc = r.rx.afc if r.rx else None
        tone = f"{afc.target:.0f}" if afc and afc.locked else "-"
        orx = getattr(r, "olivia_rx", None)
        if orx is not None and self.cur_mode().startswith("olivia-"):
            state = f"locked, offset {orx.df:+.0f} Hz" if orx.locked else "searching"
            self.stats.configure(text=f"Olivia {orx.m.name}: {state}   block quality {orx.quality:4.1f}")
        else:
            self.stats.configure(text=f"speed {d.wpm:4.1f} WPM   signal {d.snr_db:4.1f} dB   tone {tone} Hz")
        self.root.after(100, self.poll)

    def close(self):
        self.radio.close()
        self.root.destroy()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dial", type=float, default=2394.250, help="start frequency in MHz (default 2394.250)")
    p.add_argument("--rx-port", default="COM4", help="receiver board's native USB port (default COM4)")
    p.add_argument("--tx-port", default="COM5", help="transmitter board's native USB port (default COM5; '' for receive only)")
    p.add_argument("--decim", type=int, choices=sorted(br.DECIM_RATES), default=16, help="receiver decimation (default 16)")
    p.add_argument("--rx-image", default="release/esp32-sdr-trx-rx-decim16.bin", help="receiver firmware image")
    p.add_argument("--rx-gain", type=int, default=0, help="receiver gain selector (default 0: the boards are close)")
    p.add_argument("--rx-ppm", type=float, default=0.0)
    p.add_argument("--tx-ppm", type=float, default=0.0)
    p.add_argument("--power", type=float, default=0.0, help="transmit power in dB (0 .. -17.9)")
    p.add_argument("--tone", type=float, default=700.0, help="CW tone / AFC target in Hz (default 700)")
    p.add_argument("--space-shift", type=float, default=12000.0, help="carrier shift during spaces in Hz (default 12000)")
    p.add_argument("--wpm", type=float, default=12.0, help="sending speed and the decoder's starting speed (default 12)")
    p.add_argument("--lead", type=float, default=2.0, help="lead tone before each message in seconds (default 2)")
    p.add_argument("--call", help="your call sign, for Send ID")
    p.add_argument("--rtl", default="127.0.0.1:1234", help="address for espdr-rx's rtl_tcp server")
    p.add_argument("--monitor-device", help="sound device for the monitor (name part or number; default: Windows' default output)")
    args = p.parse_args()

    radio = Radio(args)
    root = tk.Tk()
    Window(root, radio, args)
    threading.Thread(target=radio.start, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
