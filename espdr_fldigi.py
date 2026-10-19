"""espdr-fldigi: two ESP32-S3 boards (esp32-sdr-trx) as a CW transceiver for fldigi.

RX  board A runs espdr-rx (an rtl_tcp server). This program tunes it to the dial frequency, demodulates upper sideband and plays the
    audio into a sound device that fldigi records from (one end of a virtual audio cable).
TX  fldigi plays its CW audio into another sound device (the other virtual cable). While fldigi is transmitting (its XML-RPC trx state)
    this program follows the tone's envelope and frequency and keys board B: carrier at dial + tone, gain code from the envelope.
    Records go out at 8000 per second, which streams without underruns on Windows (40000, the espdr-tx default, does not).

Key-up is the weakest gain code, 18 dB below key-down: the chip cannot switch its carrier off inside a transmission. Between
transmissions (fldigi in RX) the carrier is off.

Transmitting needs an amateur radio licence; this uses espdr-tx's licence confirmation (run espdr-tx --accept-licence once).

  python espdr_fldigi.py --list-devices
  python espdr_fldigi.py --dial 2350.000 --rx-port COM4 --tx-port COM5 --rx-out "CABLE Input" --tx-in "CABLE-B Output"
  python espdr_fldigi.py --dial 2350.000 --rx-port COM4 --tx-port COM5 --selftest "TEST DE <CALL>" --rx-wav selftest.wav
"""
import argparse
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
import wave
import xmlrpc.client

import numpy as np

from espdr import board, cli_tx, dsp, nb, txlink, txmodes

RTL_RATE = 250_000         # espdr-rx passes exactly this rate through unchanged; 62_500 with --decim 16 (set in main)
DECIM_RATES = {4: 250_000, 16: 62_500}
AUDIO_RATE = 48_000        # sound-card side (WASAPI shared mode)
TX_RATE = 8_000            # records per second to the transmitter; one record per 8 kHz audio sample
TX_FILL = 4_800            # records kept in the chip's buffer (600 ms): must exceed the firmware's TX_PREFILL (4000), or the carrier never starts
USB_LO, USB_HI = 200.0, 2_800.0


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# --- receive: rtl_tcp I/Q -> USB audio --------------------------------------------------------------------------------
class ComplexFir:
    """Streaming FIR with complex taps (espdr.dsp.Fir keeps only the real part of its taps)."""

    def __init__(self, h):
        self.h = np.asarray(h, dtype=np.complex128)
        self.tail = np.zeros(len(self.h) - 1, dtype=np.complex128)

    def process(self, x):
        buf = np.concatenate([self.tail, x])
        self.tail = buf[len(buf) - len(self.tail):]
        return np.convolve(buf, self.h, mode="valid")


class UsbDemod:
    """250 ksps complex baseband (the dial frequency at 0 Hz) -> real upper-sideband audio at 48 kHz."""

    def __init__(self):
        self.rs = dsp.Resampler(RTL_RATE, AUDIO_RATE)
        mid, half = (USB_LO + USB_HI) / 2, (USB_HI - USB_LO) / 2
        k = np.arange(511) - 255
        # complex band-pass USB_LO..USB_HI: a low-pass of half the width, moved up to the middle of the band
        self.bp = ComplexFir(dsp.lowpass(511, half, AUDIO_RATE) * np.exp(2j * np.pi * mid * k / AUDIO_RATE))
        self.level = 1e-3

    def process(self, z):
        y = self.bp.process(self.rs.process(z)).real
        if not len(y):
            return y.astype(np.float32)
        # slow AGC: 0.25 of full scale for the strongest signal of the last seconds. The decay is per second, not per block (socket
        # reads come many times a second), so the gain holds through CW spaces instead of filling them up
        peak = float(np.percentile(np.abs(y), 99.5))
        self.level = max(peak, self.level * np.exp(-len(y) / (3.0 * AUDIO_RATE)), 1e-3)
        return np.clip(0.25 * y / self.level, -1, 1).astype(np.float32)


