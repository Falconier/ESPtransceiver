"""Olivia receiver: audio in, characters out. Needs no lead tone, so it also decodes other stations (fldigi).

  spectra    every 1/8 symbol (4 ms) a 512-sample Hann window, zero-padded to 2048 points (3.9 Hz bins), of the 8 kHz audio;
             the spectra of the last 66 symbols are kept
  frequency  until a signal is found: the offset (within +-search Hz) where the comb of tone positions stands out most against
             the band just outside it, over the last 16 symbols (a signal gives 6-50 times the energy outside, noise about 1);
             then, every symbol, the strongest tone's peak is measured and a second-order loop follows offset and drift
  decoding   every symbol, the last 64 symbols are decoded at each of 8 timing sub-phases and at tone shifts of -2..+2 (the comb
             can lock a tone or two off before every tone has appeared); tone energies are taken from the kept spectra at the
             current offset, wound back by the drift rate, so the first block after the lock decodes too. The decode quality
             (peak / rms of the Walsh-Hadamard outputs) peaks at the true block boundary; a block is emitted at such a peak above
             `min_quality`, and a non-zero best shift corrects the offset by whole tones
"""
import numpy as np

from espdr import dsp

import olivia

HOPS = 8                                     # spectra per symbol
EDGE = 2                                     # extra tone positions kept on each side, for the shift search


