"""Rebuild the Ground Floor / First Floor office plan (source_sketch.jpg) as a DXF.

Units: metres (1 drawing unit = 1 m). Geometry is derived from the dimensions
written on the sketch; every room area checks against the labelled m² value.
Each room is a DXF GROUP containing its walls, doors, boundary and every
annotation that sits inside it, so selecting a room selects its labels too.

Run:  python build_floor_plan_dxf.py   ->  floor_plan.dxf (+ floor_plan_preview.png)
"""
import math
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment

OUT = Path(__file__).with_name("floor_plan.dxf")
FF_DX = 14.0  # first floor drawn 14 m to the right of the ground floor

# --------------------------------------------------------------------------- #
# Room geometry (interior outlines, metres).  key: (floor, polygon)
# --------------------------------------------------------------------------- #
def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

ROOMS = {
    # ---------------- Ground floor ----------------
    "06-OFF": ("GF", rect(0.0, 8.84, 9.84, 15.23)),           # 9.84 x 6.39 = 62.88
    "07-OFF": ("GF", rect(4.75, 15.43, 9.84, 18.48)),         # 5.09 x 3.05 = 15.52
    "03-BRD": ("GF", rect(0.10, 4.39, 3.74, 8.69)),           # 3.64 x 4.30
    "01-REC": ("GF", rect(0.10, 0.00, 4.27, 4.24)),           # 4.17 x 4.24 = 17.68
    "05-WCS": ("GF", rect(5.75, 4.44, 7.98, 6.14)),           # 2.23 x 1.70 = 3.79
    "04-WCS": ("GF", rect(8.10, 4.44, 9.90, 6.14)),           # 1.80 x 1.70 = 3.06
    "02-OFF": ("GF", [(5.75, 0.0), (9.93, 0.0), (9.93, 3.32), (9.17, 3.32),
                      (9.17, 4.24), (5.75, 4.24)]),           # 17.02
    "GF-STAIR": ("GF", rect(6.20, 6.30, 9.90, 8.69)),
    # ---------------- First floor (local coords, shifted by FF_DX) ----------
    "13-OFF": ("FF", rect(0.20, 9.03, 9.51, 15.06)),          # 9.31 x 6.03
    "12-KTN": ("FF", [(0.0, 5.50), (4.64, 5.50), (4.64, 6.50), (5.34, 6.50),
                      (5.34, 8.75), (0.0, 8.75)]),            # 16.65
    "CLN-CBD": ("FF", rect(3.37, 4.35, 4.64, 5.50)),
    "08-OFF": ("FF", [(0.0, 0.0), (5.90, 0.0), (5.90, 4.25), (3.27, 4.25),
                      (3.27, 5.38), (0.0, 5.38)]),            # 28.77 - pier 0.64 = 28.13
    "09-OFF": ("FF", [(6.05, 0.0), (9.84, 0.0), (9.84, 2.98), (9.08, 3.64),
                      (7.33, 3.64), (7.33, 4.25), (6.05, 4.25)]),  # 14.33
    "10-WCS": ("FF", rect(7.33, 3.74, 9.79, 4.88)),           # 2.81
    "11-WCS": ("FF", rect(7.37, 5.00, 9.79, 5.86)),           # 2.42 x 0.86 = 2.08
    "FF-STAIR": ("FF", rect(6.27, 6.10, 9.79, 8.80)),
}

# Extra wall lines that are not room outlines (corridor / lobby)
EXTRA_WALLS = {
    "GF-CORRIDOR": ("GF", [
        [(3.84, 8.69), (3.84, 4.32), (4.42, 4.32), (4.42, 1.00)],
        [(5.60, 8.69), (5.60, 6.20)],
        [(5.60, 4.24), (5.60, 1.00)],
        [(4.42, 1.00), (5.60, 1.00)],                         # entrance line
    ]),
    "13-OFF-SHELL": ("FF", [
        [(4.43, 15.06), (4.43, 12.26), (4.75, 12.26)],        # solid partition
    ]),
}

