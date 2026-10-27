"""One ESP32-S3 board as a half-duplex CW transceiver, over one USB link.

The esp32-sdr-trx transmitter image also holds the narrowband receiver (build flags 3) and sets the receiver up again after every
transmission, so one board can do both. Built with decimation 16 (release/esp32-sdr-trx-trx-decim16.bin) it streams 62.5 ksps,
which Windows' USB serial driver carries without loss.

Receive: a thread keeps the board streaming (nb.Link.run) and only queues the packets; a second thread does the DSP: conjugate (the
radio delivers LO minus RF), DC removal, the LO's fine offset, then espdr_fldigi's AFC and USB demodulator, then the CW decoder and
the speaker monitor. Transmit: the receive stream is stopped, a transmitter session runs on the same link, then receiving resumes.

The class offers what espdr_cw_tk.Window uses, so that window can drive it.
"""
import queue
import threading
import time

import numpy as np

import espdr_fldigi as br
import hell
import olivia
import olivia_rx
from cw_decoder import CwDecoder
from espdr import board, nb, txlink, txmodes

DECIM = 16
DC_TAU_S = 0.5
BAND_MHZ = (2300.005, 2449.995)


def esp_ports():
    """Native USB ports of ESP32-S3 boards: [(port, serial number = chip MAC)]."""
    from serial.tools import list_ports
    return sorted((p.device, p.serial_number or "") for p in list_ports.comports() if (p.vid, p.pid) == board.NATIVE)


