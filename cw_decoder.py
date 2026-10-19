"""A streaming CW (Morse) decoder for the audio of espdr_fldigi's receive chain.

The bridge's AFC keeps the carrier at a fixed audio tone (700 Hz by default), so the decoder only measures the level at that tone:
it mixes the tone down to 0 Hz and averages over 5 ms, giving the envelope 200 times a second. A peak tracker and a floor tracker
set the threshold halfway between them (in dB), with hysteresis. Marks and spaces are timed against the dot length, which starts
from the expected speed and then follows the signal. Short drop-outs inside a mark (under 0.35 dot) are bridged, a carrier longer
than 7 dots (a tuning or lead tone) is shown as [carrier], not decoded.
"""
import numpy as np

MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y", "--..": "Z", "-----": "0", ".----": "1", "..---": "2",
    "...--": "3", "....-": "4", ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9", "-..-.": "/",
    "..--..": "?", ".-.-.-": ".", "--..--": ",", "-...-": "=", ".-.-.": "+", "-....-": "-", "...-.-": "<SK>", ".-...": "<AS>",
}


class CwDecoder:
    STEP = 0.005                        # envelope resolution, seconds

    def __init__(self, rate=48000, tone=700.0, wpm=12.0, emit=print):
        self.rate, self.tone, self.emit = rate, tone, emit
        self.n = int(rate * self.STEP)
        self.phase = 0.0
        self.buf = np.zeros(0, np.complex128)
        self.set_wpm(wpm)
        self.hi = self.lo = 1e-6        # peak and floor trackers (linear amplitude)
        self.key = False
        self.run = 0.0                  # seconds in the current key state
        self.pending = 0.0              # a mark not yet classified (a short drop-out may still extend it)
        self.merge = False
        self.code = ""
        self.word_open = False
        self.carrier = False

    def set_wpm(self, wpm):
        self.dit = 1.2 / max(5.0, min(40.0, wpm))

    @property
    def wpm(self):
        return 1.2 / self.dit

    @property
    def snr_db(self):
        return 20 * np.log10(self.hi / self.lo) if self.lo > 0 else 0.0

    def process(self, x):
        """Audio samples (float, `rate`) in; characters out through emit()."""
        x = np.asarray(x, dtype=float)
        if not len(x):
            return
        w = 2 * np.pi * self.tone / self.rate
        ph = self.phase + w * np.arange(len(x))
        self.phase = (self.phase + w * len(x)) % (2 * np.pi)
        self.buf = np.concatenate([self.buf, x * np.exp(-1j * ph)])
        k = len(self.buf) // self.n
        if k:
            amp = 2 * np.abs(self.buf[:k * self.n].reshape(k, self.n).mean(axis=1))
            self.buf = self.buf[k * self.n:]
            for a in amp:
                self._step(float(a))

    def _step(self, a):
        # Peak: instant attack, ~3 dB/s decay. Floor: the average noise, followed while the key is up (~100 ms) and held while it is
        # down (following minimums instead would lock onto the deepest noise dips and put the threshold inside the noise).
        a = max(a, 1e-5)                                     # digital silence would pin the floor at 0 for good
        if self.lo <= 1e-6:                                  # first reading
            self.hi = self.lo = a
        self.hi = a if a > self.hi else self.hi * 0.9983
        if not self.key:
            self.lo = min(0.95 * self.lo + 0.05 * a, self.hi)
        signal = self.hi > 4 * self.lo                       # at least 12 dB between peak and floor
        mid = max(np.sqrt(self.hi * self.lo), 4 * self.lo)    # never closer than 12 dB to the floor
        if self.key:
            new = signal and a > mid / 1.3
        else:
            new = signal and a > mid * 1.3
        if new == self.key:
            self.run += self.STEP
            if not self.key:
                self._idle()
            return
        dur, self.run = self.run, self.STEP
        if self.key:
            self._mark_end(dur)
        else:
            self._space_end(dur)
        self.key = new

    def _mark_end(self, d):
        self.pending = self.pending + d if self.merge else d
        self.merge = False

    def _space_end(self, d):
        if self.pending and d < 0.35 * self.dit:            # a drop-out inside a mark: keep counting the mark
            self.merge = True
            self.pending += d
            return
        self._finish_mark()

    def _finish_mark(self):
        d, self.pending = self.pending, 0.0
        if not d:
            return
        if d > 7 * self.dit:                                 # tuning / lead tone
            if not self.carrier:
                self._flush_char()
                self.emit("[carrier]")
                self.carrier = True
            return
        self.carrier = False
        if d < 0.3 * self.dit:                               # a glitch
            return
        if d < 2 * self.dit:
            self.code += "."
            self.dit = 0.85 * self.dit + 0.15 * d
        else:
            self.code += "-"
            self.dit = 0.85 * self.dit + 0.15 * d / 3
        self.dit = min(max(self.dit, 1.2 / 40), 1.2 / 5)
        self.word_open = True

    def _idle(self):
        if self.pending and self.run > 0.35 * self.dit:     # too long for a drop-out: the mark is over
            self._finish_mark()
        if self.code and self.run > 2.5 * self.dit:
            self._flush_char()
        if self.word_open and not self.code and self.run > 6 * self.dit:
            self.emit(" ")
            self.word_open = False

    def _flush_char(self):
        if self.code:
            self.emit(MORSE.get(self.code, "*"))
            self.code = ""