# Doors: (room, floor, hinge, closed-end, side) side +1 = leaf rotates CCW, -1 CW
DOORS = [
    ("07-OFF", "GF", (7.60, 15.43), (8.35, 15.43), +1),
    ("06-OFF", "GF", (5.31, 8.84), (4.58, 8.84), -1),
    ("03-BRD", "GF", (3.74, 4.55), (3.74, 5.30), +1),
    ("01-REC", "GF", (4.27, 4.10), (4.27, 3.35), -1),
    ("05-WCS", "GF", (5.75, 4.85), (5.75, 5.55), +1),
    ("02-OFF", "GF", (5.75, 3.95), (5.75, 3.25), +1),
    ("04-WCS", "GF", (8.60, 6.14), (9.30, 6.14), +1),
    ("GF-STAIR", "GF", (6.20, 6.45), (6.20, 7.20), -1),
    ("GF-STAIR", "GF", (9.90, 6.60), (9.90, 7.35), +1),
    ("GF-CORRIDOR", "GF", (5.60, 1.00), (4.75, 1.00), -1),   # main entrance
    ("06-OFF", "GF", (4.03, 11.35), (4.03, 12.10), +1),      # glass door
    ("13-OFF", "FF", (4.75, 12.26), (5.45, 12.26), +1),      # glass door
    ("13-OFF", "FF", (6.13, 11.80), (6.13, 11.10), +1),      # glass door
    ("12-KTN", "FF", (5.34, 6.60), (5.34, 7.35), +1),
    ("CLN-CBD", "FF", (4.58, 5.50), (3.80, 5.50), -1),
    ("08-OFF", "FF", (5.76, 4.25), (5.01, 4.25), +1),
    ("09-OFF", "FF", (6.10, 4.25), (6.85, 4.25), -1),
    ("10-WCS", "FF", (7.33, 4.30), (7.33, 4.85), -1),
    ("11-WCS", "FF", (7.37, 5.05), (7.37, 5.70), -1),
]

# Glazed (dashed) partitions
GLASS = [
    ("06-OFF", "GF", [(0.0, 11.22), (4.03, 11.22), (4.03, 15.23)]),
    ("13-OFF", "FF", [(5.45, 12.26), (9.51, 12.26)]),
    ("13-OFF", "FF", [(6.13, 12.26), (6.13, 9.03)]),
]

# --------------------------------------------------------------------------- #
# Annotations, placed at their positions on the sketch (pixel -> metre).
# (text, px, py, height, rotation)
# --------------------------------------------------------------------------- #
def gf(px, py):
    return ((px - 365) / 48.0, (905 - py) / 48.0)

def ff(px, py):
    return ((px - 1122) / 46.5, (888 - py) / 46.5)

H, S, T = 0.22, 0.17, 0.45   # room label, dimension text, title heights

GF_TEXT = [
    ("Ground Floor", 470, 122, T, 0), ("Car Parking    4 spaces", 660, -6, 0.3, 0),
    ("Access", 312, 410, 0.4, 90), ("Car Parking", 315, 565, 0.4, 90),
    ("5.09 m", 712, 42, S, 0), ("3.05 m", 818, 98, S, 90),
    ("2006", 713, 82, H, 0), ("07-OFF", 712, 99, H, 0), ("15.52 m²", 712, 111, H, 0),
    ("2000", 611, 313, H, 0), ("06-OFF", 605, 333, H, 0), ("62.88 m²", 605, 345, H, 0),
    ("6.39 m", 822, 330, S, 90), ("9.84 m", 603, 438, S, 0), ("4.03 m", 465, 392, S, 0),
    ("Glass", 563, 390, S, 0), ("2.38 m", 385, 425, S, 90),
    ("3.64 m", 455, 507, S, 0), ("4.3 m", 380, 598, S, 90),
    ("03-BRD", 457, 602, H, 0), ("15.74 m²", 457, 613, H, 0),
    ("2.4 m", 427, 685, S, 0), ("0.12 m", 492, 678, S, 90), ("0.77 m", 508, 672, S, 90),
    ("01-REC", 472, 808, H, 0), ("17.68 m²", 472, 825, H, 0),
    ("4.24 m", 383, 810, S, 90), ("4.17 m", 472, 892, S, 0),
    ("05-WCS", 698, 648, H, 0), ("3.79 m²", 698, 661, H, 0),
    ("1.7 m", 740, 652, S, 90), ("2.23 m", 697, 685, S, 0),
    ("04-WCS", 800, 652, H, 0), ("3.06 m²", 800, 664, H, 0), ("1.8 m", 800, 682, S, 0),
    ("3.42 m", 726, 717, S, 0), ("0.76 m", 815, 758, S, 0),
    ("02-OFF", 743, 800, H, 0), ("17.02 m²", 743, 812, H, 0),
    ("4.24 m", 661, 800, S, 90), ("3.32 m", 826, 818, S, 90), ("4.18 m", 743, 885, S, 0),
]

