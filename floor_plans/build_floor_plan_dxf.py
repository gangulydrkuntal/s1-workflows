"""Rebuild the Ground Floor / First Floor office plan (source_sketch.jpg) as a DXF.

Units: metres (1 drawing unit = 1 m). Geometry is derived from the dimensions
written on the sketch, cross-checked against wall positions measured on the
source image. Room areas match the labelled m² values.

Walls are drawn as in the sketch: a solid wall band (poche) between each
room's inner face and the outer building line, with door openings left open.
Each room is a DXF GROUP containing its walls, doors, boundary and every
annotation that sits inside it, so selecting a room selects its labels too.

Run:  python build_floor_plan_dxf.py   ->  floor_plan.dxf (+ floor_plan_preview.png)
"""
import math
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment
from shapely.geometry import LineString, MultiLineString, Polygon, box
from shapely.ops import unary_union

OUT = Path(__file__).with_name("floor_plan.dxf")
FF_DX = 14.0      # first floor drawn 14 m to the right of the ground floor (sheet layout)
WALL_T = 0.15     # typical wall thickness used for the outer building line
EXT_T = 0.40      # hatched external wall of 13-OFF

# Registration of the first floor over the ground floor (shared bottom-left
# building corner: 08-OFF bottom-left sits over 01-REC bottom-left).
FF_STACK_OFFSET = (0.10, 0.0)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# --------------------------------------------------------------------------- #
# Room geometry (interior faces, metres).  key: (floor, polygon)
# --------------------------------------------------------------------------- #
ROOMS = {
    # ---------------- Ground floor ----------------
    "06-OFF": ("GF", rect(0.0, 8.84, 9.84, 15.23)),           # 9.84 x 6.39 = 62.88
    "07-OFF": ("GF", rect(4.71, 15.43, 9.80, 18.48)),         # 5.09 x 3.05 = 15.52
    "03-BRD": ("GF", rect(0.10, 4.39, 3.74, 8.69)),           # 3.64 x 4.30
    "01-REC": ("GF", rect(0.10, 0.00, 4.27, 4.24)),           # 4.17 x 4.24 = 17.68
    "05-WCS": ("GF", rect(5.75, 4.44, 7.98, 6.14)),           # 2.23 x 1.70 = 3.79
    "04-WCS": ("GF", rect(8.10, 4.44, 9.90, 6.14)),           # 1.80 x 1.70 = 3.06
    "02-OFF": ("GF", [(5.75, 0.0), (9.93, 0.0), (9.93, 3.32), (9.17, 3.32),
                      (9.17, 4.24), (5.75, 4.24)]),           # 17.02
    "GF-STAIR": ("GF", rect(6.20, 6.30, 9.90, 8.69)),
    # ---------------- First floor (local coords, shifted by FF_DX) ----------
    # 9.31 x 6.03 on the sketch are OUTSIDE dimensions incl. the 0.4 m wall;
    # interior 8.51 x 5.82 = 49.53 m² (matches label and measured image).
    "13-OFF": ("FF", rect(0.20, 9.08, 8.71, 14.90)),
    "12-KTN": ("FF", [(0.0, 5.50), (4.64, 5.50), (4.64, 6.50), (5.34, 6.50),
                      (5.34, 8.75), (0.0, 8.75)]),            # 16.65
    "CLN-CBD": ("FF", rect(3.37, 4.35, 4.64, 5.50)),
    "08-OFF": ("FF", [(0.0, 0.0), (5.90, 0.0), (5.90, 4.25), (3.27, 4.25),
                      (3.27, 5.38), (0.0, 5.38)]),            # 28.77 - pier 0.64 = 28.13
    "09-OFF": ("FF", [(6.05, 0.0), (9.84, 0.0), (9.84, 2.98), (9.08, 3.64),
                      (7.33, 3.64), (7.33, 4.25), (6.05, 4.25)]),  # 14.33
    "10-WCS": ("FF", rect(7.33, 3.74, 9.79, 4.88)),           # 2.81
    "11-WCS": ("FF", rect(7.37, 5.00, 9.79, 5.86)),           # 2.42 x 0.86 = 2.08
    "FF-STAIR": ("FF", rect(6.27, 6.10, 9.79, 8.75)),
}

