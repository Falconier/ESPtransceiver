"""Offline checks of live Olivia transmit (typing while transmitting), without boards.

  1  RecordStream's pieces (lead, then block by block) give exactly tx_records' records
  2  a typed session goes through trx_board.Transceiver's live path (set_pending / _take, idle NUL blocks while nothing is typed,
     edits of unsent text) to records, which are turned into the carrier the chip would make and decoded by olivia_rx

  python scripts/olivia_live_check.py
"""
import sys
import threading

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "repos/esp32-sdr-trx/src")
import olivia                                      # noqa: E402
import olivia_rx                                   # noqa: E402
import trx_board                                   # noqa: E402
from espdr import txmodes                          # noqa: E402

RATE = 8000
OFFSET = 1234.0                                    # the dial against the chip's LO, Hz


def check_equal(name):
    m = olivia.Mode(name)
    text = "CQ CQ DE W4DQB/EXP " * 2
    text += " " * (-len(text) % m.bits)
    ref = m.tx_records(text, OFFSET, 70, lead_s=2.0, rate=RATE)
    rs = m.record_stream(OFFSET, 70, rate=RATE)
    parts = [rs.lead(2.0)] + [rs.block(text[i:i + m.bits]) for i in range(0, len(text), m.bits)]
    got = np.concatenate(parts)
    # the running sums are summed in another order, so a field at a rounding tie can move by one step to its neighbour
    q = [np.cumsum((r & 0xFFFF).astype(np.uint16).view(np.int16).astype(int)) for r in (ref, got)]
    assert len(ref) == len(got) and np.abs(q[0] - q[1]).max() <= 1 and q[0][-1] == q[1][-1], f"{name}: the streamed records differ"
    print(f"{name}: streamed records match tx_records ({len(got)} records, {np.count_nonzero(ref != got)} at a rounding tie)")


class FakeTrx:
    """Only the parts of Transceiver that set_pending / _take use."""
    def __init__(self):
        self.live_lock = threading.Lock()
        self.live_text, self.live_base, self.live_sent = "", 0, 0
    set_pending = trx_board.Transceiver.set_pending
    _take = trx_board.Transceiver._take


def carrier(recs, snr_db=10.0, seed=0):
    """Records -> the complex baseband the far receiver sees once the LO offset is removed (one record per 1/8000 s)."""
    q4 = (recs & 0xFFFF).astype(np.uint16).view(np.int16).astype(float)
    f = q4 * txmodes.Q4_HZ - OFFSET
    z = np.exp(2j * np.pi * np.cumsum(f) / RATE)
    z = np.concatenate([np.zeros(RATE), z, np.zeros(3 * RATE)])
    rng = np.random.default_rng(seed)
    n = np.sqrt(1 / 10 ** (snr_db / 10) * (RATE / 2) / 3000)          # SNR in 3 kHz
    return z + n * (rng.standard_normal(len(z)) + 1j * rng.standard_normal(len(z))) / np.sqrt(2)


def check_session(name):
    m = olivia.Mode(name, center=700.0)
    rs = m.record_stream(OFFSET, 70, rate=RATE)
    trx = FakeTrx()
    window_text, acked = "", 0                     # what the window holds after the sent mark, and its count of sent characters

    def window_poll():                             # the window marks what was taken as sent and offers the rest again
        nonlocal window_text, acked
        n = trx.live_sent - acked
        window_text, acked = window_text[n:], acked + n
        trx.set_pending(window_text, acked)

    # (block number, what the user does before that block is built)
    script = {0: ("type", "CQ CQ DE W4DQB"), 4: ("type", "/EXP OLIVIA TEZT"), 7: ("backspace", 2),
              8: ("type", "ST K"), 16: ("type", " DE W4DQB SK")}
    recs, blk, ending = [rs.lead(2.0)], 0, False
    sent = ""
    while True:
        act = script.get(blk)
        if act:
            # the keystroke reaches the transceiver before the window has seen the last take: base lags behind live_sent
            if act[0] == "type":
                window_text += act[1]
            else:
                window_text = window_text[:-act[1]] if len(window_text) >= act[1] else window_text
            trx.set_pending(window_text, acked)
        chars = trx._take(m.bits)
        if blk == 16:
            ending = True
        if not chars and ending:
            break
        sent += chars
        recs.append(rs.block(chars))
        if blk % 2:                                # the window polls between some of the blocks only
            window_poll()
        blk += 1
    want = "CQ CQ DE W4DQB/EXP OLIVIA TEST K DE W4DQB SK"
    print(f"{name}: sent {sent!r} in {blk} blocks ({blk * olivia.BLOCK * m.symbol_s:.0f} s, idle blocks included)")
    assert sent == want, f"sent {sent!r}"
    out = []
    rx = olivia_rx.OliviaRx(m, rate=RATE, emit=out.append, complex_in=True, search=m.bw + 300)
    z = carrier(np.concatenate(recs))
    for i in range(0, len(z), 800):
        rx.process(z[i:i + 800])
    got = "".join(out)
    print(f"{name}: decoded {got!r}")
    assert want in got, "the decode differs"


for name in ("8/250", "16/500"):
    check_equal(name)
    check_session(name)
print("ok")
