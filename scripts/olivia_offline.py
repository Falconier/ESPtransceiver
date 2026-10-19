"""Offline Olivia tests: reference audio with noise, tuning error and drift, decoded by olivia_rx at 48 kHz.

  python scripts/olivia_offline.py
"""
import sys

import numpy as np

sys.path.insert(0, ".")
import olivia                                      # noqa: E402
import olivia_rx                                   # noqa: E402

FS = 48000
TEXT = "CQ CQ DE W4DQB/EXP OLIVIA TEST 0123"


def make(mode, offset_hz=0.0, drift_hz_s=0.0, snr_db=None, start=12345, seed=0):
    """Reference Olivia audio at 48 kHz, frequency moved by offset + drift * t (applied as a phase ramp on the analytic signal)."""
    m = olivia.Mode(mode)
    a = m.audio(TEXT, rate=FS, seed=seed)
    x = np.concatenate([np.zeros(start), a, np.zeros(3 * FS)])     # 3 s after: the decision looks ~1 s ahead
    if offset_hz or drift_hz_s:
        from scipy.signal import hilbert
        t = np.arange(len(x)) / FS
        x = np.real(hilbert(x) * np.exp(2j * np.pi * (offset_hz * t + 0.5 * drift_hz_s * t * t)))
    if snr_db is not None:
        # noise power in a 3 kHz bandwidth relative to the signal's average power
        p = np.mean(a ** 2)
        n = np.sqrt(p / 10 ** (snr_db / 10) * (FS / 2) / 3000)
        x = x + n * np.random.default_rng(seed + 1).standard_normal(len(x))
    return x


def run(mode, **kw):
    out = []
    rx = olivia_rx.OliviaRx(olivia.Mode(mode), rate=FS, emit=out.append)
    x = make(mode, **kw)
    for i in range(0, len(x), 4800):
        rx.process(x[i:i + 4800])
    return "".join(out), rx


if __name__ == "__main__":
    cases = [dict(), dict(snr_db=0), dict(offset_hz=23.0), dict(drift_hz_s=100.0), dict(offset_hz=-31.0, drift_hz_s=100.0, snr_db=3)]
    for mode in ("8/250", "16/500"):
        for kw in cases:
            text, rx = run(mode, **kw)
            ok = "OK " if text.rstrip() == TEXT else "ERR"
            desc = ", ".join(f"{k}={v}" for k, v in kw.items()) or "clean"
            print(f"{mode:6s} {desc:44s} {ok} {text!r}  (df {rx.df:+.1f} Hz)")