# Circulation spaces (not labelled rooms on the sketch, but enclosed by walls)
CIRCULATION = {
    "GF-CORRIDOR": ("GF", [(4.42, 1.00), (5.60, 1.00), (5.60, 6.29), (6.05, 6.29),
                           (6.05, 8.69), (3.84, 8.69), (3.84, 4.32), (4.42, 4.32)]),
    "FF-LOBBY": ("FF", [(4.79, 4.40), (7.18, 4.40), (7.18, 5.95), (6.12, 5.95),
                        (6.12, 8.75), (5.49, 8.75), (5.49, 6.65), (4.79, 6.65)]),
}

# Solid internal partitions (centre-line, thickness)
PARTITIONS = [
    ("13-OFF", "FF", [(4.36, 14.90), (4.36, 12.14), (4.75, 12.14)], 0.10),
]

# Doors: (room, floor, hinge, closed-end, side) side +1 = leaf rotates CCW, -1 CW
DOORS = [
    ("07-OFF", "GF", (7.56, 15.43), (8.31, 15.43), +1),
    ("06-OFF", "GF", (5.31, 8.84), (4.58, 8.84), -1),
    ("03-BRD", "GF", (3.74, 4.55), (3.74, 5.30), +1),
    ("01-REC", "GF", (4.27, 3.35), (4.27, 4.10), +1),
    ("05-WCS", "GF", (5.75, 4.85), (5.75, 5.55), +1),
    ("02-OFF", "GF", (5.75, 3.95), (5.75, 3.25), +1),
    ("04-WCS", "GF", (8.34, 6.14), (9.09, 6.14), -1),        # opens into the WC
    ("GF-STAIR", "GF", (6.20, 6.45), (6.20, 7.20), -1),
    ("GF-STAIR", "GF", (9.90, 6.60), (9.90, 7.35), +1),
    ("GF-CORRIDOR", "GF", (4.76, 1.00), (5.55, 1.00), +1),   # main entrance
    ("06-OFF", "GF", (4.03, 11.35), (4.03, 12.10), +1),      # glass door
    ("13-OFF", "FF", (4.75, 12.14), (5.45, 12.14), +1),      # glass door
    ("13-OFF", "FF", (6.09, 11.80), (6.09, 11.10), +1),      # glass door
    ("12-KTN", "FF", (5.34, 6.60), (5.34, 7.35), +1),
    ("CLN-CBD", "FF", (3.50, 5.50), (4.23, 5.50), +1),
    ("08-OFF", "FF", (5.76, 4.25), (5.01, 4.25), +1),
    ("09-OFF", "FF", (6.10, 4.25), (6.85, 4.25), -1),
    ("10-WCS", "FF", (7.33, 4.30), (7.33, 4.85), -1),
    ("11-WCS", "FF", (7.37, 5.05), (7.37, 5.70), -1),
]

# Glazed (dashed) partitions
GLASS = [
    ("06-OFF", "GF", [(0.0, 11.22), (4.03, 11.22), (4.03, 15.23)]),
    ("13-OFF", "FF", [(5.45, 12.14), (8.71, 12.14)]),
    ("13-OFF", "FF", [(6.09, 12.14), (6.09, 9.08)]),
]

# Wall piers on the 13-OFF external wall: (x, y, side) on the interior face
PIERS = [(2.34, 14.90, "top"), (6.41, 14.90, "top"),
         (0.20, 12.77, "left"), (0.20, 10.88, "left"),
         (8.71, 12.79, "right"), (8.71, 10.99, "right")]
PIER_W, PIER_D = 0.21, 0.10

CHIMNEY = ("08-OFF", "FF", rect(0.0, 1.37, 0.37, 3.10))   # 0.37 x 1.73