class RxAfc:
    """Keeps the strongest new carrier at `target` Hz of audio. The boards drift by kHz while transmitting (190 Hz/s measured), far
    more than fldigi's AFC follows. Spurs of the receiver stay at fixed frequencies, so they are removed by comparing each spectrum
    with the median of the last ~4 s; a CW carrier drifts through the bins and stands out."""

    def __init__(self, target_hz, span_hz=8000.0):
        self.N = 1 << int(round(np.log2(RTL_RATE * 0.065)))     # ~65 ms per spectrum at any rate (16384 at 250 ksps)
        self.target = target_hz
        self.f = np.fft.fftshift(np.fft.fftfreq(self.N, 1 / RTL_RATE))
        self.band = np.abs(self.f) < span_hz
        self.win = np.hanning(self.N)
        self.buf = np.zeros(0, np.complex64)
        self.hist = []
        self.shift = 0.0        # Hz removed from the signal before demodulation
        self.phase = 0.0
        self.locked = False
        self.peak = 0.0         # strongest recent ratio: only signals within 12 dB of it steer (not CW spaces, see --space-shift)

    def reset(self):
        self.hist, self.shift, self.locked, self.peak = [], 0.0, False, 0.0

    def _update(self, seg):
        p = np.abs(np.fft.fftshift(np.fft.fft(seg * self.win))) ** 2
        self.hist = (self.hist + [p])[-60:]
        if len(self.hist) < 15:
            return
        ratio = p / (np.median(self.hist, axis=0) + 1e-9)
        ratio[~self.band] = 0
        k = int(np.argmax(ratio))
        self.peak = max(float(ratio[k]), self.peak * 0.98)  # decays ~4 dB in 3 s
        if ratio[k] < 10 or ratio[k] < self.peak / 16:     # nothing 10 dB over the usual spectrum, or a key-up/space: hold
            return
        want = self.f[k] - self.target
        if not self.locked or abs(want - self.shift) > 1500:
            self.shift, self.locked = want, True           # first lock (or a jump): go there at once
        else:
            self.shift += 0.5 * (want - self.shift)

    def process(self, z):
        self.buf = np.concatenate([self.buf, z])
        while len(self.buf) >= self.N:
            self._update(self.buf[:self.N])
            self.buf = self.buf[self.N:]
        step = -2 * np.pi * self.shift / RTL_RATE          # phase-continuous mix down by `shift`
        ph = self.phase + step * np.arange(len(z))
        self.phase = (self.phase + step * len(z)) % (2 * np.pi)
        return z * np.exp(1j * ph)