class Transceiver:
    def __init__(self, args):
        self.a = args
        self.text = queue.Queue()
        self.log = queue.Queue()
        self.decoder = CwDecoder(rate=br.AUDIO_RATE, tone=args.tone, wpm=args.wpm, emit=self.text.put)
        self.dial_mhz = args.dial
        self.volume = 0.5
        self.monitor = None
        self.link = None
        self.rx = self.tx = None                 # both set to self once running (the window checks them)
        self.afc = None
        self.ready = False
        self.sending = False
        self.stopping = threading.Event()
        self.hold = threading.Event()            # set while transmitting: the receive loop leaves link.run and waits
        self.idle = threading.Event()            # set while the receive loop is outside link.run
        self.restart = False                     # retune: leave link.run once, reconfigure
        self.packets = queue.Queue(maxsize=2000)
        self.space_hz = 0.0
        self.mode = "cw"                         # "cw" or "hell" (Feld Hellschreiber)
        self.hell_cols = queue.Queue()           # received Hell image columns for the window
        self.hell_rx = hell.HellRx(rate=br.AUDIO_RATE, tone=args.tone, emit=self.hell_cols.put)
        self.olivia_rx = None                    # olivia_rx.OliviaRx in the Olivia modes
        self.beacon_stop = None                  # threading.Event while a beacon runs

    # -- start
    def start(self):
        a = self.a
        try:
            br.RTL_RATE = br.DECIM_RATES[DECIM]
            self.log.put(f"loading the transceiver firmware on {a.port} (about 8 s) ...")
            board.ensure(board.TX, port=a.port, native=True, load="always", image=a.image, log=lambda m: None)
            self.link = nb.open_link(a.port)
            self.afc = br.RxAfc(a.tone, space_hz=abs(a.space_shift))
            self.demod = br.UsbDemod()
            self.tx_obj = self._make_transmitter()
            self.rx = self.tx = self
            threading.Thread(target=self._rx_loop, daemon=True).start()
            threading.Thread(target=self._dsp_loop, daemon=True).start()
            self.ready = True
            self.log.put(f"{a.port}: receiving on {self.dial_mhz:.3f} MHz")
        except (RuntimeError, SystemExit, OSError, board.BoardError) as e:
            self.log.put(f"start failed: {e}")

    def _make_transmitter(self):
        """A br.Transmitter on this board's open link (its constructor would open the port a second time)."""
        if not br.cli_tx._licence_accepted():
            raise RuntimeError("transmitting needs the espdr-tx licence confirmation (espdr-tx --accept-licence, once)")
        t = br.Transmitter.__new__(br.Transmitter)
        t.power_db, t.tone_hz, t.ppm, t.space_hz = self.a.power, self.a.tone, self.a.tx_ppm, 0.0
        t.set_dial(self.dial_mhz * 1e6, self.a.space_shift)
        t.session = txlink.Session(self.link)
        self.space_hz = t.space_hz
        return t

    # -- receive
    def _rx_loop(self):
        a = self.a
        while not self.stopping.is_set():
            if self.hold.is_set():
                self.idle.set()
                time.sleep(0.05)
                continue
            self.idle.clear()
            freq = self.dial_mhz * 1e6
            self.restart = False
            try:
                self.link.ser.timeout = 0.05
                lo = nb.configure_receiver(self.link, int(round(freq / (1 + a.rx_ppm * 1e-6))), DECIM, nb.FORMATS["cs8"],
                                           getattr(a, "rx_shift", 4), a.rx_gain)
                self.fine_hz = lo * (1 + a.rx_ppm * 1e-6) - freq
                if self.afc is not None:
                    self.afc.dc_hz = self.fine_hz        # the DSP thread moves DC by the fine offset: the LO leakage sits there
                self.link.run(0, lambda h, p: self._queue(p, h["format"]),
                              stop_flag=lambda: self.stopping.is_set() or self.hold.is_set() or self.restart)
            except Exception as e:                               # noqa: BLE001 - keep receiving; shown in the window
                self.log.put(f"receive: {e}; retrying")
                time.sleep(1.0)
        self.idle.set()

    def _queue(self, payload, fmt):
        try:
            self.packets.put_nowait((payload, fmt, getattr(self, "fine_hz", 0.0)))
        except queue.Full:
            pass

    def _dsp_loop(self):
        rate = br.RTL_RATE
        dc = None
        phase = 0.0
        while not self.stopping.is_set():
            try:
                parts = [self.packets.get(timeout=0.2)]
            except queue.Empty:
                continue
            while len(parts) < 40 and not self.packets.empty():     # batch up to ~80 ms
                parts.append(self.packets.get_nowait())
            fine = parts[-1][2]
            z = np.concatenate([self._iq(p, f) for p, f, _ in parts])
            z = np.conj(z)
            m = z.mean()
            dc = m if dc is None else dc + min(1.0, len(z) / (DC_TAU_S * rate)) * (m - dc)
            z = z - dc
            if abs(fine) > 0.5:
                w = 2 * np.pi * fine / rate
                z = z * np.exp(1j * (phase + w * np.arange(len(z))))
                phase = (phase + w * len(z)) % (2 * np.pi)
            zc = self.afc.process(z)
            audio = self.demod.process(zc)
            if self.mode == "hell":
                self.hell_rx.process(audio)
            elif self.mode.startswith("olivia-"):
                orx = self.olivia_rx
                if orx is not None:
                    orx.process(zc)                           # complex baseband: room for kHz of drift (the audio band is not)
            else:
                self.decoder.process(audio)
            mon = self.monitor
            if mon is not None:
                mon.write((audio * self.volume).astype(np.float32))

    @staticmethod
    def _iq(payload, fmt):
        if fmt == 1:
            a = np.frombuffer(payload, dtype=np.int8).astype(np.float32)
        else:
            a = np.frombuffer(payload, dtype="<i2").astype(np.float32) / 256.0
        return a[0::2] + 1j * a[1::2]

    # -- controls (called from the window)
    def set_monitor(self, on):
        if on and self.monitor is None:
            try:
                self.monitor = br.AudioOut(br.find_device(self.a.monitor_device, False) if self.a.monitor_device else None)
            except Exception as e:                               # noqa: BLE001
                return f"monitor: {e}"
        elif not on and self.monitor is not None:
            m, self.monitor = self.monitor, None
            m.close()
        return None

    def set_mode(self, mode):
        if self.sending:
            return "wait until the transmission ends"
        self.mode = mode
        if mode.startswith("olivia-"):
            m = olivia.Mode(mode.split("-", 1)[1], center=self.a.tone)
            self.olivia_rx = olivia_rx.OliviaRx(m, rate=br.DECIM_RATES[DECIM], emit=self.text.put, complex_in=True,
                                                search=m.bw + 300)
        else:
            self.olivia_rx = None
        if self.afc is not None:                         # Olivia: lock on the lead tone, then hold (the tones would pull it)
            self.afc.hold_after_lock = mode.startswith("olivia-")
            self.afc.reset()
        return None

    def set_frequency(self, mhz):
        if self.sending:
            return "wait until the transmission ends"
        if not BAND_MHZ[0] <= mhz <= BAND_MHZ[1]:
            return f"outside {BAND_MHZ[0]:.3f} .. {BAND_MHZ[1]:.3f} MHz"
        if self.ready:
            try:
                self.tx_obj.set_dial(mhz * 1e6, abs(self.space_hz) or self.a.space_shift)
            except ValueError as e:
                return str(e)
            self.space_hz = self.tx_obj.space_hz
            self.afc.reset()
        self.dial_mhz = mhz
        self.restart = True
        return None

    def send(self, text, wpm, lead_s, shift_spaces):
        if not self.ready:
            return "not ready"
        if self.sending:
            return "already transmitting"
        self.sending = True
        threading.Thread(target=self._send, args=(text, wpm, lead_s, shift_spaces), daemon=True).start()
        return None

    def _send(self, text, wpm, lead_s, shift_spaces):
        if self.mode.startswith("olivia-"):
            return self._send_olivia(text, lead_s)
        rate, tone = br.TX_RATE, self.a.tone
        parts = []
        if lead_s:
            t = np.arange(int(lead_s * rate)) / rate
            parts.append(0.5 * np.minimum(1, np.minimum(t, t[::-1]) / 0.005) * np.sin(2 * np.pi * tone * t))
            if text:
                parts.append(np.zeros(rate // 2))
        hell_mode = self.mode == "hell"
        if text:
            parts.append(hell.audio(text, tone=tone, rate=rate) if hell_mode else br.morse_audio(text, wpm=wpm, tone=tone))
        audio = np.concatenate(parts)
        self.tx_obj.env_taps = 8 if hell_mode else 40      # Hell's 4 ms elements need a 1 ms envelope window
        self.hold.set()
        try:
            if not self.idle.wait(5.0):
                raise RuntimeError("the receiver did not stop")
            self.tx_obj.space_hz = self.space_hz if shift_spaces else 0.0
            what = (f"Feld Hell {text!r}" if hell_mode else f"{text!r} at {wpm:g} WPM") if text else f"{lead_s:g} s tone"
            self.log.put(f"TX {self.dial_mhz:.3f} MHz: {what}")
            self.tx_obj.transmit((audio[i:i + 160] for i in range(0, len(audio), 160)), threading.Event())
        except Exception as e:                                   # noqa: BLE001 - shown in the window
            self.log.put(f"transmit failed: {e}")
        finally:
            self.afc.reset()
            self.sending = False
            self.hold.clear()

    def _send_olivia(self, text, lead_s):
        """Olivia goes out as records (one tone per symbol, exact average frequencies), always after a lead tone: the
        receiver's AFC locks on it and then holds."""
        name = self.mode.split("-", 1)[1]
        m = olivia.Mode(name, center=self.a.tone)
        t = self.tx_obj
        try:
            reach = txmodes.MAX_Q4 * txmodes.Q4_HZ
            edge = max(abs(t.offset_hz + m.tone_hz(0)), abs(t.offset_hz + m.tone_hz(m.tones - 1)))
            if edge > reach:
                raise RuntimeError(f"Olivia {name} does not fit here: the band reaches {edge:.0f} Hz from the LO, records {reach:.0f}")
            recs = m.tx_records(text, t.offset_hz, txmodes.peak_code(t.power_db), lead_s=lead_s or 2.0, rate=br.TX_RATE)
            self.hold.set()
            if not self.idle.wait(5.0):
                raise RuntimeError("the receiver did not stop")
            what = f"Olivia {name} {text!r}, {len(recs) / br.TX_RATE:.1f} s" if text else f"{lead_s or 2.0:g} s tone"
            self.log.put(f"TX {self.dial_mhz:.3f} MHz: {what}")
            s = t.session
            s.configure(t.lo_hz, br.TX_RATE, limit_s=600)
            s.begin(expect_word=txlink.lo_word(t.lo_hz))
            q = queue.Queue()
            for i in range(0, len(recs), 800):
                q.put(recs[i:i + 800])
            q.put(None)
            summary = s.stream(q, target_fill=br.TX_FILL)
            br.log(f"TX: off ({txlink.END_REASONS.get(summary['reason'], summary['reason'])}; underruns {summary['underruns']})")
        except Exception as e:                                   # noqa: BLE001 - shown in the window
            self.log.put(f"transmit failed: {e}")
        finally:
            self.afc.reset()
            self.sending = False
            self.hold.clear()

    # -- Olivia, typed live (as in fldigi)
    def start_live(self, lead_s=2.0):
        """Starts an Olivia transmission that sends what set_pending() offers, block by block (one block carries 3 or 4
        characters in 2.048 s; idle blocks of NUL go out while nothing is typed). end_live() ends it once the typed text is
        out, abort_live() at once."""
        if not self.ready:
            return "not ready"
        if self.sending:
            return "already transmitting"
        if not self.mode.startswith("olivia-"):
            return "live transmit is for the Olivia modes"
        self.sending = True
        self.live_lock = threading.Lock()
        self.live_text, self.live_base, self.live_sent = "", 0, 0
        self.live_end, self.live_abort = threading.Event(), threading.Event()
        threading.Thread(target=self._live, args=(lead_s or 2.0,), daemon=True).start()
        return None

    def set_pending(self, text, base):
        """The window's unsent text; `base`: how many characters of this transmission it had seen as sent when it took it."""
        with self.live_lock:
            self.live_text, self.live_base = text, base

    def _take(self, n):
        with self.live_lock:
            avail = self.live_text[max(0, self.live_sent - self.live_base):]
            chars = avail[:n]
            self.live_sent += len(chars)
            return chars

    def end_live(self):
        if self.sending and getattr(self, "live_end", None) is not None:
            self.live_end.set()

    def abort_live(self):
        if self.sending and getattr(self, "live_abort", None) is not None:
            self.live_abort.set()

    def _live(self, lead_s):
        name = self.mode.split("-", 1)[1]
        m = olivia.Mode(name, center=self.a.tone)
        t = self.tx_obj
        try:
            reach = txmodes.MAX_Q4 * txmodes.Q4_HZ
            edge = max(abs(t.offset_hz + m.tone_hz(0)), abs(t.offset_hz + m.tone_hz(m.tones - 1)))
            if edge > reach:
                raise RuntimeError(f"Olivia {name} does not fit here: the band reaches {edge:.0f} Hz from the LO, records {reach:.0f}")
            rs = m.record_stream(t.offset_hz, txmodes.peak_code(t.power_db), rate=br.TX_RATE)
            self.hold.set()
            if not self.idle.wait(5.0):
                raise RuntimeError("the receiver did not stop")
            self.log.put(f"TX {self.dial_mhz:.3f} MHz: Olivia {name}, live (RX or Ctrl+R ends, Esc aborts)")
            s = t.session
            s.configure(t.lo_hz, br.TX_RATE, limit_s=600)
            s.begin(expect_word=txlink.lo_word(t.lo_hz))
            q = queue.Queue()
            q.put(rs.lead(lead_s))

            def feed():
                # One block waits in the queue while the stream sends the one before: text typed reaches the air within one
                # to two blocks (2-4 s), and the next block is built just in time from what is typed by then.
                while not self.live_abort.is_set():
                    if q.qsize():
                        time.sleep(0.05)
                        continue
                    chars = self._take(m.bits)
                    if not chars and self.live_end.is_set():
                        break
                    q.put(rs.block(chars))
                q.put(None)

            threading.Thread(target=feed, daemon=True).start()
            summary = s.stream(q, target_fill=br.TX_FILL, stop=self.live_abort)
            br.log(f"TX: off ({txlink.END_REASONS.get(summary['reason'], summary['reason'])}; underruns {summary['underruns']})")
            if summary["reason"] != 0 and not self.live_abort.is_set():
                self.log.put(f"transmission ended: {txlink.END_REASONS.get(summary['reason'], summary['reason'])}")
        except Exception as e:                                   # noqa: BLE001 - shown in the window
            self.log.put(f"transmit failed: {e}")
        finally:
            self.live_abort.set()
            self.afc.reset()
            self.sending = False
            self.hold.clear()

    # -- CW beacon
    def beacon_cycle(self, text, wpm, period_s, rate=None):
        """One beacon cycle as keyer audio: the ID in CW, then a steady carrier to the end of `period_s`."""
        rate, tone = rate or br.TX_RATE, self.a.tone
        ident = br.morse_audio(text, wpm=wpm, tone=tone)
        ident = np.concatenate([np.zeros(6 * int(rate * 1.2 / wpm)), ident])   # a word space after the carrier (morse_audio adds one unit)
        n = int(period_s * rate) - len(ident)
        if n < 2 * rate:
            raise ValueError(f"the ID takes {len(ident) / rate:.1f} s: the period must be at least 2 s longer")
        ramp = np.minimum(1.0, np.minimum(np.arange(n), np.arange(n)[::-1]) / (0.005 * rate))   # 5 ms edges
        carrier = 0.5 * ramp * np.sin(2 * np.pi * tone * np.arange(n) / rate)
        return np.concatenate([ident, carrier])

    def start_beacon(self, text, wpm, period_s, shift_spaces):
        """Transmits beacon cycles back to back until stop_beacon(). Receiving pauses meanwhile (half duplex)."""
        if not self.ready:
            return "not ready"
        if self.sending:
            return "already transmitting"
        try:
            cycle = self.beacon_cycle(text, wpm, period_s)
        except ValueError as e:
            return str(e)
        self.sending = True
        self.beacon_stop = threading.Event()
        threading.Thread(target=self._beacon, args=(cycle, text, wpm, period_s, shift_spaces, self.beacon_stop), daemon=True).start()
        return None

    def stop_beacon(self):
        if self.beacon_stop is not None:
            self.beacon_stop.set()

    def _beacon(self, cycle, text, wpm, period_s, shift_spaces, stop):
        def blocks():
            i = 0
            while not stop.is_set():
                yield cycle[i:i + 160]
                i = i + 160 if i + 160 < len(cycle) else 0
        self.hold.set()
        try:
            if not self.idle.wait(5.0):
                raise RuntimeError("the receiver did not stop")
            self.tx_obj.space_hz = self.space_hz if shift_spaces else 0.0
            self.tx_obj.env_taps = 40
            self.log.put(f"beacon on {self.dial_mhz:.3f} MHz: {text!r} at {wpm:g} WPM every {period_s:g} s")
            while not stop.is_set():                             # a session ends after 600 s: start the next one
                self.tx_obj.transmit(blocks(), stop)
        except Exception as e:                                   # noqa: BLE001 - shown in the window
            self.log.put(f"beacon failed: {e}")
        finally:
            self.beacon_stop = None
            self.afc.reset()
            self.sending = False
            self.hold.clear()
            self.log.put("beacon off")

    def close(self):
        self.stop_beacon()
        self.set_monitor(False)
        self.stopping.set()
        self.idle.wait(2.0)
        if self.link:
            try:
                self.link.ser.close()
            except Exception:                                    # noqa: BLE001
                pass