# Stairs: list of (room, floor, polyline)
STAIRS = [
    ("GF-STAIR", "GF", [(6.25, 7.46), (7.76, 7.46)]),
    *[("GF-STAIR", "GF", [(7.39, 7.46), p]) for p in
      [(6.27, 8.04), (6.27, 8.55), (6.71, 8.69), (7.15, 8.69), (7.56, 8.69), (8.04, 8.69)]],
    ("GF-STAIR", "GF", [(7.73, 7.46), (7.73, 8.69)]),
    ("GF-STAIR", "GF", [(7.76, 7.46), (8.68, 8.69)]),
    ("GF-STAIR", "GF", [(7.99, 7.46), (9.90, 7.46)]),            # landing edge
    ("FF-STAIR", "FF", [(7.46, 7.59), (9.79, 7.59)]),
    *[("FF-STAIR", "FF", [(x, 6.10), (x, 7.59)]) for x in (7.46, 7.66, 7.86, 8.06, 8.26, 8.56)],
    *[("FF-STAIR", "FF", [(8.56, 7.59), p]) for p in
      [(8.20, 8.75), (8.75, 8.75), (9.30, 8.75), (9.79, 8.75), (9.79, 8.20),
       (9.79, 7.95), (9.79, 6.95), (9.79, 6.45)]],
]

# --------------------------------------------------------------------------- #
# Annotations, placed at their positions on the sketch (pixel -> metre).
# Pixel coords refer to the sketch viewed at 2000 px width.
# --------------------------------------------------------------------------- #
def gf(px, py):
    return ((px - 365) / 48.0, (905 - py) / 48.0)


def ff(px, py):   # fitted to measured wall faces of 13-OFF / 08-OFF
    return ((px - 1125.8) / 46.18, (879.9 - py) / 45.36)

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
# Geometry helpers
# --------------------------------------------------------------------------- #
def shift(floor, pts):
    dx = FF_DX if floor == "FF" else 0.0
    return [(x + dx, y) for x, y in pts]


def door_band(floor, h, e, depth=0.25):
    """Rectangle cut through the wall at a door opening."""
    (hx, hy), (ex, ey) = shift(floor, [h, e])
    return LineString([(hx, hy), (ex, ey)]).buffer(depth, cap_style="flat")


def door_bands(floor):
    return unary_union([door_band(fl, h, e) for _, fl, h, e, _ in DOORS if fl == floor])


def pier_polys(floor="FF"):
    out = []
    for x, y, side in PIERS:
        (x, y), = shift(floor, [(x, y)])
        if side == "top":
            out.append(box(x - PIER_W / 2, y - PIER_D, x + PIER_W / 2, y))
        elif side == "left":
            out.append(box(x, y - PIER_W / 2, x + PIER_D, y + PIER_W / 2))
        else:
            out.append(box(x - PIER_D, y - PIER_W / 2, x, y + PIER_W / 2))
    return out


def ext_wall_13():
    """Outer rectangle and hatched U-shaped band of the 13-OFF external wall."""
    x0, y0, x1, y1 = 0.20, 9.08, 8.71, 14.90
    outer = Polygon(shift("FF", rect(x0 - EXT_T, 8.75, x1 + EXT_T, y1 + EXT_T)))
    band = outer.difference(Polygon(shift("FF", rect(x0, 8.75, x1, y1))))
    return outer, band


