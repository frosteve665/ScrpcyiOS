"""Undo the iPhone encoder's "resolution collapse".

Under fast motion (a swipe in a game) the device encoder drops to a smaller capture size and
draws it into the TOP-LEFT of the fixed-size buffer; the rest is flat gray (Y=Cb=Cr=128). The
shrunken region is still the whole screen. Detect that rectangle so the player can stretch it
back. Detection runs on a small downsample of the RAW frame, never on an already-corrected one.

Behaviour mirrors pymobiledevice3's web viewer (resources/serve_web/viewer.js, detectContentCrop).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

DET_H = 344          # downsample height used for detection
GRAY = 128
TOL = 6              # |channel - 128| below this is padding
GRAY_FRAC = 0.6      # a column/row is padding if at least this share of samples is gray
MIN_FRAC, MAX_FRAC = 0.2, 0.92   # only a clear collapse in BOTH axes counts


def detect_content_crop(rgb: np.ndarray) -> Optional[tuple[float, float]]:
    """Return (fx, fy): the fraction of width/height holding real content, or None if not collapsed.

    rgb is an HxWx3 uint8 array (a downsample is fine and much cheaper).
    """
    h, w = rgb.shape[:2]
    if h < 8 or w < 8:
        return None
    gray = (np.abs(rgb.astype(np.int16) - GRAY) < TOL).all(axis=2)
    col_gray = gray[::4, :].mean(axis=0)          # per column, sampling every 4th row
    row_gray = gray[:, ::4].mean(axis=1)          # per row, sampling every 4th column
    cols = np.nonzero(col_gray < GRAY_FRAC)[0]
    rows = np.nonzero(row_gray < GRAY_FRAC)[0]
    if cols.size == 0 or rows.size == 0:
        return None
    fx, fy = cols[-1] / w, rows[-1] / h
    if MIN_FRAC < fx < MAX_FRAC and MIN_FRAC < fy < MAX_FRAC:
        return float(fx), float(fy)
    return None


def crop_box(frac: Optional[tuple[float, float]], width: int, height: int) -> tuple[int, int, int, int]:
    """Pixel box (left, top, right, bottom) to crop from a full frame; the whole frame if frac is None."""
    if frac is None:
        return 0, 0, width, height
    return 0, 0, max(1, round(frac[0] * width)), max(1, round(frac[1] * height))
