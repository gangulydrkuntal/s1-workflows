"""Two-unit residential floor plan (feet-inches, 60' x 31'11") -> GeoPackage.

Source: source_drawing.png (1000 x 708 px, CAD export, thin double-line walls).
Method: PROMPT_floor_plan_to_cad_gis.md, with automatic line extraction because
this drawing has ~200 thin lines that a person cannot place by eye reliably.

Pipeline (all deterministic, from the source pixels):
  1. colour split: ink (grey/black), red (stairs + maroon edge), green lines
  2. straight segments: morphological opening (10 px) + connected components
  3. walls = pairs of parallel segments 3.5-7.5 px apart with white between
  4. doors = circle fit to arc pixels; closed end = chord with wall continuing
     on both sides (the opening); open leaf drawn only where pixels show it
  5. dimension lines = unpaired segment carrying a dimension label
  6. rooms = free space enclosed by walls / single lines / green / maroon
     lines / door closing chords, flood-filled and vectorised
Units: metres (feet kept as attributes). Origin = west end of the 60' line,
bottom outer wall line; Y up; no CRS.
Calibration: 60' = px 98..886 (13.133 px/ft); 31'11" = py 175..594 (13.128 px/ft).

Run:  python build_geopackage.py
"""
import json
import math
import sqlite3
import warnings
from pathlib import Path

import cv2
import geopandas as gpd
import numpy as np
from PIL import Image
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
import shapely

HERE = Path(__file__).parent
SRC = HERE / "source_drawing.png"
GPKG = HERE / "floor_plan.gpkg"
warnings.filterwarnings("ignore", message=".*crs.*")

FT = 0.3048
SX, SY = 788 / 60.0, 419 / (31 + 11 / 12)      # px per foot
PX0, PY0 = 98.0, 594.0                          # origin pixel


def to_m(px, py):
    return ((px - PX0) / SX * FT, (PY0 - py) / SY * FT)


# --------------------------------------------------------------------------- #
# Text (verbatim, incl. the drawing's spelling "Baclcony").  (text, kind, box px, rotation)
# --------------------------------------------------------------------------- #
TEXTS = [
    ("Road", "site_label", (426, 76, 453, 89), 0),
    ("Baclcony", "room_label", (236, 140, 292, 155), 0),
    ("Baclcony", "room_label", (624, 153, 677, 168), 0),
    ("Baclcony", "room_label", (68, 416, 86, 468), 90),
    ("Bedroom", "room_label", (207, 271, 260, 284), 0),
    ("Bedroom", "room_label", (548, 206, 601, 219), 0),
    ("Bedroom", "room_label", (156, 497, 212, 510), 0),
    ("Bedroom", "room_label", (791, 532, 844, 545), 0),
    ("Dining Area", "room_label", (133, 340, 202, 353), 0),
    ("Drawing Area", "room_label", (348, 377, 428, 393), 0),
    ("Drawing Area", "room_label", (578, 353, 658, 368), 0),
    ("Dining Area", "room_label", (787, 356, 855, 372), 0),
    ("T1", "room_label", (302, 484, 319, 498), 0),
    ("T2", "room_label", (367, 482, 384, 495), 0),
    ("T1", "room_label", (586, 486, 601, 499), 0),
    ("T2", "room_label", (651, 486, 668, 499), 0),
]
DIM_TEXTS = [   # (text, box px, rotation)  270 = reads top-to-bottom, 90 = bottom-to-top
    ("7'5\"", (143, 129, 161, 138), 0), ("4'10\"", (135, 146, 146, 167), 270),
    ("7'1\"", (535, 122, 549, 130), 0), ("4'1\"", (526, 140, 537, 153), 270),
    ("14'0\"", (185, 223, 206, 232), 0), ("10'6\"", (179, 256, 190, 275), 90),
    ("14'", (582, 230, 595, 239), 0), ("10'6\"", (510, 239, 521, 258), 270),
    ("8'9\"", (736, 183, 753, 192), 0), ("14'7\"", (773, 244, 794, 253), 0),
    ("19'6\"", (760, 301, 771, 320), 270), ("29'5\"", (286, 357, 308, 366), 0),
    ("7'7\"", (115, 381, 125, 396), 270), ("30'6\"", (82, 384, 93, 400), 90),
    ("10'5\"", (454, 378, 465, 398), 270), ("20'11\"", (472, 305, 483, 331), 270),
    ("10'", (531, 383, 544, 398), 270), ("29'2\"", (677, 383, 699, 392), 0),
    ("9'7\"", (869, 367, 880, 383), 270), ("31'11\"", (905, 372, 916, 397), 90),
    ("12'4\"", (788, 458, 810, 467), 0), ("11'", (745, 510, 756, 522), 270),
    ("11'", (153, 510, 164, 520), 270), ("12'", (176, 535, 190, 543), 0),
    ("5'", (293, 520, 304, 529), 0), ("6'", (319, 515, 330, 524), 270),
    ("6'", (362, 516, 373, 524), 270), ("5'", (365, 528, 375, 537), 0),
    ("10'6\"", (323, 569, 344, 578), 0), ("1'9\"", (395, 569, 403, 583), 270),
    ("3'", (494, 477, 505, 485), 270), ("6'7\"", (471, 535, 482, 547), 270),
    ("11'10\"", (474, 551, 506, 565), 0),
    ("5'", (601, 506, 612, 515), 0), ("6'", (622, 517, 633, 524), 270),
    ("5'", (674, 512, 685, 522), 0), ("6'", (693, 516, 703, 524), 270),
    ("10'7\"", (633, 567, 654, 575), 0), ("1'9\"", (607, 570, 622, 585), 270),
    ("60'", (485, 604, 500, 613), 0),
]
# Door arc search boxes (px) - tight around each swing arc
DOOR_BOXES = {
    "D01": (172, 164, 200, 190), "D02": (306, 139, 337, 176), "D03": (251, 293, 282, 328),
    "D04": (559, 150, 582, 176), "D05": (695, 183, 732, 220), "D06": (645, 278, 675, 306),
    "D07": (61, 355, 88, 385), "D08": (217, 444, 256, 482), "D09": (411, 418, 449, 453),
    "D10": (269, 530, 293, 556), "D11": (375, 486, 401, 506), "D12": (541, 420, 570, 457),
    "D13": (722, 440, 766, 484), "D14": (574, 482, 602, 506), "D15": (685, 530, 715, 560),
}