class RtlClient(threading.Thread):
    """Reads espdr-rx's rtl_tcp stream, demodulates it and hands audio blocks to `sink`."""

    def __init__(self, addr, dial_hz, sink, stop):
        super().__init__(daemon=True)
        self.addr, self.dial_hz, self.sink, self.stop = addr, dial_hz, sink, stop
        self.demod = UsbDemod()
        self.afc = None                     # RxAfc, set by main unless --no-afc
        self.measure = None                # "base" or "tx": add the I/Q power spectrum to self.spectra[measure] (for --selftest)
        self.spectra = {"base": [], "tx": []}

    def _measure(self, z):
        n = 1 << 14
        self._acc = np.concatenate([getattr(self, "_acc", np.zeros(0, np.complex64)), z])   # socket reads are often shorter than n
        while len(self._acc) >= n:
            seg, self._acc = self._acc[:n], self._acc[n:]
            self.spectra[self.measure].append(np.abs(np.fft.fftshift(np.fft.fft(seg * np.hanning(n)))) ** 2)

    def retune(self, dial_hz):
        """Moves the receiver while it runs (rtl_tcp 'set frequency'); the AFC starts over."""
        self.dial_hz = dial_hz
        s = getattr(self, "sock", None)
        if s:
            s.sendall(struct.pack(">BI", 1, int(round(dial_hz))))
        if self.afc:
            self.afc.reset()
        log(f"RX: retuned to {dial_hz / 1e6:.6f} MHz")

    def carrier_offset(self):
        """Where the transmitter appeared against the dial (Hz), from the spectra taken before and during the transmission."""
        if not self.spectra["base"] or not self.spectra["tx"]:
            return None
        f = np.fft.fftshift(np.fft.fftfreq(1 << 14, 1 / RTL_RATE))
        ratio = np.max(self.spectra["tx"], axis=0) / (np.median(self.spectra["base"], axis=0) + 1e-9)
        ratio[(f < -20_000) | (f > 20_000)] = 0
        return float(f[np.argmax(ratio)])

    def run(self):
        while not self.stop.is_set():
            try:
                s = socket.create_connection(self.addr, timeout=5)
            except OSError:
                time.sleep(1.0)
                continue
            try:
                s.settimeout(30)
                if s.recv(12)[:4] != b"RTL0":
                    raise OSError("not an rtl_tcp server")
                s.sendall(struct.pack(">BI", 2, RTL_RATE) + struct.pack(">BI", 1, int(round(self.dial_hz))))
                self.sock = s
                log(f"RX: tuned {self.dial_hz / 1e6:.6f} MHz on {self.addr[0]}:{self.addr[1]}")
                odd = b""
                while not self.stop.is_set():
                    data = s.recv(1 << 16)
                    if not data:
                        raise OSError("server closed the connection")
                    data = odd + data
                    n = len(data) & ~1
                    odd = data[n:]
                    a = np.frombuffer(data[:n], np.uint8).astype(np.float32) - 127.5
                    z = a[0::2] + 1j * a[1::2]
                    if self.measure:
                        self._measure(z)
                    if self.afc:
                        z = self.afc.process(z)
                    self.sink(self.demod.process(z))
            except OSError as e:
                if not self.stop.is_set():
                    log(f"RX: {e}; reconnecting")
                    time.sleep(1.0)
            finally:
                s.close()


class AudioOut:
    """A ring buffer in front of an output sound device; muted while transmitting. Over- and underflow (the two clocks differ) drop
    or pad samples."""

    def __init__(self, device):
        import sounddevice as sd
        self.buf = np.zeros(0, np.float32)
        self.lock = threading.Lock()
        self.muted = False
        self.max = AUDIO_RATE // 2
        self.stream = sd.OutputStream(samplerate=AUDIO_RATE, channels=1, dtype="float32", device=device, callback=self._cb,
                                      blocksize=AUDIO_RATE // 50)
        self.stream.start()

    def write(self, x):
        with self.lock:
            self.buf = np.concatenate([self.buf, x])[-self.max:]

    def _cb(self, out, frames, time_info, status):
        with self.lock:
            n = min(frames, len(self.buf))
            chunk, self.buf = self.buf[:n], self.buf[n:]
        out[:, 0] = 0
        if not self.muted:
            out[:n, 0] = chunk

    def close(self):
        self.stream.stop()
        self.stream.close()


class WavOut:
    def __init__(self, path):
        self.w = wave.open(path, "wb")
        self.w.setnchannels(1)
        self.w.setsampwidth(2)
        self.w.setframerate(AUDIO_RATE)
        self.muted = False
        self.lock = threading.Lock()

    def write(self, x):
        with self.lock:
            self.w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())

    def close(self):
        with self.lock:
            self.w.close()