FF_TEXT = [
    ("First Floor", 1312, 96, T, 0),
    ("0.5 m", 1152, 222, S, 0), ("1.6 m", 1190, 216, S, 0), ("0.1 m", 1220, 233, S, 90),
    ("0.2 m", 1238, 233, S, 90), ("1.83 m", 1280, 214, S, 0), ("0.21 m", 1328, 224, S, 0),
    ("1.83 m", 1375, 214, S, 0), ("0.21 m", 1420, 224, S, 0), ("1.59 m", 1458, 212, S, 0),
    ("0.54 m", 1500, 217, S, 0), ("0.35 m", 1515, 233, S, 0), ("Air-Con", 1455, 237, S, 0),
    ("1.56 m", 1137, 268, S, 90), ("0.21 m", 1160, 303, S, 0), ("0.1 m", 1150, 320, S, 0),
    ("1.55 m", 1137, 350, S, 90), ("0.21 m", 1160, 386, S, 0), ("0.1 m", 1150, 402, S, 0),
    ("1.55 m", 1137, 430, S, 90), ("0.2 m", 1155, 462, S, 0), ("Air-Con", 1223, 458, S, 0),
    ("6.03 m", 1300, 330, S, 90), ("Glass", 1400, 316, S, 0),
    ("13-OFF", 1330, 358, H, 0), ("49.53 m²", 1333, 378, H, 0), ("2000", 1330, 396, H, 0),
    ("9.31 m", 1332, 454, S, 0),
    ("1.56 m", 1520, 265, S, 90), ("0.21 m", 1500, 298, S, 0), ("1.55 m", 1520, 345, S, 90),
    ("0.21 m", 1500, 381, S, 0), ("1.55 m", 1520, 425, S, 90), ("Air-Con", 1460, 446, S, 0),
    ("0.1 m", 1528, 446, S, 0), ("0.2 m", 1528, 456, S, 0), ("0.4 m", 1572, 463, S, 0),
    ("5.34 m", 1240, 491, S, 0), ("3.25 m", 1130, 555, S, 90),
    ("12-KTN", 1222, 548, H, 0), ("16.65 m²", 1228, 566, H, 0),
    ("4.64 m", 1226, 616, S, 0), ("2.25 m", 1352, 522, S, 90),
    ("Cln", 1302, 651, S, 0), ("cbd", 1302, 666, S, 0),
    ("3.27 m", 1195, 652, S, 0), ("2.63 m", 1325, 703, S, 0),
    ("08-OFF", 1258, 762, H, 0), ("28.13 m²", 1258, 775, H, 0),
    ("5.38 m", 1185, 755, S, 90), ("1.73 m", 1152, 785, S, 90), ("0.37 m", 1150, 840, S, 0),
    ("4.25 m", 1382, 785, S, 90), ("5.9 m", 1260, 873, S, 0),
    ("1.28 m", 1422, 700, S, 0), ("1.75 m", 1502, 728, S, 0), ("4.25 m", 1415, 785, S, 90),
    ("09-OFF", 1490, 785, H, 0), ("14.33 m²", 1490, 798, H, 0),
    ("2.98 m", 1563, 812, S, 90), ("3.79 m", 1490, 866, S, 0),
    ("10-WCS", 1522, 675, H, 0), ("2.81 m²", 1517, 690, S, 0),
    ("2.42 m", 1512, 616, S, 0), ("11-WCS", 1520, 627, S, 0), ("2.08 m²", 1520, 640, S, 0),
    ("0.86 m", 1565, 630, S, 90),
]

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def shift(floor, pts):
    dx = FF_DX if floor == "FF" else 0.0
    return [(x + dx, y) for x, y in pts]