class OliviaRx:
    def __init__(self, mode, rate=48000, emit=print, search=60.0, min_quality=5.0, complex_in=False):
        """complex_in: the input is complex baseband (the dial at 0 Hz, the tones at +tone_hz), processed at 16 kHz so the
        signal may drift several kHz; otherwise real audio, processed at 8 kHz."""
        self.m = mode
        self.emit = emit
        self.search, self.min_quality = search, min_quality
        self.complex = complex_in
        self.fs = 16000 if complex_in else olivia.FS
        self.rs = dsp.Resampler(rate, self.fs) if rate != self.fs else None
        self.sep = int(round(mode.symbol_s * self.fs))           # one symbol: 256 samples at 8 kHz, 512 at 16 kHz
        self.hop = self.sep // HOPS
        self.nfft = 4 * 2 * self.sep
        self.binhz = self.fs / self.nfft                         # 3.9 Hz either way
        self.win = np.hanning(2 * self.sep)
        self.buf = np.zeros(0)
        self.spectra = []                                        # one fine power spectrum per hop
        self.keep = HOPS * (olivia.BLOCK + 2)
        self.df = 0.0                                            # frequency correction, Hz
        self.rate = 0.0                                          # its drift per symbol, Hz
        self.locked = False
        self.locked_at = 0
        self.quiet = 0                                           # symbols without a clear tone
        self.streak, self.streak_df = 0, None                    # acquisition must agree for 8 symbols in a row
        self.streak_hist = []                                    # (symbol, offset) of the streak, for the drift rate
        self.hop_n = 0
        self.last_block = None                                   # symbol count at the last emitted block
        self.cands = []                                          # (symbol, quality, chars, shift) of the last 3 attempts
        self.quality = 0.0
        vals = np.array([olivia.ungray(n) for n in range(mode.tones)])
        self.bitmask = [((vals >> j) & 1).astype(bool) for j in range(mode.bits)]

    # -- input
    def process(self, x):
        x = np.asarray(x, dtype=complex if self.complex else float)
        if self.rs is not None:
            x = self.rs.process(x)
        self.buf = np.concatenate([self.buf, x])
        n = 2 * self.sep
        while len(self.buf) >= n:
            self._hop(self.buf[:n])
            self.buf = self.buf[self.hop:]

    def _hop(self, seg):
        w = seg * self.win
        p = np.abs(np.fft.fft(w, self.nfft) if self.complex else np.fft.rfft(w, self.nfft)) ** 2
        self.spectra.append(p)
        if len(self.spectra) > self.keep:
            del self.spectra[0]
        self.hop_n += 1
        if self.hop_n % HOPS:
            return
        if not self.locked:
            self._acquire()
            if not self.locked:
                return
        self._track()
        last = self.last_block if self.last_block is not None else self.locked_at
        if self.hop_n // HOPS - last > 3 * olivia.BLOCK:          # no block for 3 block lengths: search again
            self.locked, self.rate = False, 0.0
            return
        if len(self.spectra) >= HOPS * olivia.BLOCK:
            self._try_blocks()

    # -- energies at tone positions
    def _bins(self, f, n):
        """Frequencies (Hz) -> bin indices: wrapped for a complex spectrum (negative frequencies), clipped for a real one."""
        i = np.round(np.asarray(f) / self.binhz).astype(int)
        return i % n if self.complex else np.clip(i, 1, n - 2)

    def _gather(self, spectra, df):
        """spectra (H x bins), df (H,) -> energies (H x tones+2*EDGE) at tone positions -EDGE .. tones-1+EDGE."""
        f = self.m.tone_hz(np.arange(-EDGE, self.m.tones + EDGE))[None, :] + np.asarray(df)[:, None]
        n = spectra.shape[1]
        i = self._bins(f, n)
        r = np.arange(len(spectra))[:, None]
        return np.maximum(spectra[r, i], np.maximum(spectra[r, (i - 1) % n], spectra[r, (i + 1) % n]))

    def _comb(self, avg, df, steps):
        f = self.m.tone_hz(np.asarray(steps, dtype=float)) + df
        n = len(avg)
        i = self._bins(f, n)
        return np.maximum(avg[i], np.maximum(avg[(i - 1) % n], avg[(i + 1) % n])).mean()

    # -- frequency
    def _acquire(self):
        if len(self.spectra) < HOPS * 16:
            return
        avg = np.mean(self.spectra[-HOPS * 16:], axis=0)
        n = self.m.tones
        inside, outside = np.arange(n), np.array([-2, -1, n, n + 1])
        best = None
        for df in np.arange(-self.search, self.search + 0.1, self.binhz / 2):
            s = self._comb(avg, df, inside) - self._comb(avg, df, outside)
            if best is None or s > best[0]:
                best = (s, df)
        _, df = best
        tone, out = self._comb(avg, df, inside), self._comb(avg, df, outside)
        # Olivia uses all its tones (the data is scrambled); a lead or tune tone is one. Requiring at least half of the tones to
        # carry energy keeps a steady carrier from locking the grid a fraction of a tone off.
        e = np.array([self._comb(avg, df, [n_]) for n_ in range(n)])
        many = np.count_nonzero(e > 0.1 * e.max()) >= n // 2 if e.max() > 0 else False
        # The same answer for 8 symbols in a row: the step at a signal's start spreads energy over every tone for a moment and
        # once locked the grid ~100 Hz off, which garbled the first block.
        ok = tone > 3 * out and tone > 0 and many
        sym = self.hop_n // HOPS
        if ok and self.streak_df is not None and abs(df - self.streak_df) <= 3 * self.binhz:
            self.streak += 1
            self.streak_hist.append((sym, df))
        else:
            self.streak = 1 if ok else 0
            self.streak_hist = [(sym, df)] if ok else []
        self.streak_df = df if ok else None
        if self.streak >= 8:
            # the 8 offsets of the streak also give the drift rate (a line through them): the loop starts right instead of
            # learning 4.7 Hz per symbol (150 Hz/s) from zero, which a steady loop does too slowly
            s_, d_ = np.array(self.streak_hist[-8:]).T
            rate0 = float(np.polyfit(s_, d_, 1)[0])
            self.streak, self.streak_df, self.streak_hist = 0, None, []
            # the 16-symbol average describes the signal ~8 symbols ago: move on by 8 x the rate to now
            self.df, self.rate, self.locked = df + 8 * rate0, float(np.clip(rate0, -10.0, 10.0)), True
            self.locked_at = self.hop_n // HOPS
            self.last_block, self.cands, self.quiet = None, [], 0

    def _track(self):
        """Second-order loop on the strongest tone of the latest symbol (100 Hz/s is 3.2 Hz per symbol)."""
        self.df += self.rate
        # Measure on the cleanest of the last 8 spectra: a window spans two symbol periods, so most hold two tones at once, and
        # with the transmitter's hard tone steps the peak then sits between them, which the loop took for drift (it wandered
        # 45 Hz off on a signal without any).
        last = np.array(self.spectra[-HOPS:])
        ee = self._gather(last, np.full(len(last), self.df))[:, EDGE:EDGE + self.m.tones]
        top2 = np.sort(ee, axis=1)[:, -2:]
        h = int(np.argmax(top2[:, 1] / (top2[:, 0] + 1e-30)))
        p, e = last[h], ee[h]
        n = int(np.argmax(e))
        if e[n] < 4 * np.median(e):                               # no clear tone: let the rate fade
            self.quiet += 1
            self.rate *= 0.9
            return
        self.quiet = 0
        b = self.m.tone_hz(n) + self.df
        half = self.m.spacing / 2 / self.binhz
        lo, hi = int(b / self.binhz - half), int(b / self.binhz + half) + 1
        if not self.complex and (lo < 1 or hi >= len(p) - 1):
            return
        k = lo + int(np.argmax(p.take(np.arange(lo, hi), mode="wrap")))     # k: unwrapped bin (may be negative)
        a, c, d = np.log(p.take([k - 1, k, k + 1], mode="wrap") + 1e-30)   # Gaussian interpolation (log): exact-ish for Hann
        frac = 0.5 * (a - d) / (a - 2 * c + d) if (a - 2 * c + d) != 0 else 0.0
        err = float(np.clip((k + frac) * self.binhz - b, -self.m.spacing / 2, self.m.spacing / 2))
        self.df += 0.2 * err
        self.rate = float(np.clip(self.rate + 0.015 * err, -10.0, 10.0))

    # -- decoding
    def _soft(self, e):
        out = np.empty((len(e), self.m.bits))
        for j, one in enumerate(self.bitmask):
            e0, e1 = e[:, ~one].max(axis=1), e[:, one].max(axis=1)
            out[:, j] = (e0 - e1) / (e0 + e1 + 1e-30)
        return out

    def _try_blocks(self):
        s = np.array(self.spectra[-HOPS * olivia.BLOCK:])
        age = (len(s) - 1 - np.arange(len(s))) / HOPS              # symbols before now
        e = self._gather(s, self.df - self.rate * age)
        best = None
        n = self.m.tones
        for ph in range(HOPS):
            idx = np.arange(len(e) - 1 - ph, -1, -HOPS)[:olivia.BLOCK][::-1]
            if len(idx) < olivia.BLOCK:
                continue
            for shift in range(-EDGE, EDGE + 1):
                chars = self.m.decode_block(self._soft(e[idx, EDGE + shift:EDGE + shift + n]))
                q = float(np.mean([c[1] for c in chars]))
                if best is None or q > best[0]:
                    best = (q, chars, shift)
        q, chars, shift = best
        self.quality = q
        sym = self.hop_n // HOPS
        # A block is taken where the quality is the best within +-HALF symbols (decided HALF symbols later): a lead tone made
        # weak false peaks (quality ~4.5) a few symbols before the true boundary (13 and more), which a 3-point peak let through.
        # Each candidate is decided once it has HALF later neighbours; the earlier ones are whatever exists (right after a lock
        # there are few, and waiting for HALF of them lost the first block).
        half = olivia.BLOCK // 2
        self.cands.append((sym, q, chars, shift))
        j = len(self.cands) - 1 - half                              # the candidate decided now
        if j < 0:
            return
        window = self.cands[max(0, j - half):]
        s1, q1, c1, sh1 = self.cands[j]
        if len(self.cands) > 4 * half:
            del self.cands[0]
        if q1 < self.min_quality or q1 < max(c[1] for c in window):
            return
        if self.last_block is not None and s1 - self.last_block < olivia.BLOCK - 4:
            return
        self.last_block = s1
        if sh1:
            self.df += sh1 * self.m.spacing                         # the comb was a tone or two off
        for c, cq in c1:                                            # squelch: noise blocks reach quality ~4 now and then
            if c and (c >= 32 or c in (10, 13)) and cq >= 4.0:
                self.emit(chr(c))