def floor_geometry(floor):
    """Outer building outline, wall poche and hatched band for one floor."""
    spaces = [Polygon(shift(fl, p)) for fl, p in list(ROOMS.values()) + list(CIRCULATION.values())
              if fl == floor]
    grown = unary_union([s.buffer(WALL_T, join_style="mitre") for s in spaces])
    band = None
    if floor == "FF":
        outer13, band = ext_wall_13()
        grown = unary_union([grown, outer13])
    # fill small internal voids so the outline is one solid footprint
    polys = grown.geoms if grown.geom_type == "MultiPolygon" else [grown]
    footprint = unary_union([Polygon(p.exterior) for p in polys])
    solids = [Polygon(shift(CHIMNEY[1], CHIMNEY[2]))] if floor == "FF" else []
    part_polys = [LineString(shift(fl, ln)).buffer(t / 2, cap_style="flat", join_style="mitre")
                  for _, fl, ln, t in PARTITIONS if fl == floor]
    pier = pier_polys() if floor == "FF" else []
    poche = footprint.difference(unary_union(spaces)).difference(door_bands(floor))
    poche = unary_union([poche, *part_polys, *pier])
    if band is not None:
        poche = poche.difference(band.difference(door_bands(floor)))
    return dict(footprint=footprint, poche=poche, band=band, solids=solids)


def stack(x, y, floor):
    """Sheet coords -> stacked building coords (FF registered over GF)."""
    if floor == "FF":
        return (x - FF_DX + FF_STACK_OFFSET[0], y + FF_STACK_OFFSET[1])
    return (x, y)


def point_in_poly(pt, poly):
    return Polygon(poly).contains(__import__("shapely").geometry.Point(pt))


def poly_area(poly):
    return Polygon(poly).area