# --- transmit: CW audio -> keyed carrier --------------------------------------------------------------------------------
class CwKeyer:
    """8 kHz CW audio -> transmitter records. The envelope (5 ms RMS) sets the gain code; the tone's frequency, measured from zero
    crossings while the key is down, sets a constant frequency offset, so the carrier sits at dial + tone like an SSB rig's would."""

    def __init__(self, offset_hz, power_db=0.0, tone_hz=None, space_hz=0.0):
        self.offset_hz = offset_hz                     # dial against the chip's real LO, in Hz of the nominal clock
        self.space_hz = space_hz                       # during spaces, move the (weakest, -18 dB) carrier this far away
        self.peak = txmodes.peak_code(power_db)
        self.tone = tone_hz or 1000.0
        self.fixed = tone_hz is not None
        self.ms = dsp.Fir(np.ones(40) / 40)
        self.ref = 0.0
        self.prev = 0.0
        self.crossings = self.down_n = 0
        self.measured = False

    def process(self, x):
        env = np.sqrt(2 * np.maximum(self.ms.process(x * x), 0))
        self.ref = max(float(env.max(initial=0)), self.ref * 0.999)
        level = env / self.ref if self.ref > 1e-3 else np.zeros(len(x))
        down = level > 0.5
        if not self.fixed and down.any():              # count zero crossings while the key is down; update every 50 ms of key-down
            s = np.signbit(np.concatenate([[self.prev], x]))
            self.crossings += np.count_nonzero(s[1:][down] != s[:-1][down])
            self.down_n += int(down.sum())
            if self.down_n >= 400:
                f = self.crossings * TX_RATE / (2 * self.down_n)
                if 200 < f < 3000:
                    self.tone = 0.8 * self.tone + 0.2 * f if self.measured else f      # the first measurement replaces the guess
                    self.measured = True
                self.crossings = self.down_n = 0
        if len(x):
            self.prev = x[-1]
        hz = np.full(len(x), self.offset_hz + self.tone)
        if self.space_hz:
            hz[level < 0.1] += self.space_hz           # only once the edge has ramped down: the chip's gain floor is just 18 dB
        return txmodes.pack(hz / txmodes.Q4_HZ, txmodes.codes_for_levels(np.clip(level, 0, 1), self.peak))


