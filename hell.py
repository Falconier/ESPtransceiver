"""Feld Hellschreiber for the ESP32-S3 CW transceiver: text to keyed audio, and received audio to image columns.

Feld Hell sends text as a picture. Each character is a dot-matrix glyph (here 5 x 7 in 7 columns, so two blank columns separate
characters), sent column by column, each column from the bottom up. Every glyph row is sent as two equal elements, so a column
has 14 elements. Timing: 2.5 characters per second, a column lasts 1 / 17.5 s = 57.14 ms, an element 4.08 ms (pixel rate 122.5
baud). The carrier is simply on or off (on-off keying), like CW.

The receiver does not decode: it measures the level of the tone in 28 slots per column (twice the element rate) and hands each
column to a display that draws it. People read the result; misalignment only slants the text.
"""
import numpy as np

COLUMN_S = 1 / 17.5                   # 2.5 characters/s x 7 columns
ELEMENTS = 14                         # per column: 7 rows, each sent twice
ROWS_RX = 28                          # receive slots per column (2 x oversampled)

_FONT = {
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."],
    "D": ["###..", "#..#.", "#...#", "#...#", "#...#", "#..#.", "###.."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "G": [".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".####"],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": [".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "J": ["..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."],
    "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"],
    "N": ["#...#", "#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "Q": [".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "V": ["#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "W": ["#...#", "#...#", "#...#", "#.#.#", "#.#.#", "#.#.#", ".#.#."],
    "X": ["#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"],
    "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."],
    "Z": ["#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
    "0": [".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    "3": ["#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
    "5": ["#####", "#....", "####.", "....#", "....#", "#...#", ".###."],
    "6": ["..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."],
    "/": [".....", "....#", "...#.", "..#..", ".#...", "#....", "....."],
    "?": [".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."],
    ".": [".....", ".....", ".....", ".....", ".....", ".##..", ".##.."],
    ",": [".....", ".....", ".....", ".....", ".##..", "..#..", ".#..."],
    "-": [".....", ".....", ".....", "#####", ".....", ".....", "....."],
    "=": [".....", ".....", "#####", ".....", "#####", ".....", "....."],
    "+": [".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."],
    " ": ["....."] * 7,
}
CHARS = set(_FONT)


def columns(text):
    """Text -> list of columns; each column is 7 booleans from the bottom row up. 5 glyph columns + 2 blank per character."""
    cols = []
    for ch in text.upper():
        rows = _FONT.get(ch)
        if rows is None:
            continue
        for x in range(5):
            cols.append([rows[6 - y][x] == "#" for y in range(7)])
        cols += [[False] * 7, [False] * 7]
    return cols


def seconds(text):
    return len(columns(text)) * COLUMN_S


def audio(text, tone=700.0, rate=8000, level=0.5, ramp_s=0.0005):
    """Keyed tone for `text`: every row bit becomes two elements of 4.08 ms; edges get short raised-cosine ramps."""
    cols = columns(text)
    n = int(round(len(cols) * COLUMN_S * rate))
    key = np.zeros(n)
    elem = COLUMN_S / ELEMENTS
    for c, col in enumerate(cols):
        for y, on in enumerate(col):
            if on:
                for half in (0, 1):
                    k = c * ELEMENTS + 2 * y + half
                    key[int(round(k * elem * rate)):int(round((k + 1) * elem * rate))] = 1.0
    r = max(1, int(ramp_s * rate))
    win = np.hanning(2 * r + 1)
    key = np.convolve(key, win / win.sum(), "same")
    return level * key * np.sin(2 * np.pi * tone * np.arange(n) / rate)


LEAD_MIN_S = 1.0                      # a steady carrier at least this long is a lead (or tune) tone
LEAD_GAP_S = 0.5                      # our transmitter: lead tone, then 0.5 s of silence, then the first column


class HellRx:
    """Receive audio (48 kHz, the carrier held at `tone` by the AFC) -> image columns of ROWS_RX levels 0..1, bottom first.

    Column phase: a receiver does not know where the sender's columns begin, so glyphs come out cut at an arbitrary height. With
    `auto` on, the end of a lead tone (a steady carrier of LEAD_MIN_S or more) re-aligns the columns: the first column starts
    LEAD_GAP_S after it, as this project's transmitter sends it."""

    def __init__(self, rate=48000, tone=700.0, emit=print, auto=True):
        self.rate, self.tone, self.emit, self.auto = rate, tone, emit, auto
        self.bin = COLUMN_S / ROWS_RX * rate          # samples per slot (fractional)
        self.phase = 0.0
        self.count = 0.0                              # samples since the current slot began
        self.acc = 0j
        self.n = 0
        self.col = []
        self.peak = 1e-6
        self.on_slots = 0                             # consecutive slots with the carrier on
        self.resync = None                            # samples until the first column after a lead tone
        self.synced = 0                               # how many times the columns were re-aligned

    def process(self, x):
        x = np.asarray(x, dtype=float)
        if not len(x):
            return
        w = 2 * np.pi * self.tone / self.rate
        z = x * np.exp(-1j * (self.phase + w * np.arange(len(x))))
        self.phase = (self.phase + w * len(x)) % (2 * np.pi)
        i = 0
        while i < len(z):
            take = min(len(z) - i, int(np.ceil(self.bin - self.count)))
            if self.resync is not None:
                take = max(1, min(take, int(np.ceil(self.resync))))
            self.acc += z[i:i + take].sum()
            self.n += take
            self.count += take
            i += take
            if self.resync is not None:
                self.resync -= take
                if self.resync <= 0:                  # the first column of the message starts here
                    self.resync = None
                    self.count, self.acc, self.n, self.col = 0.0, 0j, 0, []
                    self.synced += 1
                    continue
            if self.count >= self.bin:
                self.count -= self.bin
                self._slot(2 * abs(self.acc) / max(self.n, 1))
                self.acc, self.n = 0j, 0

    def _slot(self, a):
        self.peak = max(a, self.peak * 0.9995)         # slow AGC: about 10 s to fall 20 dB
        if a > 0.5 * self.peak:
            self.on_slots += 1
        else:
            if self.auto and self.on_slots * self.bin >= LEAD_MIN_S * self.rate:
                # The lead tone ended in this slot: its level is the share of the slot the carrier was still on, which places the
                # end inside the slot (taking the middle left the columns up to a slot off). The first column follows LEAD_GAP_S later;
                # the extra slot makes up for the lead tone's 5 ms fade-out and the slot averaging (measured: 0 slots error at 10
                # start offsets with it, a constant -1 without).
                on_share = min(1.0, a / self.peak)
                self.resync = LEAD_GAP_S * self.rate - (1.0 - on_share) * self.bin + self.bin
            self.on_slots = 0
        self.col.append(min(1.0, a / self.peak))
        if len(self.col) == ROWS_RX:
            self.emit(np.array(self.col))
            self.col = []