# Doors where the arc fit or the end rule needs a visual decision (12x zoom, pixel grid).
#   hinge px, radius px, closed-end angle, open-end angle (0 = +x, 90 = +y image-down)
DOOR_OVERRIDES = {
    "D05": dict(closed_deg=0, note="corner: dark leaf drawn downward, so the opening is the wall to the east"),
    "D10": dict(closed_deg=270, note="corner: dark leaf drawn to the east, so the opening is the wall above"),
    "D11": dict(hinge=(399.0, 476.5), r=27.0, closed_deg=180, open_deg=90,
                note="arc tangent at (398,503); jamb ticks at x 373 and 399 in the top wall"),
    "D13": dict(hinge=(724.5, 445.0), r=32.5, closed_deg=0, open_deg=90,
                note="grey leaf drawn down at x 724; arc meets top wall at x 757"),
    "D14": dict(hinge=(575.0, 477.0), r=23.0, closed_deg=0, open_deg=90,
                note="mirror of D11; jamb tick at x 598 in the top wall"),
}


def ft_in(v):
    """parse 14'7" -> feet"""
    v = v.replace('"', "")
    f, _, i = v.partition("'")
    return float(f) + (float(i) / 12 if i else 0.0)


# --------------------------------------------------------------------------- #
def load():
    a = np.asarray(Image.open(SRC).convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    gray, sat = a.mean(2), a.max(2) - a.min(2)
    return dict(gray=gray, ink=((gray < 225) & (sat < 40)).astype(np.uint8),
                ink_light=((gray < 235) & (sat < 40)).astype(np.uint8),
                red=((r > g + 30) & (r > b + 10)).astype(np.uint8),
                green=((g > r + 25) & (g > b + 25)).astype(np.uint8))


def segments(mask, cls, L=10):
    out = []
    for o, k in (("h", (L, 1)), ("v", (1, L))):
        op = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, k))
        n, lab, st, _ = cv2.connectedComponentsWithStats(op, 8)
        for i in range(1, n):
            x, y, w, h, _ = st[i]
            ys, xs = np.where(lab == i)
            if o == "h":
                out.append(dict(cls=cls, o="h", x0=int(x), x1=int(x + w - 1), y=float(np.mean(ys)), t=int(h)))
            else:
                out.append(dict(cls=cls, o="v", y0=int(y), y1=int(y + h - 1), x=float(np.mean(xs)), t=int(w)))
    return out


def seg_line(s):
    return LineString([(s["x0"], s["y"]), (s["x1"] + 1, s["y"])]) if s["o"] == "h" else \
        LineString([(s["x"], s["y0"]), (s["x"], s["y1"] + 1)])


def text_anchor(gray, bx, rot):
    x0, y0, x1, y1 = bx
    global _SAT
    if "_SAT" not in globals():
        rgb = np.asarray(Image.open(SRC).convert("RGB")).astype(int)
        _SAT = rgb.max(2) - rgb.min(2)
    dark = (gray[y0:y1 + 1, x0:x1 + 1] < 170) & (_SAT[y0:y1 + 1, x0:x1 + 1] < 40)   # grey/black ink only
    dark[:, dark.mean(0) > 0.8] = False       # a line crossing the whole box is not text
    dark[dark.mean(1) > 0.8, :] = False
    ys, xs = np.where(dark)
    cx, cy = x0 + (xs.min() + xs.max() + 1) / 2, y0 + (ys.min() + ys.max() + 1) / 2
    size = (ys.max() - ys.min() + 1) if rot in (0, 180) else (xs.max() - xs.min() + 1)
    return (cx, cy), size


