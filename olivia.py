"""Olivia MFSK: the coding layer and a reference modulator (8/250, 16/500 and the other standard tone/bandwidth pairs).

Follows Pawel Jalocha's Olivia as implemented in fldigi (src/include/jalocha/pj_mfsk.h), written anew for numpy:

  character  7-bit ASCII -> 64 chips: a +-1 vector with one non-zero entry (index = char, or char-64 with a minus sign),
             through the inverse Walsh-Hadamard transform
  scrambling the chips of the k-th character of a block are multiplied by -1 where SCRAMBLE has a 1 bit, starting at bit
             13*k (mod 64) and wrapping
  block      bits-per-symbol characters (3 for 8 tones, 4 for 16) make 64 symbols; chip t of character k sets bit
             (k + t) mod bits of symbol t when the chip is negative
  tones      the symbol is Gray-coded; tone n lies at first + n*spacing, spacing = bandwidth/tones (31.25 Hz for 8/250 and
             16/500); a symbol lasts 1/spacing (32 ms) and its tone is shaped by a raised cosine over two symbol periods
"""
import numpy as np

SCRAMBLE = 0xE257E6D0291574EC
SHIFT = 13
BLOCK = 64                                   # symbols per block = chips per character
FS = 8000                                    # reference sample rate
MODES = {"8/250": (8, 250.0), "16/500": (16, 500.0), "4/125": (4, 125.0), "32/1000": (32, 1000.0)}


def ifht(v):
    """Inverse fast Walsh-Hadamard transform (fldigi's IFHT butterfly: a, b -> a-b, a+b), on the last axis."""
    v = np.array(v, dtype=float)
    n = v.shape[-1]
    step = n // 2
    while step:
        w = v.reshape(v.shape[:-1] + (n // (2 * step), 2, step))
        a, b = w[..., 0, :].copy(), w[..., 1, :].copy()
        w[..., 0, :], w[..., 1, :] = a - b, a + b
        step //= 2
    return v


def fht(v):
    """Forward transform (fldigi's FHT butterfly: a, b -> a+b, b-a), on the last axis; fht(ifht(x)) = 64 * x."""
    v = np.array(v, dtype=float)
    n = v.shape[-1]
    step = 1
    while step < n:
        w = v.reshape(v.shape[:-1] + (n // (2 * step), 2, step))
        a, b = w[..., 0, :].copy(), w[..., 1, :].copy()
        w[..., 0, :], w[..., 1, :] = a + b, b - a
        step *= 2
    return v


_SCR = np.array([(SCRAMBLE >> i) & 1 for i in range(64)], dtype=bool)


def scramble(v, k):
    """Sign flips of the k-th character of a block (its own inverse)."""
    idx = (np.arange(BLOCK) + k * SHIFT) % BLOCK
    out = np.array(v, dtype=float)
    out[_SCR[idx]] *= -1
    return out


def gray(n):
    return n ^ (n >> 1)


def ungray(g):
    n = 0
    while g:
        n ^= g
        g >>= 1
    return n


class Mode:
    def __init__(self, name="8/250", center=700.0):
        self.name = name
        self.tones, self.bw = MODES[name]
        self.bits = int(np.log2(self.tones))
        self.spacing = self.bw / self.tones                       # 31.25 Hz for 8/250 and 16/500
        self.symbol_s = 1.0 / self.spacing                        # 32 ms
        self.center = center
        self.first = center - self.bw * (1.0 - 0.5 / self.tones) / 2.0   # as fldigi places the band around its cursor

    def tone_hz(self, n):
        return self.first + n * self.spacing

    @property
    def chars_per_s(self):
        return self.bits / (BLOCK * self.symbol_s)

    # -- coding
    def encode_block(self, chars):
        """`bits` character codes -> 64 symbols (before Gray coding)."""
        out = np.zeros(BLOCK, dtype=int)
        for k, c in enumerate(chars):
            c &= 0x7F
            e = np.zeros(BLOCK)
            e[c % BLOCK] = 1.0 if c < BLOCK else -1.0
            chips = scramble(ifht(e), k)
            t = np.nonzero(chips < 0)[0]
            out[t] |= 1 << ((k + t) % self.bits)
        return out

    def decode_block(self, soft):
        """soft: 64 x bits array, positive = bit 0 (chip +1). Returns [(char, quality)], quality = peak / rms of the rest."""
        out = []
        t = np.arange(BLOCK)
        for k in range(self.bits):
            v = scramble(soft[t, (k + t) % self.bits], k)
            w = fht(v)
            p = int(np.argmax(np.abs(w)))
            rest = np.delete(w, p)
            q = abs(w[p]) / (np.sqrt(np.mean(rest ** 2)) + 1e-12)
            out.append(((p + (BLOCK if w[p] < 0 else 0)) & 0x7F, q))
        return out

    def symbols(self, text):
        """Text -> symbol stream (Gray-coded tone numbers), padded with NUL to whole blocks."""
        codes = [ord(c) & 0x7F for c in text]
        codes += [0] * (-len(codes) % self.bits)
        syms = []
        for i in range(0, len(codes), self.bits):
            syms.extend(gray(int(s)) for s in self.encode_block(codes[i:i + self.bits]))
        return syms

    # -- transmitter records (esp32-sdr-trx: one frequency offset and one gain code per record, here 8000 records/s)
    def tx_records(self, text, offset_hz, code, lead_s=2.0, rate=8000):
        """Records for `text`: a steady lead tone at the band's centre (the receiver's AFC locks on it), then one tone per symbol.

        The chip makes one carrier at a time, so the tones follow each other phase-continuously at a constant level instead of
        the standard's overlapping raised-cosine symbols; the receiver's spectra see the same tone per symbol. A record's
        frequency field counts in steps of 28.6 Hz (Q4_HZ), coarser than the 31.25 Hz tone spacing allows, so the fields carry
        the rounding error forward (error feedback): each record is within a step, the running sum is exact, and every tone's
        average frequency is exact. `offset_hz`: the dial against the chip's LO (Transmitter.offset_hz)."""
        from espdr import txmodes
        sep = int(round(self.symbol_s * rate))
        hz = [np.full(int(lead_s * rate), self.center)] if lead_s else []
        hz += [np.full(sep, self.tone_hz(t)) for t in self.symbols(text)]
        target = (np.concatenate(hz) + offset_hz) / txmodes.Q4_HZ
        exact = np.cumsum(target)
        q4 = np.diff(np.round(exact), prepend=0.0)
        return txmodes.pack(q4, np.full(len(q4), code))

    # -- reference modulator (the overlapping raised-cosine symbols of the standard; for tests and interop)
    def audio(self, text, rate=FS, level=0.5, seed=1):
        tones = self.symbols(text)
        sep = int(round(self.symbol_s * rate))
        n = len(tones) * sep + 2 * sep
        y = np.zeros(n)
        win = 0.5 * (1 - np.cos(2 * np.pi * np.arange(2 * sep) / (2 * sep)))
        rng = np.random.default_rng(seed)
        phase = 0.0
        for i, tone in enumerate(tones):
            f = self.tone_hz(tone)
            t = np.arange(2 * sep) / rate
            y[i * sep:i * sep + 2 * sep] += win * np.cos(2 * np.pi * f * t + phase)
            phase = (phase + 2 * np.pi * f * sep / rate + rng.choice([-0.5, 0.5]) * np.pi) % (2 * np.pi)
        return level * y / max(1e-9, np.abs(y).max())
