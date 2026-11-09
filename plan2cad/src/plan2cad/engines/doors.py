"""Automatic door detection: fit circles to the leftover arc pixels (after straight lines and text are removed)."""
from __future__ import annotations

import math

import cv2
import numpy as np


def _fit(xs, ys):
    A = np.c_[2 * xs, 2 * ys, np.ones(len(xs))]
    cx, cy, c = np.linalg.lstsq(A, xs ** 2 + ys ** 2, rcond=None)[0]
    return cx, cy, math.sqrt(max(c + cx ** 2 + cy ** 2, 0.0))


def residual_mask(core, im):
    """Line-work pixels that are not part of any straight line segment (arcs, text, ticks)."""
    line_mask = np.zeros_like(im["ink"])
    for s in core.segments(im["ink"], "ink"):
        (ax, ay), (bx, by) = core.seg_line(s).coords
        cv2.line(line_mask, (int(round(ax)), int(round(ay))), (int(round(bx)), int(round(by))), 1, s["t"] + 2)
    return (im["ink_light"] & (1 - line_mask)).astype(np.uint8)


def detect_door_boxes(core, text_boxes=(), min_r=12.0, max_r=45.0) -> dict:
    """Return {id: [x0, y0, x1, y1]} for every quarter-circle-like arc found. Ids are assigned top-to-bottom."""
    im = core.load()
    res = residual_mask(core, im)
    H, W = res.shape
    for x0, y0, x1, y1 in text_boxes:
        res[max(0, y0 - 1):min(H, y1 + 2), max(0, x0 - 1):min(W, x1 + 2)] = 0
    n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(res, np.ones((3, 3), np.uint8)), 8)
    found = []
    for i in range(1, n):
        x, y, w, h, _ = st[i]
        if not (0.6 * min_r <= w <= 1.4 * max_r and 0.6 * min_r <= h <= 1.4 * max_r):
            continue
        ys, xs = np.where((lab == i) & (res > 0))
        if len(xs) < 12:
            continue
        for _ in range(2):                           # second pass drops outliers (leaf stubs, stray ticks)
            cx, cy, r = _fit(xs, ys)
            keep = np.abs(np.hypot(xs - cx, ys - cy) - r) < 1.5
            if keep.sum() < 10:
                break
            xs, ys = xs[keep], ys[keep]
        if len(xs) < 12:
            continue
        cx, cy, r = _fit(xs, ys)
        resid = np.abs(np.hypot(xs - cx, ys - cy) - r)
        if not (min_r <= r <= max_r) or resid.mean() > 0.8:
            continue
        ang = np.sort(np.degrees(np.arctan2(ys - cy, xs - cx)) % 360)
        span = 360 - float(np.max(np.diff(np.r_[ang, ang[0] + 360])))
        if not (50 <= span <= 125):
            continue
        found.append((y, x, [int(x) - 2, int(y) - 2, int(x + w) + 2, int(y + h) + 2]))
    found.sort()
    return {f"D{k + 1:02d}": b for k, (_, _, b) in enumerate(found)}