class AudioIn:
    """Input sound device -> 8 kHz float blocks in a queue (only while `enabled`)."""

    def __init__(self, device):
        import sounddevice as sd
        self.q = queue.Queue(maxsize=200)
        self.enabled = False
        self.down = dsp.Resampler(AUDIO_RATE, TX_RATE)
        self.stream = sd.InputStream(samplerate=AUDIO_RATE, channels=1, dtype="float32", device=device, callback=self._cb,
                                     blocksize=AUDIO_RATE // 50)
        self.stream.start()

    def _cb(self, indata, frames, time_info, status):
        if self.enabled:
            try:
                self.q.put_nowait(self.down.process(indata[:, 0].astype(float)))
            except queue.Full:
                pass

    def drain(self):
        while not self.q.empty():
            self.q.get_nowait()

    def close(self):
        self.stream.stop()
        self.stream.close()


def morse_audio(text, wpm=20, tone=700.0, rate=TX_RATE):
    """Text -> CW audio with 5 ms raised-cosine edges (PARIS timing), for --selftest."""
    code = {"A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---",
            "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-",
            "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..", "0": "-----", "1": ".----", "2": "..---",
            "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.", "/": "-..-.",
            "?": "..--..", ".": ".-.-.-", ",": "--..--", "=": "-...-"}
    unit = int(rate * 1.2 / wpm)
    keys = []
    for word in text.upper().split():
        for ch in word:
            for sym in code.get(ch, ""):
                keys += [1] * (unit if sym == "." else 3 * unit) + [0] * unit
            keys += [0] * (2 * unit)
        keys += [0] * (4 * unit)
    k = np.array([0] * unit + keys, dtype=float)
    edge = int(0.005 * rate)
    k = np.convolve(k, np.hanning(2 * edge + 1) / np.hanning(2 * edge + 1).sum(), "same")
    return 0.5 * k * np.sin(2 * np.pi * tone * np.arange(len(k)) / rate)


class Transmitter:
    """Owns board B's link; transmit(blocks) runs one transmission until `stop` is set or the blocks end."""

    def __init__(self, port, dial_hz, ppm, power_db, tone_hz, space_hz=0.0):
        if not cli_tx._licence_accepted():
            raise SystemExit("transmitting needs the espdr-tx licence confirmation: read `espdr-tx -h` and run espdr-tx --accept-licence once")
        self.power_db, self.tone_hz, self.ppm = power_db, tone_hz, ppm
        try:
            self.set_dial(dial_hz, space_hz)                       # checked before the board is touched
        except ValueError as e:
            raise SystemExit(str(e))
        port = board.ensure(board.TX, port=port, native=True, log=lambda m: log("TX: " + m))
        self.session = txlink.Session(nb.open_link(port))
        log(f"TX: board on {port}, power {power_db:g} dB")

    def set_dial(self, dial_hz, space_hz=None):
        """Plans the LO and the record offsets for a new dial frequency (between transmissions). Raises ValueError."""
        if not txlink.TX_MIN_HZ + 5_000 <= dial_hz <= txlink.TX_MAX_HZ - 5_000:
            raise ValueError("the dial must be inside 2320.005 .. 2449.995 MHz (the transmitter's band)")
        if space_hz is None:
            space_hz = abs(self.space_hz)
        want = dial_hz / (1 + self.ppm * 1e-6)                     # in the chip's nominal clock
        lo_hz, _ = txlink.choose_lo(want)
        real_lo = txlink.word_hz(txlink.lo_word(lo_hz))
        offset = want - real_lo
        reach = txmodes.MAX_Q4 * txmodes.Q4_HZ

        def fits(shift):                       # marks (tone 200..3000 Hz) and spaces must stay inside what a record can carry
            return all(abs(offset + t + s) <= reach for t in (200, 3000) for s in (0, shift))

        # The PLL can put the LO up to ~15 kHz from the dial (2394.250: 14.6 kHz below), so try the shift both ways.
        space = next((s for s in (space_hz, -space_hz) if fits(s)), None) if space_hz else 0.0
        if space is None:
            raise ValueError(f"--space-shift {space_hz:g} does not fit either way here: records reach +-{reach:.0f} Hz and the "
                             f"dial is {offset:+.0f} Hz from the LO")
        self.dial_hz, self.lo_hz, self.offset_hz, self.space_hz = dial_hz, lo_hz, offset, space
        if space_hz and space != space_hz:
            log(f"TX: spaces go {space:+.0f} Hz (the other way would leave the records' reach)")
        log(f"TX: dial {dial_hz / 1e6:.6f} MHz, LO {real_lo / 1e6:.6f} MHz nominal, dial offset {offset:+.0f} Hz")

    def transmit(self, blocks, stop):
        keyer = CwKeyer(self.offset_hz, self.power_db, self.tone_hz, self.space_hz)
        q = queue.Queue(maxsize=64)

        def producer():
            for b in blocks:
                if stop.is_set():
                    break
                q.put(keyer.process(b))
            q.put(None)

        self.session.configure(self.lo_hz, TX_RATE, limit_s=600)
        self.session.begin(expect_word=txlink.lo_word(self.lo_hz))
        threading.Thread(target=producer, daemon=True).start()
        summary = self.session.stream(q, target_fill=TX_FILL, stop=stop)
        log(f"TX: off ({txlink.END_REASONS.get(summary['reason'], summary['reason'])}; underruns {summary['underruns']}, "
            f"tone {keyer.tone:.0f} Hz)")
        return summary


# --- fldigi -----------------------------------------------------------------------------------------------------------
def fldigi_state(proxy):
    try:
        return str(proxy.main.get_trx_state())
    except (OSError, xmlrpc.client.Error):
        return None


def run_fldigi(args, tx, audio_out):
    proxy = xmlrpc.client.ServerProxy(args.fldigi)
    if tx and not args.tx_in:
        raise SystemExit("--tx-in is needed with --tx-port: the sound device fldigi plays into")
    ain = AudioIn(find_device(args.tx_in, True)) if tx else None
    last = None
    try:
        while True:
            state = fldigi_state(proxy)
            if state != last:
                log(f"fldigi: {state or 'not reachable at ' + args.fldigi}")
                last = state
            if state in ("TX", "TUNE") and tx:
                if audio_out:
                    audio_out.muted = True
                stop = threading.Event()
                ain.drain()
                ain.enabled = True

                def blocks():
                    while not stop.is_set():
                        try:
                            yield ain.q.get(timeout=0.1)
                        except queue.Empty:
                            yield np.zeros(TX_RATE // 50)        # keep the chip fed through gaps in the audio

                def watch():
                    while fldigi_state(proxy) in ("TX", "TUNE"):
                        time.sleep(0.05)
                    stop.set()

                threading.Thread(target=watch, daemon=True).start()
                log("TX: on")
                tx.transmit(blocks(), stop)
                ain.enabled = False
                if audio_out:
                    audio_out.muted = False
                continue
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if ain:
            ain.close()


def run_selftest(args, tx, rx):
    tone = args.tone or 700.0
    audio = morse_audio(args.selftest, wpm=args.wpm, tone=tone)
    if args.lead:                                                # a steady carrier first: AFC, AGC and the decoder lock onto it
        t = np.arange(int(args.lead * TX_RATE)) / TX_RATE
        lead = 0.5 * np.minimum(1, np.minimum(t, t[::-1]) / 0.005) * np.sin(2 * np.pi * tone * t)
        audio = np.concatenate([lead, np.zeros(TX_RATE // 2), audio])
    log(f"selftest: sending '{args.selftest}' at {args.wpm} WPM"
        + (f" after a {args.lead:g} s tone" if args.lead else "") + f", {len(audio) / TX_RATE:.1f} s")
    if rx:
        rx.measure = "base"
    time.sleep(1.5)                                              # let the receiver settle; its spectrum without the transmitter
    stop = threading.Event()
    blocks = (audio[i:i + 160] for i in range(0, len(audio), 160))
    if rx:
        rx.measure = "tx"
    summary = tx.transmit(blocks, stop)
    time.sleep(1.5)                                              # the receiver's tail
    if rx:
        rx.measure = None
        off = rx.carrier_offset()
        if off is not None:                                      # the receiver is tuned to dial + rx_shift, so `off` is the audio tone
            log(f"selftest: the carrier arrived as a {off:+.0f} Hz tone (sent {tone:.0f} Hz). "
                f"To hear it at {tone:.0f} Hz: --rx-shift {args.rx_shift + off - tone:+.0f}")
    return summary


def find_device(spec, want_input):
    if spec is None:
        return None
    import sounddevice as sd
    if str(spec).isdigit():
        return int(spec)
    for i, d in enumerate(sd.query_devices()):
        api_ok = sys.platform != "win32" or sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI"
        has_dir = d["max_input_channels" if want_input else "max_output_channels"] > 0
        if api_ok and has_dir and spec.lower() in d["name"].lower():
            return i
    raise SystemExit(f"no {'input' if want_input else 'output'} sound device matches '{spec}' (see --list-devices)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dial", type=float, help="dial frequency in MHz (the carrier lands at dial + fldigi's audio tone)")
    p.add_argument("--rx-port", help="native USB port of the receiver board (espdr-rx is started on it unless --no-rx-server)")
    p.add_argument("--tx-port", help="native USB port of the transmitter board (omit for receive only)")
    p.add_argument("--rx-ppm", type=float, default=0.0, help="receiver board's crystal error")
    p.add_argument("--tx-ppm", type=float, default=0.0, help="transmitter board's crystal error")
    p.add_argument("--rx-gain", type=int, help="receiver gain selector 0..127 (default 15, or 0 with --decim 16: with the boards "
                                                "close, more overloads the 8-bit stream)")
    p.add_argument("--decim", type=int, choices=sorted(DECIM_RATES), default=4,
                   help="receiver decimation: 4 = 250 ksps (stock firmware; Windows' USB serial path loses ~56%%), 16 = 62.5 ksps "
                        "(firmware with decimation 16, see --rx-image; no loss on Windows)")
    p.add_argument("--rx-image", help="receiver firmware image to load (needed for --decim 16 unless it is already running)")
    p.add_argument("--rx-shift", type=float, default=0.0, help="tune the receiver this many Hz above the dial (follows the boards' "
                                                                 "drift; --selftest prints the value to use)")
    p.add_argument("--rx-out", help="sound device fldigi records from (name part or number)")
    p.add_argument("--tx-in", help="sound device fldigi plays into (name part or number)")
    p.add_argument("--rx-wav", help="write the receive audio to this WAV file (instead of or as well as --rx-out)")
    p.add_argument("--afc-tone", type=float, default=700.0, help="receive AFC: keep the strongest carrier at this audio frequency (default 700)")
    p.add_argument("--no-afc", action="store_true", help="no receive AFC (use --rx-shift by hand)")
    p.add_argument("--rtl", default="127.0.0.1:1234", help="espdr-rx's rtl_tcp address")
    p.add_argument("--no-rx-server", action="store_true", help="espdr-rx is already running; do not start it")
    p.add_argument("--fldigi", default="http://127.0.0.1:7362", help="fldigi's XML-RPC address")
    p.add_argument("--power", type=float, default=0.0, help="transmit power in dB against the strongest (0 .. -17.9)")
    p.add_argument("--space-shift", type=float, default=0.0, metavar="HZ",
                   help="move the carrier this far during CW spaces (e.g. 12000): the chip's key-up is only 18 dB down; this takes it out of "
                        "the receiver's passband. The space is still a (weak) emission, inside the band")
    p.add_argument("--tone", type=float, help="fixed CW tone in Hz (default: measured from fldigi's audio)")
    p.add_argument("--selftest", metavar="TEXT", help="send TEXT in CW from the transmitter without fldigi, and exit")
    p.add_argument("--wpm", type=float, default=18.0, help="speed for --selftest")
    p.add_argument("--lead", type=float, default=2.0, metavar="SECONDS",
                   help="--selftest: steady tone before the message, then 0.5 s pause (default 2; 0 for none)")
    p.add_argument("--list-devices", action="store_true", help="list sound devices and exit")
    args = p.parse_args()

    if args.list_devices:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            api = sd.query_hostapis(d["hostapi"])["name"]
            if sys.platform != "win32" or api == "Windows WASAPI":
                print(f"{i:3d}  in {d['max_input_channels']}  out {d['max_output_channels']}  {d['name']}")
        return
    if args.dial is None:
        p.error("--dial is required")
    dial_hz = args.dial * 1e6
    global RTL_RATE
    RTL_RATE = DECIM_RATES[args.decim]
    if args.rx_gain is None:
        args.rx_gain = 0 if args.decim == 16 else 15

    stop = threading.Event()
    server = None
    sinks = []
    rx = None
    try:
        if args.rx_port or args.no_rx_server:
            if not args.no_rx_server:
                cmd = [sys.executable, "-c", "from espdr.cli_rx import main; main()", "--native", "--port", args.rx_port,
                       "--gain", str(args.rx_gain), "--ppm", str(args.rx_ppm), "--listen", args.rtl, "--decim", str(args.decim)]
                if args.rx_image:
                    cmd += ["--image", args.rx_image]
                server = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in server.stdout:                      # wait until it serves
                    if "rtl_tcp server" in line:
                        log("RX: " + line.strip())
                        break
                    if "espdr-rx:" in line or "Error" in line:
                        raise SystemExit("RX: " + line.strip())
                threading.Thread(target=lambda: [None for _ in server.stdout], daemon=True).start()
            if args.rx_out:
                sinks.append(AudioOut(find_device(args.rx_out, False)))
            if args.rx_wav:
                sinks.append(WavOut(args.rx_wav))
            if not sinks:
                p.error("give --rx-out and/or --rx-wav for the receive audio")
            host, _, port = args.rtl.rpartition(":")
            rx = RtlClient((host, int(port)), dial_hz + args.rx_shift, lambda x: [s.write(x) for s in sinks], stop)
            if not args.no_afc:
                rx.afc = RxAfc(args.afc_tone)
            rx.start()

        tx = Transmitter(args.tx_port, dial_hz, args.tx_ppm, args.power, args.tone, args.space_shift) if args.tx_port else None
        if args.selftest:
            if not tx:
                p.error("--selftest needs --tx-port")
            run_selftest(args, tx, rx)
        else:
            out = next((s for s in sinks if isinstance(s, AudioOut)), None)
            log("running; Ctrl-C ends")
            run_fldigi(args, tx, out)
    finally:
        stop.set()
        for s in sinks:
            s.close()
        if server:
            server.terminate()


if __name__ == "__main__":
    main()
