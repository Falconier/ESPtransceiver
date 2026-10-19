"""Renders Hell receive columns to a PNG (two stacked copies, as Hell displays do). No dependencies beyond numpy.

  from scripts.hell_png import save_png; save_png(columns, "out.png")
"""
import struct
import zlib

import numpy as np


def image(columns, scale=3):
    """columns: list of arrays (bottom row first, values 0..1) -> uint8 grey image, black ink on white, two copies stacked."""
    a = np.array(columns).T[::-1]                  # rows top-down, one column per received column
    a = np.vstack([a, a])
    g = (255 * (1 - np.clip(a, 0, 1))).astype(np.uint8)
    # a glyph row is 4 slots tall but a glyph column only 1 column wide: widen columns 4x so letters keep their shape
    return np.kron(g, np.ones((scale, 4 * scale), np.uint8))


def save_png(columns, path, scale=3):
    g = image(columns, scale)
    h, w = g.shape
    raw = b"".join(b"\x00" + g[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
