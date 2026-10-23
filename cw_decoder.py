"""A streaming CW (Morse) decoder for the audio of espdr_fldigi's receive chain.

The bridge's AFC keeps the carrier at a fixed audio tone (700 Hz by default), so the decoder only measures the level at that tone:
it mixes the tone down to 0 Hz and averages over 5 ms, giving the envelope 200 times a second. A peak tracker and a floor tracker
set the threshold halfway between them (in dB), with hysteresis. That envelope is ~200 Hz wide, wide enough for a strong signal's
tone to wander +-100 Hz. A weak steady one (a distant beacon, ~10 dB clear in 50 Hz) is lost in it, so a narrow path runs alongside:
it follows the tone and averages 20 ms coherently, and takes over while a steady tone is there that the wide path cannot key.
Marks and spaces are timed against the dot length, which starts from the expected speed and then follows the signal. Short
drop-outs inside a mark (under 0.35 dot) are bridged, a carrier longer than 7 dots (a tuning or lead tone) is shown as [carrier],
not decoded.
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
    NARROW_N = 4                        # narrow path: 4 steps (20 ms) averaged coherently, ~50 Hz wide
    LOCK = 0.03                         # narrow path: weight of each step in the tone estimate (~0.7 s)
    COHERENT = 0.5                      # narrow path: coherence that means a steady tone (noise: ~0.1)

    def __init__(self, rate=48000, tone=700.0, wpm=12.0, emit=print):
        self.rate, self.tone, self.emit = rate, tone, emit
        self.n = int(rate * self.STEP)
        self.phase = 0.0
        self.buf = np.zeros(0, np.complex128)
        self.set_wpm(wpm)
        self.hi = self.lo = 1e-6        # peak and floor trackers (linear amplitude) of the envelope in use
        self.wide = [1e-6, 1e-6]        # [peak, floor] of each path
        self.narrow = [1e-6, 1e-6]
        self.use_narrow = False
        self.prev = 0j                  # the last 5 ms value, for the tone estimate
        self.lag = 0j                   # running mean of value * conj(previous value): its angle is the tone's offset
        self.lag_abs = 1e-12            # ...and of its magnitude: |lag| / lag_abs is near 1 for a steady tone, ~0.1 for noise
        self.coherence = 0.0
        self.offset = 0.0               # the tone's offset from `tone`, Hz
        self.theta = 0.0
        self.hist = np.zeros(self.NARROW_N, np.complex128)
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
            c = self.buf[:k * self.n].reshape(k, self.n).mean(axis=1)
            self.buf = self.buf[k * self.n:]
            for v in c:
                self._step(*self._paths(v))

    def _paths(self, v):
        """One 5 ms value of the mixed-down tone -> (wide, narrow) amplitudes. The narrow path follows the tone (the AFC centres a
        weak carrier only within tens of Hz, and a cold transmitter chirps): the mean of value * conj(previous) over ~0.7 s points at
        the tone, as long as there is one (coherence over COHERENT), and the narrow path mixes it to 0 Hz before averaging 20 ms."""
        p = v * np.conj(self.prev)
        self.prev = v
        self.lag += self.LOCK * (p - self.lag)
        self.lag_abs += self.LOCK * (abs(p) - self.lag_abs)
        self.coherence = abs(self.lag) / self.lag_abs
        if self.coherence > self.COHERENT:
            self.offset = float(np.angle(self.lag)) / (2 * np.pi * self.STEP)
        self.theta = (self.theta + 2 * np.pi * self.offset * self.STEP) % (2 * np.pi)
        self.hist = np.roll(self.hist, 1)
        self.hist[0] = v * np.exp(-1j * self.theta)
        return 2 * abs(v), 2 * abs(self.hist.mean())

    def _track(self, t, a, skip_marks=False):
        # Peak: instant attack, ~3 dB/s decay. Floor: the average noise, followed while the key is up (~100 ms) and held while it is
        # down (following minimums instead would lock onto the deepest noise dips and put the threshold inside the noise).
        a = max(a, 1e-5)                                     # digital silence would pin the floor at 0 for good
        if t[1] <= 1e-6:                                     # first reading
            t[0] = t[1] = a
        t[0] = a if a > t[0] else t[0] * 0.9983
        if not self.key:
            w = 0.005 if skip_marks and a > 2 * t[1] else 0.05
            t[1] = min(t[1] + w * (a - t[1]), t[0])
        return a

    def _step(self, a_wide, a_narrow):
        # A weak signal's marks that do not key would pull the floor up to them: on the narrow path, readings over twice (6 dB) the
        # floor count only a tenth (enough to climb out of a silence in ~1 s).
        a_wide, a_narrow = self._track(self.wide, a_wide), self._track(self.narrow, a_narrow, skip_marks=True)
        # The narrow path is for a steady tone (coherence) too weak for the wide one, and clearer in the narrow one. Its 20 ms
        # average smears the keying, so a strong signal stays on the wide path. The choice is made with the key up, so that a mark
        # is timed on one envelope.
        if not self.key:
            self.use_narrow = (self.coherence > self.COHERENT and self.wide[0] < 4 * self.wide[1]
                               and self.narrow[0] * self.wide[1] > self.wide[0] * self.narrow[1])
        a, (self.hi, self.lo) = (a_narrow, self.narrow) if self.use_narrow else (a_wide, self.wide)
        # At least 12 dB between peak and floor (8 dB on the narrow path: only a steady tone uses it, and its noise is 4x narrower)
        g = 2.5 if self.use_narrow else 4
        signal = self.hi > g * self.lo
        # Threshold halfway in dB, never closer than that to the floor. On the narrow path halfway in amplitude: the 20 ms average
        # ramps each edge, and only a threshold at half the peak keeps the marks from coming out long and the spaces short.
        mid = max(0.5 * self.hi if self.use_narrow else np.sqrt(self.hi * self.lo), g * self.lo)
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
