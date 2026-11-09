"""Automatic verification of the output against the *source image* (replaces the manual overlay check).

The output line work is mapped back into source pixels and sampled every ~1 px. Each sample's distance to the nearest
source line-work pixel is measured (distance transform). A zone (cell of a grid over the drawing) passes when at least
`min_share` of its samples lie within `tolerance_px` of the drawing. Samples that do not are drawn red in the overlay.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
import shapely
from PIL import Image, ImageDraw

from .check import layer_names, read_layer

SKIP_LAYERS = {"rooms", "annotations"}


@dataclass
class VerifyResult:
    tolerance_px: float
    min_share: float
    n_samples: int = 0
    share_within: float = 0.0
    p95_px: float = 0.0
    coverage: float = 0.0
    zones: list = field(default_factory=list)
    passed: bool = False
    overlay: str = ""
    message: str = ""

    def to_dict(self):
        d = dict(self.__dict__)
        d["zones"] = list(self.zones)
        return d


def source_masks(path):
    rgb = np.asarray(Image.open(path).convert("RGB")).astype(np.int16)
    gray = rgb.mean(2)
    bg = float(np.percentile(gray, 90))
    ink = (gray < bg - 20).astype(np.uint8)
    return rgb, ink


def _lines_px(gdf, to_px):
    """Yield (N, 2) pixel arrays for every line / polygon ring of a layer."""
    for g in gdf.geometry:
        if g is None or g.is_empty:
            continue
        parts = shapely.get_parts(g)
        for p in parts:
            if p.geom_type == "Polygon":
                rings = [p.exterior, *p.interiors]
            elif p.geom_type in ("LineString", "LinearRing"):
                rings = [p]
            else:
                continue
            for r in rings:
                c = np.asarray(r.coords)[:, :2]
                if len(c) >= 2:
                    yield np.array([to_px(x, y) for x, y in c])


def _sample(poly):
    seg = np.diff(poly, axis=0)
    ln = np.hypot(seg[:, 0], seg[:, 1])
    out = []
    for (x0, y0), (dx, dy), L in zip(poly[:-1], seg, ln):
        n = max(1, int(np.ceil(L)))
        t = np.arange(n) / n
        out.append(np.c_[x0 + dx * t, y0 + dy * t])
    return np.vstack(out) if out else np.empty((0, 2))


def verify(source, gpkg, frame, tolerance_px=3.0, min_share=0.85, grid=(3, 3), min_samples=40, overlay_path=None):
    rgb, ink = source_masks(source)
    H, W = ink.shape
    dist = cv2.distanceTransform((1 - ink).astype(np.uint8), cv2.DIST_L2, 3)
    pts, drawn = [], []
    for lay in layer_names(gpkg):
        if lay in SKIP_LAYERS:
            continue
        gdf = read_layer(gpkg, lay)
        for poly in _lines_px(gdf, frame.to_px):
            s = _sample(poly)
            if len(s):
                pts.append(s)
            drawn.append(poly)
    res = VerifyResult(tolerance_px=float(tolerance_px), min_share=float(min_share))
    if not pts:
        res.message = "no line work to verify"
        return res
    P = np.vstack(pts)
    xi, yi = np.rint(P[:, 0]).astype(int), np.rint(P[:, 1]).astype(int)
    inside = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
    d = np.full(len(P), np.inf)
    d[inside] = dist[yi[inside], xi[inside]]
    ok = d <= tolerance_px
    res.n_samples = int(len(P))
    res.share_within = float(ok.mean())
    fin = d[np.isfinite(d)]
    res.p95_px = float(np.percentile(fin, 95)) if len(fin) else float("inf")

    # zones
    r, c = grid
    x0, x1, y0, y1 = P[:, 0].min(), P[:, 0].max(), P[:, 1].min(), P[:, 1].max()
    zi = np.clip(((P[:, 1] - y0) / max(y1 - y0, 1e-9) * r).astype(int), 0, r - 1)
    zj = np.clip(((P[:, 0] - x0) / max(x1 - x0, 1e-9) * c).astype(int), 0, c - 1)
    zones = []
    for i in range(r):
        for j in range(c):
            m = (zi == i) & (zj == j)
            n = int(m.sum())
            if n < min_samples:
                continue
            share = float(ok[m].mean())
            zones.append(dict(row=i, col=j, samples=n, share=round(share, 4),
                              p95_px=round(float(np.percentile(d[m][np.isfinite(d[m])], 95)) if np.isfinite(d[m]).any() else float("inf"), 2),
                              ok=bool(share >= min_share)))
    res.zones = zones
    res.passed = bool(zones) and all(z["ok"] for z in zones)

    # coverage of long straight source edges by the output (informational)
    edge = ink & (1 - cv2.erode(ink, np.ones((3, 3), np.uint8)))
    longe = cv2.morphologyEx(edge, cv2.MORPH_OPEN, np.ones((1, 9), np.uint8)) | \
        cv2.morphologyEx(edge, cv2.MORPH_OPEN, np.ones((9, 1), np.uint8))
    mask = np.zeros_like(ink)
    for poly in drawn:
        cv2.polylines(mask, [np.rint(poly).astype(np.int32).reshape(-1, 1, 2)], False, 1, 1)
    od = cv2.distanceTransform((1 - mask).astype(np.uint8), cv2.DIST_L2, 3)
    n_long = int(longe.sum())
    res.coverage = float((od[longe > 0] <= tolerance_px + 1).mean()) if n_long else 1.0

    if overlay_path is not None:
        k = 1.0 if W >= 1800 else min(3.0, 2400.0 / W)
        base = Image.fromarray(rgb.astype(np.uint8))
        if k != 1.0:
            base = base.resize((int(W * k), int(H * k)), Image.LANCZOS)
        base = Image.blend(base, Image.new("RGB", base.size, "white"), 0.5)
        dr = ImageDraw.Draw(base)
        for poly in drawn:
            dr.line([(x * k, y * k) for x, y in poly], fill=(0, 90, 255), width=1)
        for (x, y), good in zip(P, ok):
            if not good:
                dr.ellipse([x * k - 2, y * k - 2, x * k + 2, y * k + 2], fill=(255, 0, 0))
        for z in zones:
            if not z["ok"]:
                pass
        base.save(overlay_path)
        res.overlay = str(overlay_path)
    return res