def in_box(s, bx, pad=1):
    x0, y0, x1, y1 = bx
    if s["o"] == "h":
        return x0 - pad <= s["x0"] and s["x1"] <= x1 + pad and y0 - pad <= s["y"] <= y1 + pad
    return y0 - pad <= s["y0"] and s["y1"] <= y1 + pad and x0 - pad <= s["x"] <= x1 + pad


def pair_walls(segs, gray):
    walls = []
    for o in ("h", "v"):
        S = [s for s in segs if s["o"] == o]
        for i, p in enumerate(S):
            for q in S[i + 1:]:
                if o == "h":
                    d, lo, hi = abs(p["y"] - q["y"]), max(p["x0"], q["x0"]), min(p["x1"], q["x1"])
                    c0, c1 = sorted([p["y"], q["y"]])
                else:
                    d, lo, hi = abs(p["x"] - q["x"]), max(p["y0"], q["y0"]), min(p["y1"], q["y1"])
                    c0, c1 = sorted([p["x"], q["x"]])
                if not (3.5 <= d <= 7.5 and hi - lo >= 5):
                    continue
                inner = gray[int(c0) + 2:int(math.ceil(c1)) - 1, lo:hi + 1] if o == "h" else \
                    gray[lo:hi + 1, int(c0) + 2:int(math.ceil(c1)) - 1]
                if inner.size == 0 or inner.mean() < 200:
                    continue
                walls.append(dict(o=o, a=p["id"], b=q["id"], c0=c0, c1=c1, lo=lo, hi=hi))
    return walls


def wall_rect(w):
    return box(w["lo"], w["c0"] - 0.5, w["hi"] + 1, w["c1"] + 0.5) if w["o"] == "h" else \
        box(w["c0"] - 0.5, w["lo"], w["c1"] + 0.5, w["hi"] + 1)


def fit_door(ink_light, line_mask, bx):
    x0, y0, x1, y1 = bx
    res = ink_light & (1 - line_mask)
    ys, xs = np.where(res[y0:y1, x0:x1] > 0)
    xs, ys = xs + x0, ys + y0
    for _ in range(2):
        A = np.c_[2 * xs, 2 * ys, np.ones(len(xs))]
        cx, cy, c = np.linalg.lstsq(A, xs ** 2 + ys ** 2, rcond=None)[0]
        r = math.sqrt(c + cx ** 2 + cy ** 2)
        keep = np.abs(np.hypot(xs - cx, ys - cy) - r) < 1.5
        xs, ys = xs[keep], ys[keep]
    ang = np.sort(np.degrees(np.arctan2(ys - cy, xs - cx)) % 360)
    gaps = np.diff(np.r_[ang, ang[0] + 360])
    k = int(np.argmax(gaps))
    a0, a1 = ang[(k + 1) % len(ang)], ang[k]
    return cx, cy, r, a0, a1, len(xs)