def _lines(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [list(geom.coords)]
    return [list(g.coords) for g in geom.geoms if g.geom_type == "LineString"]


def _rings(geom):
    polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    out = []
    for p in polys:
        if p.is_empty:
            continue
        out.append((list(p.exterior.coords), [list(i.coords) for i in p.interiors]))
    return out


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
        "A-WALL": (7, 35, "CONTINUOUS"),
        "A-WALL-OUTER": (7, 50, "CONTINUOUS"),
        "A-WALL-POCHE": (8, 0, "CONTINUOUS"),
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
    members = {}

    def add(room, ent):
        members.setdefault(room, []).append(ent)
        return ent

    bands = {"GF": door_bands("GF"), "FF": door_bands("FF")}
    spaces = {**ROOMS, **CIRCULATION}

    # inner wall faces of every room / circulation space, door openings removed
    room_polys = {}
    for room, (fl, poly) in spaces.items():
        pts = shift(fl, poly)
        room_polys[room] = pts
        ring = LineString(pts + [pts[0]])
        for ln in _lines(ring.difference(bands[fl])):
            add(room, msp.add_lwpolyline(ln, dxfattribs={"layer": "A-WALL"}))
        add(room, msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "A-AREA-BOUNDARY"}))

    # outer building line, poche, 13-OFF hatched wall, chimney breast
    for fl in ("GF", "FF"):
        g = floor_geometry(fl)
        key = f"{fl}-SHELL"
        for ext, _ in _rings(g["footprint"]):
            for ln in _lines(LineString(ext).difference(bands[fl])):
                add(key, msp.add_lwpolyline(ln, dxfattribs={"layer": "A-WALL-OUTER"}))
        h = msp.add_hatch(color=253, dxfattribs={"layer": "A-WALL-POCHE"})
        for ext, holes in _rings(g["poche"]):
            h.paths.add_polyline_path(ext[:-1], is_closed=True, flags=1)
            for hole in holes:
                h.paths.add_polyline_path(hole[:-1], is_closed=True, flags=0)
        add(key, h)
        if g["band"] is not None:
            hb = msp.add_hatch(color=8, dxfattribs={"layer": "A-WALL-HATCH"})
            hb.set_pattern_fill("ANSI31", scale=0.03)
            for ext, holes in _rings(g["band"].difference(bands[fl])):
                hb.paths.add_polyline_path(ext[:-1], is_closed=True, flags=1)
                for hole in holes:
                    hb.paths.add_polyline_path(hole[:-1], is_closed=True, flags=0)
            add("13-OFF", hb)
            inner = LineString(shift("FF", rect(0.20, 9.08, 8.71, 14.90)) + [shift("FF", [(0.20, 9.08)])[0]])
            for ln in _lines(inner.difference(bands[fl])):
                add("13-OFF", msp.add_lwpolyline(ln, dxfattribs={"layer": "A-WALL"}))
    for p in pier_polys():
        add("13-OFF", msp.add_lwpolyline(list(p.exterior.coords)[:-1], close=True,
                                         dxfattribs={"layer": "A-WALL"}))
    for room, fl, ln, t in PARTITIONS:
        p = LineString(shift(fl, ln)).buffer(t / 2, cap_style="flat", join_style="mitre")
        add(room, msp.add_lwpolyline(list(p.exterior.coords)[:-1], close=True,
                                     dxfattribs={"layer": "A-WALL"}))
    croom, cfl, cpoly = CHIMNEY
    cb = shift(cfl, cpoly)
    add(croom, msp.add_lwpolyline(cb, close=True, dxfattribs={"layer": "A-WALL"}))
    hc = msp.add_hatch(color=8, dxfattribs={"layer": "A-WALL-HATCH"})
    hc.set_pattern_fill("ANSI31", scale=0.03)
    hc.paths.add_polyline_path(cb, is_closed=True)
    add(croom, hc)

    # glazed partitions (door gaps removed)
    for room, fl, line in GLASS:
        for ln in _lines(LineString(shift(fl, line)).difference(bands[fl])):
            add(room, msp.add_lwpolyline(ln, dxfattribs={"layer": "A-GLAZ"}))

    # doors: leaf (open 90°) + swing arc
    for room, fl, h, e, side in DOORS:
        (hx, hy), (ex, ey) = shift(fl, [h, e])
        vx, vy = ex - hx, ey - hy
        r = math.hypot(vx, vy)
        lx, ly = (-vy, vx) if side > 0 else (vy, -vx)
        add(room, msp.add_line((hx, hy), (hx + lx, hy + ly), dxfattribs={"layer": "A-DOOR"}))
        a_closed = math.degrees(math.atan2(vy, vx))
        a_open = math.degrees(math.atan2(ly, lx))
        start, end = (a_closed, a_open) if side > 0 else (a_open, a_closed)
        add(room, msp.add_arc((hx, hy), r, start, end, dxfattribs={"layer": "A-DOOR"}))

    # stairs
    for room, fl, ln in STAIRS:
        add(room, msp.add_lwpolyline(shift(fl, ln), dxfattribs={"layer": "A-STAIR"}))

    # annotations, each linked to the smallest space containing it
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
            cands = [r for r, (fl, _) in spaces.items() if fl == floor]
            inside = [r for r in cands if point_in_poly((x, y), room_polys[r])]
            if inside:
                owner = min(inside, key=lambda r: poly_area(room_polys[r]))
            elif layer == "A-ANNO-TITLE":
                owner = "TITLES"
            else:  # dimension on a wall -> nearest room
                from shapely.geometry import Point
                owner = min((r for r in cands if r in ROOMS),
                            key=lambda r: Polygon(room_polys[r]).distance(Point(x, y)))
            add(owner, ent)

    place(GF_TEXT, gf, "GF")
    place(FF_TEXT, ff, "FF")

    for room, ents in members.items():
        grp = doc.groups.new(name=room.replace("-", "_"),
                             description=f"{room} with linked annotation")
        grp.extend(ents)

    doc.set_modelspace_vport(height=22, center=(12, 9))
    doc.saveas(OUT)
    return doc


def report():
    print("Room areas from geometry:")
    for room, (fl, poly) in ROOMS.items():
        a = poly_area(poly) - (poly_area(CHIMNEY[2]) if room == CHIMNEY[0] else 0)
        print(f"  {fl} {room:9s} {a:6.2f} m²")


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
                        color_policy=ColorPolicy.COLOR, lineweight_scaling=2.0)
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(doc.modelspace())
    fig.savefig(OUT.with_name("floor_plan_preview.png"), dpi=200)


if __name__ == "__main__":
    d = build()
    report()
    preview(d)
    print("written", OUT)