def subtract_openings(a, b, openings, tol=1e-6):
    """Split wall segment a-b removing any collinear door openings."""
    ax, ay = a
    bx, by = b
    L = math.hypot(bx - ax, by - ay)
    if L < tol:
        return []
    ux, uy = (bx - ax) / L, (by - ay) / L
    cuts = []
    for p, q in openings:
        # both ends of opening must lie on the segment's line
        def off(pt):
            return abs((pt[0] - ax) * uy - (pt[1] - ay) * ux)
        if off(p) > 0.02 or off(q) > 0.02:
            continue
        t1 = (p[0] - ax) * ux + (p[1] - ay) * uy
        t2 = (q[0] - ax) * ux + (q[1] - ay) * uy
        t1, t2 = sorted((t1, t2))
        t1, t2 = max(t1, 0), min(t2, L)
        if t2 - t1 > tol:
            cuts.append((t1, t2))
    cuts.sort()
    pieces, t = [], 0.0
    for c1, c2 in cuts:
        if c1 > t + tol:
            pieces.append((t, c1))
        t = max(t, c2)
    if L > t + tol:
        pieces.append((t, L))
    return [((ax + ux * s, ay + uy * s), (ax + ux * e, ay + uy * e)) for s, e in pieces]


def point_in_poly(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def poly_area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                   - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def build():
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.M
    doc.header["$INSUNITS"] = 6
    doc.header["$MEASUREMENT"] = 1
    if "DASHED2" not in doc.linetypes:
        doc.linetypes.add("DASHED2", pattern=[0.25, 0.15, -0.1])
    doc.styles.add("PLAN", font="arial.ttf")

    layers = {
        "A-WALL": (7, 50, "CONTINUOUS"),
        "A-WALL-HATCH": (8, 13, "CONTINUOUS"),
        "A-GLAZ": (5, 35, "DASHED2"),
        "A-DOOR": (5, 25, "CONTINUOUS"),
        "A-STAIR": (5, 25, "CONTINUOUS"),
        "A-AREA-BOUNDARY": (3, 9, "CONTINUOUS"),
        "A-ANNO-ROOM": (5, 18, "CONTINUOUS"),
        "A-ANNO-DIM": (7, 13, "CONTINUOUS"),
        "A-ANNO-TITLE": (5, 25, "CONTINUOUS"),
    }
    for name, (color, lw, lt) in layers.items():
        doc.layers.add(name, color=color, lineweight=lw, linetype=lt)
    doc.layers.get("A-AREA-BOUNDARY").off()   # helper outlines for AREA queries

    msp = doc.modelspace()
    members = {}          # room -> list of entities for grouping

    def add(room, ent):
        members.setdefault(room, []).append(ent)
        return ent

    # door openings per floor (absolute coords)
    openings = {"GF": [], "FF": []}
    for _, fl, h, e, _s in DOORS:
        openings[fl].append(tuple(shift(fl, [h, e])))

    # walls from room outlines
    room_polys = {}
    for room, (fl, poly) in ROOMS.items():
        pts = shift(fl, poly)
        room_polys[room] = pts
        for i in range(len(pts)):
            for a, b in subtract_openings(pts[i], pts[(i + 1) % len(pts)], openings[fl]):
                add(room, msp.add_line(a, b, dxfattribs={"layer": "A-WALL"}))
        add(room, msp.add_lwpolyline(pts, close=True,
                                     dxfattribs={"layer": "A-AREA-BOUNDARY"}))

    for key, (fl, lines) in EXTRA_WALLS.items():
        for line in lines:
            pts = shift(fl, line)
            for i in range(len(pts) - 1):
                for a, b in subtract_openings(pts[i], pts[i + 1], openings[fl]):
                    add(key, msp.add_line(a, b, dxfattribs={"layer": "A-WALL"}))

    # glazed partitions (with door gaps)
    for room, fl, line in GLASS:
        pts = shift(fl, line)
        for i in range(len(pts) - 1):
            for a, b in subtract_openings(pts[i], pts[i + 1], openings[fl]):
                add(room, msp.add_line(a, b, dxfattribs={"layer": "A-GLAZ"}))

    # doors: leaf (open 90°) + swing arc
    for room, fl, h, e, side in DOORS:
        (hx, hy), (ex, ey) = shift(fl, [h, e])
        vx, vy = ex - hx, ey - hy
        r = math.hypot(vx, vy)
        lx, ly = (-vy, vx) if side > 0 else (vy, -vx)
        leaf_end = (hx + lx, hy + ly)
        add(room, msp.add_line((hx, hy), leaf_end, dxfattribs={"layer": "A-DOOR"}))
        a_closed = math.degrees(math.atan2(vy, vx))
        a_open = math.degrees(math.atan2(ly, lx))
        start, end = (a_closed, a_open) if side > 0 else (a_open, a_closed)
        add(room, msp.add_arc((hx, hy), r, start, end, dxfattribs={"layer": "A-DOOR"}))

    # ---- First floor 13-OFF: 0.4 m hatched external wall on 3 sides -------
    x0, y0, x1, y1 = 0.20 + FF_DX, 9.03, 9.51 + FF_DX, 15.06
    t = 0.40
    outer = [(x0 - t, y0 - 0.15), (x1 + t, y0 - 0.15), (x1 + t, y1 + t), (x0 - t, y1 + t)]
    add("13-OFF", msp.add_lwpolyline(outer, close=True, dxfattribs={"layer": "A-WALL"}))
    band = msp.add_hatch(color=8, dxfattribs={"layer": "A-WALL-HATCH"})
    band.set_pattern_fill("ANSI31", scale=0.03)
    band.paths.add_polyline_path(
        [(x0 - t, y0 - 0.15), (x0, y0 - 0.15), (x0, y1), (x1, y1), (x1, y0 - 0.15),
         (x1 + t, y0 - 0.15), (x1 + t, y1 + t), (x0 - t, y1 + t)], is_closed=True)
    add("13-OFF", band)
    # pilasters / window piers (0.2 x 0.1 m) shown as notches in the sketch
    piers = [(2.06, y1, "h"), (2.49, y1, "h"), (4.43, y1, "h"), (6.95, y1, "h"),
             (0.20, 12.65, "v"), (0.20, 10.82, "v"), (9.51, 12.69, "v"), (9.51, 10.92, "v")]
    for px, py, o in piers:
        px = px + FF_DX if px < 20 else px
        if o == "h":
            pts = rect(px - 0.05, py - 0.2, px + 0.05, py)
        else:
            d = 0.2 if px < x0 + 1 else -0.2
            pts = rect(min(px, px + d), py - 0.05, max(px, px + d), py + 0.05)
        add("13-OFF", msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "A-WALL"}))

    # ---- 08-OFF chimney breast (0.37 x 1.73, hatched) ----------------------
    cb = shift("FF", rect(0.0, 1.14, 0.37, 2.87))
    add("08-OFF", msp.add_lwpolyline(cb, close=True, dxfattribs={"layer": "A-WALL"}))
    h = msp.add_hatch(color=8, dxfattribs={"layer": "A-WALL-HATCH"})
    h.set_pattern_fill("ANSI31", scale=0.03)
    h.paths.add_polyline_path(cb, is_closed=True)
    add("08-OFF", h)

    # ---- Stairs -----------------------------------------------------------
    def L(room, a, b):
        add(room, msp.add_line(a, b, dxfattribs={"layer": "A-STAIR"}))

    # ground floor winder (top-left of stair hall)
    sx0, sy0, sx1, sy1 = 6.25, 7.45, 9.90, 8.69
    L("GF-STAIR", (sx0, sy0), (sx1, sy0))
    c = (7.40, sy0)
    for p in [(sx0, 8.05), (sx0, sy1), (6.70, sy1), (7.15, sy1), (7.60, sy1),
              (8.05, sy1), (8.50, sy1)]:
        L("GF-STAIR", c, p)
    # first floor winder + straight flight
    fx = FF_DX
    cx, cy = 8.56 + fx, 7.59
    L("FF-STAIR", (7.46 + fx, cy), (9.79 + fx, cy))
    for x in [7.46, 7.66, 7.86, 8.06, 8.26]:
        L("FF-STAIR", (x + fx, 6.10), (x + fx, cy))
    L("FF-STAIR", (8.56 + fx, 6.10), (cx, cy))
    for p in [(8.20, 8.80), (8.75, 8.80), (9.30, 8.80), (9.79, 8.80), (9.79, 8.30),
              (9.79, 7.95), (9.79, 6.95), (9.79, 6.45)]:
        L("FF-STAIR", (cx, cy), (p[0] + fx, p[1]))

    # ---- Annotations -------------------------------------------------------
    def place(items, conv, floor):
        for text, px, py, ht, rot in items:
            x, y = conv(px, py)
            x += FF_DX if floor == "FF" else 0.0
            if "Floor" in text or "Parking" in text or text == "Access":
                layer = "A-ANNO-TITLE"
            elif text.endswith(" m") or text in ("Glass", "Air-Con"):
                layer = "A-ANNO-DIM"
            else:
                layer = "A-ANNO-ROOM"
            ent = msp.add_text(text, height=ht, rotation=rot,
                               dxfattribs={"layer": layer, "style": "PLAN"})
            ent.set_placement((x, y), align=TextEntityAlignment.MIDDLE_CENTER)
            # link annotation to the room it sits in (smallest containing room)
            owner, best = None, 1e9
            for room, (fl, _) in ROOMS.items():
                if fl != floor:
                    continue
                poly = room_polys[room]
                if point_in_poly((x, y), poly) and poly_area(poly) < best:
                    owner, best = room, poly_area(poly)
            if owner is None:  # dims on walls -> nearest room centroid
                def dist(rm):
                    p = room_polys[rm]
                    cx_ = sum(q[0] for q in p) / len(p)
                    cy_ = sum(q[1] for q in p) / len(p)
                    return math.hypot(cx_ - x, cy_ - y)
                cand = [r for r, (fl, _) in ROOMS.items() if fl == floor]
                owner = min(cand, key=dist) if layer != "A-ANNO-TITLE" else "TITLES"
            add(owner, ent)

    place(GF_TEXT, gf, "GF")
    place(FF_TEXT, ff, "FF")

    # ---- Groups (room = walls + doors + glazing + its labels) --------------
    for room, ents in members.items():
        grp = doc.groups.new(name=room.replace("-", "_"),
                             description=f"Room {room} with linked annotation")
        grp.extend(ents)

    doc.set_modelspace_vport(height=22, center=(12, 9))
    doc.saveas(OUT)
    return doc


def report():
    print("Room areas from geometry:")
    for room, (fl, poly) in ROOMS.items():
        print(f"  {fl} {room:9s} {poly_area(poly):6.2f} m²")


def preview(doc):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy

    fig = plt.figure(figsize=(20, 10))
    ax = fig.add_axes([0, 0, 1, 1])
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE,
                        color_policy=ColorPolicy.COLOR,
                        lineweight_scaling=2.0)
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(
        doc.modelspace())
    fig.savefig(OUT.with_name("floor_plan_preview.png"), dpi=200)


if __name__ == "__main__":
    d = build()
    report()
    preview(d)
    print("written", OUT)