def snap_quadrant(cx, cy, r, a0, a1):
    """the arc of a door is a quarter circle between two axis directions"""
    mid = (a0 + ((a1 - a0) % 360) / 2) % 360
    q = int(mid // 90)                      # quadrant of the bulge
    d0, d1 = q * 90, q * 90 + 90
    return [(cx + r * math.cos(math.radians(t)), cy + r * math.sin(math.radians(t))) for t in (d0, d1)], (d0, d1)


def orthogonal_snap(poly, faces, tol=3.5):
    """Make a raster room outline orthogonal, then move each edge onto the centre
    of the nearest parallel wall-face line within `tol` px (the drawn face)."""
    pts = list(poly.exterior.coords)[:-1]
    n = len(pts)
    edges = []
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        if abs(x1 - x0) >= abs(y1 - y0):
            edges.append(["h", (y0 + y1) / 2, min(x0, x1), max(x0, x1)])
        else:
            edges.append(["v", (x0 + x1) / 2, min(y0, y1), max(y0, y1)])
    merged = []                       # merge consecutive same-orientation edges
    for e in edges:
        if merged and merged[-1][0] == e[0] and abs(merged[-1][1] - e[1]) < 2.5:
            m = merged[-1]
            m[1] = (m[1] * (m[3] - m[2]) + e[1] * (e[3] - e[2])) / max((m[3] - m[2]) + (e[3] - e[2]), 1e-6)
            m[2], m[3] = min(m[2], e[2]), max(m[3], e[3])
        else:
            merged.append(list(e))
    if len(merged) > 1 and merged[0][0] == merged[-1][0] and abs(merged[0][1] - merged[-1][1]) < 2.5:
        last = merged.pop()
        merged[0][2], merged[0][3] = min(merged[0][2], last[2]), max(merged[0][3], last[3])
    for e in merged:
        o, c, lo, hi = e
        best = None
        for f in faces:
            if f["o"] != o:
                continue
            fc = f["y"] if o == "h" else f["x"]
            flo, fhi = (f["x0"], f["x1"]) if o == "h" else (f["y0"], f["y1"])
            ov = min(hi, fhi) - max(lo, flo)
            if abs(fc - c) <= tol and ov >= 0.5 * (hi - lo):
                if best is None or abs(fc - c) < abs(best - c):
                    best = fc
        if best is not None:
            e[1] = best
    if len(merged) < 4 or any(merged[i][0] == merged[(i + 1) % len(merged)][0] for i in range(len(merged))):
        return poly                   # could not orthogonalise cleanly: keep raster outline
    verts = []
    for i in range(len(merged)):
        a, b = merged[i], merged[(i + 1) % len(merged)]
        verts.append((b[1], a[1]) if a[0] == "h" else (a[1], b[1]))
    out = Polygon(verts)
    return out if out.is_valid and abs(out.area - poly.area) < 0.25 * poly.area else poly


def build():
    im = load()
    gray = im["gray"]
    segs = segments(im["ink"], "ink") + segments(im["red"], "red", 8) + segments(im["green"], "green", 8)
    for i, s in enumerate(segs):
        s["id"] = i
    text_boxes = [t[2] for t in TEXTS] + [t[1] for t in DIM_TEXTS]
    ink = [s for s in segs if s["cls"] == "ink" and not any(in_box(s, bx) for bx in text_boxes)]

    # strict pre-pass: a line on a dimension label's axis (<= 8 px) whose length matches the
    # label within 6 % is that dimension line, never a wall face (e.g. 30'6" beside the west wall)
    reserved = set()
    rgb = np.asarray(Image.open(SRC).convert("RGB")).astype(int)
    pale = segments(((gray < 242) & (rgb.max(2) - rgb.min(2) < 40)).astype(np.uint8), "pale", 8)
    pale = [dict(q, id=-1 - i) for i, q in enumerate(pale)
            if not any(in_box(q, bx, 2) for bx in text_boxes)]           # pale lines compete too
    for text, bx, rot in DIM_TEXTS:
        (tx, ty), _ = text_anchor(gray, bx, rot)
        o = "h" if rot in (0, 180) else "v"
        want = ft_in(text) * (SX if o == "h" else SY)
        axis = ty if o == "h" else tx
        near = sorted([s for s in ink + pale if s["o"] == o and abs((s["y"] if o == "h" else s["x"]) - axis) <= 8],
                      key=lambda s: (round(s["y"] if o == "h" else s["x"]), s["x0"] if o == "h" else s["y0"]))
        runs_, cur = [], []
        for s in near:                       # join collinear pieces across gaps <= 5 px (crossing lines)
            key = s["y"] if o == "h" else s["x"]
            lo = s["x0"] if o == "h" else s["y0"]
            if cur and abs(key - (cur[-1]["y"] if o == "h" else cur[-1]["x"])) <= 1.5 and \
                    lo - (cur[-1]["x1"] if o == "h" else cur[-1]["y1"]) <= 5:
                cur.append(s)
            else:
                if cur:
                    runs_.append(cur)
                cur = [s]
        if cur:
            runs_.append(cur)
        c = tx if o == "h" else ty
        best = None                          # only the closest matching run is the dimension line
        for run in runs_:
            lo = min(s["x0"] if o == "h" else s["y0"] for s in run)
            hi = max(s["x1"] if o == "h" else s["y1"] for s in run)
            off = abs((run[0]["y"] if o == "h" else run[0]["x"]) - axis)
            if lo - 3 <= c <= hi + 3 and abs((hi - lo + 1) - want) / want <= 0.06:
                if best is None or off < best[0]:
                    best = (off, run)
        if best:
            reserved |= {s["id"] for s in best[1] if s["id"] >= 0}
    walls = pair_walls([s for s in ink if s["id"] not in reserved], gray)
    line_mask = np.zeros_like(im["ink"])
    for s in [s for s in segs if s["cls"] == "ink"]:
        p = seg_line(s)
        (ax, ay), (bx_, by) = p.coords
        cv2.line(line_mask, (int(round(ax)), int(round(ay))), (int(round(bx_)), int(round(by))), 1, s["t"] + 2)

    # ---- doors
    wall_union = unary_union([wall_rect(w) for w in walls])
    doors = []
    for did, bx in DOOR_BOXES.items():
        cx, cy, r, a0, a1, n = fit_door(im["ink_light"], line_mask, bx)
        ends, (d0, d1) = snap_quadrant(cx, cy, r, a0, a1)
        info = []
        for e, deg in zip(ends, (d0, d1)):
            ux, uy = (e[0] - cx) / r, (e[1] - cy) / r
            beyond_end = Point(e[0] + 3 * ux, e[1] + 3 * uy).buffer(1.5)
            beyond_hinge = Point(cx - 3 * ux, cy - 3 * uy).buffer(1.5)
            on_wall = wall_union.intersects(beyond_end) and wall_union.intersects(beyond_hinge)
            chord = LineString([(cx, cy), e])
            covered = chord.buffer(1.0).intersection(wall_union).area / max(chord.buffer(1.0).area, 1e-6)
            drawn = sum(im["ink"][int(round(cy + (e[1] - cy) * f)) - 1:int(round(cy + (e[1] - cy) * f)) + 2,
                                  int(round(cx + (e[0] - cx) * f)) - 1:int(round(cx + (e[0] - cx) * f)) + 2].any()
                        for f in np.linspace(0.15, 0.85, 15)) / 15
            info.append(dict(end=e, deg=deg, on_wall=on_wall, covered=covered, drawn=drawn))
        ov = DOOR_OVERRIDES.get(did, {})
        if "hinge" in ov:
            cx, cy = ov["hinge"]
            r = ov["r"]
            pt = lambda deg: (cx + r * math.cos(math.radians(deg)), cy + r * math.sin(math.radians(deg)))
            closed, opened = pt(ov["closed_deg"]), pt(ov["open_deg"])
            deg = (ov["closed_deg"], ov["open_deg"])
            status = "VERIFIED (visual: " + ov["note"] + ")"
        else:
            on = [x["on_wall"] for x in info]
            if "closed_deg" in ov:
                ci = 0 if info[0]["deg"] == ov["closed_deg"] else 1
                status = "VERIFIED (visual: " + ov["note"] + ")"
            elif on[0] != on[1]:
                ci = 0 if on[0] else 1          # closed position lies along a wall face
                status = "VERIFIED"
            else:
                ci = 0
                status = "UNVERIFIED: both/neither arc ends lie along a wall"
            closed, opened = info[ci]["end"], info[1 - ci]["end"]
            deg = (info[0]["deg"], info[1]["deg"])
        drawn = np.mean([im["ink_light"][int(round(cy + (opened[1] - cy) * f)) - 1:int(round(cy + (opened[1] - cy) * f)) + 2,
                                         int(round(cx + (opened[0] - cx) * f)) - 1:int(round(cx + (opened[0] - cx) * f)) + 2].any()
                         for f in np.linspace(0.2, 0.9, 15)])
        doors.append(dict(id=did, hinge=(cx, cy), r=r, closed=closed, open=opened, deg=deg,
                          leaf_drawn=bool(drawn > 0.7), status=status, n=n))

    # leaves drawn on top of a wall face create false wall pairs: drop those
    leaf_lines = [LineString([d["hinge"], d["open"]]) for d in doors if d["leaf_drawn"]]
    segmap = {s["id"]: s for s in segs}

    def touches_leaf(sid):
        L = seg_line(segmap[sid])
        return any(L.buffer(1.5).contains(l.interpolate(0.5, normalized=True)) and
                   L.length < l.length * 1.6 for l in leaf_lines)
    walls = [w for w in walls if not (touches_leaf(w["a"]) or touches_leaf(w["b"]))]
    paired = {w["a"] for w in walls} | {w["b"] for w in walls}
    wall_px = unary_union([wall_rect(w) for w in walls])

    # ---- dimension lines: a single line carrying a dimension label.
    # Candidates come from a lighter threshold (dimension lines are often pale grey).
    unpaired = [s for s in ink if s["id"] not in paired]
    light = segments(((gray < 242) & (np.asarray(Image.open(SRC).convert("RGB")).astype(int).max(2)
                                      - np.asarray(Image.open(SRC).convert("RGB")).astype(int).min(2) < 40)).astype(np.uint8),
                     "dimcand", 8)
    face_lines = [seg_line(segmap[i]) for i in paired]
    faces_u = unary_union([f.buffer(1.2) for f in face_lines])
    cands = []
    for s in light:
        if any(in_box(s, bx, 2) for bx in text_boxes):
            continue
        Lc = seg_line(s)
        if Lc.length and Lc.intersection(faces_u).length > 0.8 * Lc.length:
            continue                                   # it is a wall face
        cands.append(s)
    for i, s in enumerate(cands):
        s["cid"] = i

    def chain_from(s0, pool, o, bx):
        key = s0["y"] if o == "h" else s0["x"]
        group = [s for s in pool if s["o"] == o and abs((s["y"] if o == "h" else s["x"]) - key) <= 1.5]
        chain = [s0]
        for direction in (1, -1):
            cur = s0
            while True:
                nxt = None
                for s in group:
                    if s in chain:
                        continue
                    if o == "h":
                        gap = s["x0"] - cur["x1"] if direction == 1 else cur["x0"] - s["x1"]
                    else:
                        gap = s["y0"] - cur["y1"] if direction == 1 else cur["y0"] - s["y1"]
                    if o == "h":
                        g0, g1 = (cur["x1"], s["x0"]) if direction == 1 else (s["x1"], cur["x0"])
                        text_in_gap = bx[0] - 3 <= g1 and bx[2] + 3 >= g0
                    else:
                        g0, g1 = (cur["y1"], s["y0"]) if direction == 1 else (s["y1"], cur["y0"])
                        text_in_gap = bx[1] - 3 <= g1 and bx[3] + 3 >= g0
                    if -2 <= gap <= 3 or (gap <= 30 and text_in_gap):
                        nxt = s
                        break
                if not nxt:
                    break
                chain.append(nxt)
                cur = nxt
        lo = min(s["x0"] if o == "h" else s["y0"] for s in chain)
        hi = max(s["x1"] if o == "h" else s["y1"] for s in chain)
        return chain, lo, hi

    dims, dim_ids = [], set()
    for text, bx, rot in DIM_TEXTS:
        (tx, ty), size = text_anchor(gray, bx, rot)
        o = "h" if rot in (0, 180) else "v"
        want = ft_in(text) * (SX if o == "h" else SY)          # px, used only to rank candidates
        options = []
        for s in cands:
            if s["o"] != o:
                continue
            off = abs((s["y"] if o == "h" else s["x"]) - (ty if o == "h" else tx))
            if off > 12:
                continue
            chain, lo, hi = chain_from(s, cands, o, bx)
            c = tx if o == "h" else ty
            if not (lo - 3 <= c <= hi + 3):
                continue
            err = abs((hi - lo + 1) - want) / want
            options.append((off + 40 * err, err, chain, lo, hi))
        line_ids, quality = [], "no measurable line (too faint or not drawn)"
        if options:
            _, err, chain, lo, hi = min(options, key=lambda t: t[0])
            line_ids = [s["cid"] for s in chain]
            dim_ids |= {s["id"] for s in ink for c in chain
                        if s["o"] == o and abs((s["y"] if o == "h" else s["x"]) - (c["y"] if o == "h" else c["x"])) <= 1.5
                        and seg_line(s).intersection(seg_line(c).buffer(1.5)).length > 0.5 * seg_line(s).length}
            quality = "matches label" if err <= 0.06 else f"drawn length differs from label by {err:.0%}"
            if err > 0.10:                      # not the right line: do not attach one
                line_ids, chain, quality = [], [], "no reliable line (nearest differs by %d%%)" % round(err * 100)
        dims.append(dict(text=text, anchor=(tx, ty), size=size, rot=rot, segs=line_ids, quality=quality,
                         chain=[seg_line(c) for c in (chain if (options and line_ids) else [])]))

    other = [s for s in unpaired if s["id"] not in dim_ids]

    def is_leaf(s):
        L = seg_line(s)
        for d in doors:
            chord = LineString([d["hinge"], d["open"]])
            if L.length <= chord.length * 1.3 and chord.buffer(2.0).contains(L.interpolate(0.5, normalized=True)) \
                    and L.buffer(2.0).intersection(chord).length >= 0.6 * min(L.length, chord.length):
                return True
        return False
    leaf_ids = {s["id"] for s in other if is_leaf(s)}
    other = [s for s in other if s["id"] not in leaf_ids]
    barrier_lines = [s for s in ink if s["id"] not in dim_ids and s["id"] not in leaf_ids]

    # ---- rooms: flood fill of free space
    H, W = gray.shape
    barrier = np.zeros((H, W), np.uint8)
    for p in (wall_px.geoms if wall_px.geom_type == "MultiPolygon" else [wall_px]):
        pm = np.zeros_like(barrier)       # one polygon at a time: exterior minus holes
        cv2.fillPoly(pm, [np.round(np.array(p.exterior.coords)).astype(np.int32)], 1)
        cv2.polylines(pm, [np.round(np.array(p.exterior.coords)).astype(np.int32)], True, 1, 1)
        for hole in p.interiors:
            hm = np.zeros_like(barrier)
            cv2.fillPoly(hm, [np.round(np.array(hole.coords)).astype(np.int32)], 1)
            pm[cv2.erode(hm, np.ones((3, 3), np.uint8)) > 0] = 0
        barrier |= pm
    for s in barrier_lines + [s for s in segs if s["cls"] == "green"]:
        (ax, ay), (bx_, by) = seg_line(s).coords
        cv2.line(barrier, (int(round(ax)), int(round(ay))), (int(round(bx_)), int(round(by))), 1, 2)
    maroon = [s for s in segs if s["cls"] == "red" and (s.get("y", 999) < 185 if s["o"] == "h" else s["y1"] < 185)]
    for s in maroon:
        (ax, ay), (bx_, by) = seg_line(s).coords
        cv2.line(barrier, (int(round(ax)), int(round(ay))), (int(round(bx_)), int(round(by))), 1, 3)
    for d in doors:
        cv2.line(barrier, tuple(int(round(v)) for v in d["hinge"]), tuple(int(round(v)) for v in d["closed"]), 1, 2)
    barrier = cv2.morphologyEx(barrier, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
    n, lab, st, _ = cv2.connectedComponentsWithStats((1 - barrier).astype(np.uint8), 4)

    labels = [(t, k, *text_anchor(gray, bx, rot), rot) for t, k, bx, rot in TEXTS]
    comp_labels = {}
    for t, k, (tx, ty), size, rot in labels:
        c = lab[int(ty), int(tx)]
        if k == "room_label" and c > 0:
            comp_labels.setdefault(c, []).append(t)
    road = [l for l in labels if l[0] == "Road"][0]
    exterior = {lab[int(road[3 - 1][1]), int(road[3 - 1][0])], lab[2, 2]}

    rooms = []
    for c in range(1, n):
        if c in exterior or st[c, 4] < 60:
            continue
        m = (lab == c).astype(np.uint8)
        m = cv2.dilate(m, np.ones((3, 3), np.uint8))          # back to the wall face
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnt = max(cs, key=cv2.contourArea)
        ap = cv2.approxPolyDP(cnt, 1.2, True)[:, 0, :].astype(float)
        poly = Polygon(ap).buffer(0)
        if poly.area < 60:
            continue
        poly = orthogonal_snap(poly, barrier_lines)
        rooms.append(dict(comp=c, labels=comp_labels.get(c, []), poly_px=poly))

    return dict(im=im, segs=segs, walls=walls, wall_px=wall_px, doors=doors, dims=dims, dim_ids=dim_ids, leaf_ids=leaf_ids,
                other=other, maroon=maroon, rooms=rooms, labels=labels)


# --------------------------------------------------------------------------- #
def px_geom(g):
    return shapely.transform(g, lambda c: np.c_[(c[:, 0] - PX0) / SX * FT, (PY0 - c[:, 1]) / SY * FT])


def write(R):
    segmap = {s["id"]: s for s in R["segs"]}
    L = {k: [] for k in ("rooms", "wall_fill", "walls", "doors", "stairs", "dimensions", "other_lines",
                         "annotations")}
    room_geoms = {}
    for i, r in enumerate(R["rooms"]):
        g = px_geom(r["poly_px"])
        lbl = r["labels"]
        name = " / ".join(lbl) if lbl else "Unlabelled space"
        rid = f"R{i + 1:02d}"
        room_geoms[rid] = g
        L["rooms"].append(dict(room_id=rid, name=name, labels="; ".join(lbl), area_m2=round(g.area, 2),
                               area_ft2=round(g.area / FT ** 2, 1),
                               status="VERIFIED" if lbl else "VERIFIED (no label on drawing)", geometry=g))

    def owner(pt):
        p = Point(pt)
        inside = [k for k, g in room_geoms.items() if g.contains(p)]
        return min(inside, key=lambda k: room_geoms[k].area) if inside else ""

    wp = px_geom(R["wall_px"])
    sheet_w = (R["im"]["gray"].shape[1] - 1) / SX * FT
    for p in (wp.geoms if wp.geom_type == "MultiPolygon" else [wp]):
        el = "sheet border (double line)" if (p.bounds[2] - p.bounds[0]) > 0.9 * sheet_w else "wall (double-line pair)"
        L["wall_fill"].append(dict(element=el, area_m2=round(p.area, 3), geometry=p))
    paired = {w["a"] for w in R["walls"]} | {w["b"] for w in R["walls"]}
    for sid in sorted(paired):
        L["walls"].append(dict(part="wall face", geometry=px_geom(seg_line(segmap[sid]))))

    for d in R["doors"]:
        hx, hy = d["hinge"]
        hm = to_m(hx, hy)
        (ox, oy), (cxx, cyy) = d["open"], d["closed"]
        a0, a1 = sorted(d["deg"])
        arc = [to_m(hx + d["r"] * math.cos(math.radians(t)), hy + d["r"] * math.sin(math.radians(t)))
               for t in np.linspace(a0, a1, 24)]
        swing_pt = to_m(hx + 0.5 * ((ox - hx) + (cxx - hx)), hy + 0.5 * ((oy - hy) + (cyy - hy)))
        rid = owner(swing_pt)
        common = dict(door_id=d["id"], room_id=rid, width_m=round(d["r"] / SX * FT, 3),
                      width_ft=round(d["r"] / SX, 2), status=d["status"])
        L["doors"].append(dict(common, part="swing arc", geometry=LineString(arc)))
        L["doors"].append(dict(common, part="opening (closed position)", geometry=LineString([hm, to_m(cxx, cyy)])))
        L["doors"].append(dict(common, part="leaf (open)" if d["leaf_drawn"] else "leaf (open, not drawn on source)",
                               geometry=LineString([hm, to_m(ox, oy)])))

    stairs = [s for s in R["segs"] if s["cls"] == "red" and s not in R["maroon"]]
    for s in stairs:
        g = px_geom(seg_line(s))
        L["stairs"].append(dict(part="stair line", room_id=owner(g.interpolate(0.5, normalized=True).coords[0]),
                                geometry=g))
    for s in R["maroon"]:
        L["other_lines"].append(dict(part="maroon edge line (meaning not stated)", colour="maroon",
                                     geometry=px_geom(seg_line(s))))
    for s in [s for s in R["segs"] if s["cls"] == "green"]:
        L["other_lines"].append(dict(part="green line (meaning not stated)", colour="green",
                                     geometry=px_geom(seg_line(s))))
    for s in R["other"]:
        L["other_lines"].append(dict(part="single line", colour="black/grey", geometry=px_geom(seg_line(s))))

    for d in R["dims"]:
        (tx, ty) = d["anchor"]
        if d["chain"]:
            g = unary_union([px_geom(c) for c in d["chain"]])
            line = g if g.geom_type == "LineString" else LineString([min(c for gg in g.geoms for c in gg.coords),
                                                                    max(c for gg in g.geoms for c in gg.coords)])
            L["dimensions"].append(dict(value_text=d["text"], value_ft=round(ft_in(d["text"]), 4),
                                        value_m=round(ft_in(d["text"]) * FT, 4), drawn_length_m=round(line.length, 3),
                                        check=d["quality"], geometry=line))
        pt = to_m(tx, ty)
        L["annotations"].append(dict(text=d["text"], kind="dimension", rotation=d["rot"],
                                     height_m=round(d["size"] / SY * FT, 3), room_id=owner(pt),
                                     dim_line=d["quality"], geometry=Point(pt)))
    for t, k, (tx, ty), size, rot in R["labels"]:
        pt = to_m(tx, ty)
        L["annotations"].append(dict(text=t, kind=k, rotation=rot, height_m=round(size / SY * FT, 3),
                                     room_id=owner(pt) if k == "room_label" else "", geometry=Point(pt)))

    if GPKG.exists():
        GPKG.unlink()
    for i, (name, rows) in enumerate(L.items()):
        gpd.GeoDataFrame(rows, geometry="geometry", crs=None).to_file(GPKG, layer=name, driver="GPKG",
                                                                      mode="w" if i == 0 else "a")
    add_styles()
    return L


def add_styles():
    def line(c, w):
        return (f'<qgis version="3.28"><renderer-v2 type="singleSymbol"><symbols><symbol type="line" name="0">'
                f'<layer class="SimpleLine"><Option type="Map"><Option name="line_color" value="{c}" type="QString"/>'
                f'<Option name="line_width" value="{w}" type="QString"/><Option name="line_width_unit" value="MapUnit" type="QString"/>'
                f'</Option></layer></symbol></symbols></renderer-v2></qgis>')

    def fill(c, o):
        return (f'<qgis version="3.28"><renderer-v2 type="singleSymbol"><symbols><symbol type="fill" name="0">'
                f'<layer class="SimpleFill"><Option type="Map"><Option name="color" value="{c}" type="QString"/>'
                f'<Option name="outline_color" value="{o}" type="QString"/></Option></layer></symbol></symbols></renderer-v2></qgis>')
    anno = ('<qgis version="3.28"><renderer-v2 type="nullSymbol"/><labeling type="simple"><settings calloutType="simple">'
            '<text-style fieldName="text" fontSize="0.2" fontSizeUnit="MapUnit" textColor="0,0,0,255"/>'
            '<placement placement="1" quadOffset="4"/><rendering obstacle="0" displayAll="1" upsidedownLabels="2"/>'
            '<dd_properties><Option type="Map"><Option name="properties" type="Map">'
            '<Option name="Size" type="Map"><Option name="active" value="true" type="bool"/>'
            '<Option name="expression" value="&quot;height_m&quot; * 1.35" type="QString"/><Option name="type" value="3" type="int"/></Option>'
            '<Option name="LabelRotation" type="Map"><Option name="active" value="true" type="bool"/>'
            '<Option name="expression" value="-&quot;rotation&quot;" type="QString"/><Option name="type" value="3" type="int"/></Option>'
            '</Option></Option></dd_properties></settings></labeling></qgis>')
    styles = {"rooms": fill("248,250,255,255", "200,200,220,255"), "wall_fill": fill("190,190,190,255", "120,120,120,255"),
              "walls": line("60,60,60,255", 0.015), "doors": line("0,0,0,255", 0.01), "stairs": line("150,40,80,255", 0.015),
              "dimensions": line("120,120,120,255", 0.008), "other_lines": line("0,120,0,255", 0.015), "annotations": anno}
    con = sqlite3.connect(GPKG)
    con.execute("CREATE TABLE IF NOT EXISTS layer_styles (id INTEGER PRIMARY KEY AUTOINCREMENT, f_table_catalog TEXT,"
                " f_table_schema TEXT, f_table_name TEXT, f_geometry_column TEXT, styleName TEXT, styleQML TEXT,"
                " styleSLD TEXT, useAsDefault BOOLEAN, description TEXT, owner TEXT, ui TEXT,"
                " update_time DATETIME DEFAULT CURRENT_TIMESTAMP)")
    for layer, qml in styles.items():
        con.execute("INSERT INTO layer_styles (f_table_catalog,f_table_schema,f_table_name,f_geometry_column,"
                    "styleName,styleQML,styleSLD,useAsDefault,description) VALUES ('','',?,'geom',?,?,'',1,'plan')",
                    (layer, layer, qml))
    con.commit()
    con.close()


if __name__ == "__main__":
    R = build()
    L = write(R)
    for k, v in L.items():
        print(f"  {k:12s} {len(v):4d}")
    for d in R["doors"]:
        print(d["id"], d["status"], "leaf drawn" if d["leaf_drawn"] else "leaf not drawn",
              "r=%.1fpx" % d["r"], "hinge=(%.1f,%.1f)" % d["hinge"])
    for row in L["rooms"]:
        print(row["room_id"], row["name"], row["area_ft2"], "ft2")
    print("written", GPKG)
