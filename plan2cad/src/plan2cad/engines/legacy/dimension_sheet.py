"""Ground Floor Plan (Griffith College Dublin class exercise, scale 1:50) -> GeoPackage.

Source: source_drawing.jpg (1600 x 1079 px, orthographic CAD export, no skew).
Method: PROMPT_floor_plan_to_cad_gis.md (Phases 0-9).

Units: metres, origin = outer south-west corner of the building, Y up, no CRS.
Calibration: stroke-centre pixel positions of the labelled outer faces
  x: px 170 -> 0.000 m, px 1250 -> 13.790 m   => 78.32 px/m
  y: py 848 -> 0.000 m, py 116  -> 9.350 m    => 78.30 px/m
Every value below is tagged  L = from a dimension label,  P = measured in pixels.

Run:  python build_geopackage.py   (writes ground_floor_plan.gpkg + previews)
"""
import math
import sqlite3
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
from PIL import Image
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

HERE = Path(__file__).parent
SRC = HERE / "source_drawing.jpg"
GPKG = HERE / "ground_floor_plan.gpkg"
warnings.filterwarnings("ignore", message=".*crs.*")

SX, X0 = 78.32, 170.0
SY, Y0 = 78.30, 848.0


def m(px, py):
    """pixel -> metres"""
    return ((px - X0) / SX, (Y0 - py) / SY)


def mx(px):
    return (px - X0) / SX


def my(py):
    return (Y0 - py) / SY


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# --------------------------------------------------------------------------- #
# Key coordinates (metres)
# --------------------------------------------------------------------------- #
W, H = 13.790, 9.350            # L  overall: top string sum 13789.92, left/right strings 9350
T = 0.300                       # L  external wall thickness (300 on every string)
XI0, XI1, YI0, YI1 = T, 13.490, T, 9.050   # L  inner faces of external walls
P1 = (6.215, 6.315)             # L  wall B1/B3 | hall   (2706+1725+1783.92 ; +100)
P_BATH_E = (8.938, 9.038)       # L  wall bathroom | bedroom 2 (… +736 ; +100)
P_B13 = (4.30, 4.40)            # P  partition bedroom 1 | bedroom 3 (py 504..511, 100 thick per internal walls)
P_BATH_S = (6.04, 6.135)        # P  bathroom south wall (py 368..375)
P_B2_W = (8.31, 8.39)           # P  short wall with bedroom-2 door (px 821..827)
P_B2_S = (4.90, 5.00)           # L  right string 4050 / 100
P_W3 = (10.10, 10.20)           # P  wall stair | ensuite/void (px 961..967)
P_ENS_S = (3.384, 3.484)        # L  right string 1416 / 100 / 3084

# chimney breast on west wall
CB_TOP, CB_MID, CB_STEP, CB_BOT = 4.661, 3.902, 3.411, 2.661   # L,P,L,L  (4389/1250/750/2361)
CB_FACE, CB_RECESS = 0.594, 0.402                              # P  (px 216.5 / 201.5)

# --------------------------------------------------------------------------- #
# Spaces (inside faces)
# --------------------------------------------------------------------------- #
ROOMS = {
    "Bedroom 1": ("Bedroom", [(CB_FACE, P_B13[1]), (P1[0], P_B13[1]), (P1[0], YI1), (XI0, YI1),
                              (XI0, CB_TOP), (CB_FACE, CB_TOP)]),
    "Bedroom 3": ("Bedroom", [(XI0, YI0), (P1[0], YI0), (P1[0], P_B13[0]), (CB_FACE, P_B13[0]),
                              (CB_FACE, CB_MID), (CB_RECESS, CB_MID), (CB_RECESS, CB_STEP),
                              (CB_FACE, CB_STEP), (CB_FACE, CB_BOT), (XI0, CB_BOT)]),
    "Bathroom": ("Bathroom", rect(P1[1], P_BATH_S[1], P_BATH_E[0], YI1)),
    "Bedroom 2": ("Bedroom", [(P_B2_W[1], P_B2_S[1]), (XI1, P_B2_S[1]), (XI1, YI1), (P_BATH_E[1], YI1),
                              (P_BATH_E[1], P_BATH_S[0]), (P_B2_W[1], P_BATH_S[0])]),
    "Ensuite": ("Ensuite", rect(P_W3[1], P_ENS_S[1], XI1, P_B2_S[0])),
    # unlabelled spaces: named by what is drawn, not by assumed use
    "Hall / landing (unlabelled)": ("Circulation", [(P1[1], YI0), (P_W3[0], YI0), (P_W3[0], P_B2_S[0]),
                                                    (P_B2_W[0], P_B2_S[0]), (P_B2_W[0], P_BATH_S[0]),
                                                    (P1[1], P_BATH_S[0])]),
    "Cross-marked area (unlabelled)": ("Unlabelled (cross-marked)", rect(P_W3[1], YI0, XI1, P_ENS_S[0])),
}
LABELLED = {"Bedroom 1", "Bedroom 2", "Bedroom 3", "Bathroom", "Ensuite"}

CHIMNEY = Polygon([(XI0, CB_TOP), (CB_FACE, CB_TOP), (CB_FACE, CB_MID), (CB_RECESS, CB_MID),
                   (CB_RECESS, CB_STEP), (CB_FACE, CB_STEP), (CB_FACE, CB_BOT), (XI0, CB_BOT)])

# SE corner: unfilled square in the external wall (534 = square + 300 wall)
POST = box(13.253, 0.0, XI1, T)   # L (13253) + P (px 1208..1227, py 825..848)

# --------------------------------------------------------------------------- #
# Doors: (id, room, hinge, closed_end, swing +1 CCW / -1 CW)  - all P (arcs traced)
# --------------------------------------------------------------------------- #
DOORS = [
    ("D1", "Bedroom 1", (P1[0], 4.45), (P1[0], 5.25), +1),                 # leaf along partition, swings into B1
    ("D2", "Bedroom 3", (P1[0], 4.25), (P1[0], 3.45), -1),                 # mirror of D1, swings into B3
    ("D3", "Bathroom", (6.60, P_BATH_S[0]), (7.41, P_BATH_S[0]), +1),      # leaf up into bathroom
    ("D4", "Bedroom 2", (P_B2_W[1], 5.90), (P_B2_W[1], 5.10), +1),         # leaf east into B2 alcove
    ("D5", "Ensuite", (10.60, P_B2_S[0]), (11.39, P_B2_S[0]), -1),         # leaf down into ensuite
]

# --------------------------------------------------------------------------- #
# Windows: (id, wall, x0, x1, source)  top wall y 9.05..9.35, bottom wall y 0..0.30
# --------------------------------------------------------------------------- #
WINDOWS = [
    ("W1", "N", 2.706, 4.431, "L"),
    ("W2", "N", 6.52, 8.202, "P start (unlabelled) + L end"),
    ("W3", "N", 10.603, 12.303, "L"),
    ("W4", "S", 1.486, 3.186, "L"),
    ("W5", "S", 3.499, 5.199, "L"),
    ("W6", "S", 6.499, 8.199, "L"),
    ("W7", "S", 10.401, 13.253, "L"),
]
SILLS = {"W1": (mx(377), mx(519)), "W2": (mx(674), mx(816)), "W3": (mx(995), mx(1137)),
         "W4": (mx(282), mx(424)), "W5": (mx(440), mx(581)), "W6": (mx(675), mx(816)),
         "W7": (mx(981), mx(1212))}                                        # P rows 112 / 852
GLAZING_Y = {"N": (my(124), my(127)), "S": (my(833), my(836))}            # P
SILL_Y = {"N": my(112), "S": my(852)}                                     # P
MULLIONS = [(mx(1063), mx(1066)), (mx(1136), mx(1139))]                   # P  in W7

# --------------------------------------------------------------------------- #
# Stairs (P, traced from px)
# --------------------------------------------------------------------------- #
STAIR_LINES = [
    ("outline", [m(820, 465), m(820, 539.5), m(881, 539.5), m(881, 595)]),
    *[("tread", [m(x, 465), m(x, 538)]) for x in (841.7, 860.7, 880)],
    *[("tread", [m(882, y), m(960, y)]) for y in (545, 565, 584)],
    ("tread", [m(923, 604), m(960, 604)]),
    ("break line", [m(871.7, 594), m(916.7, 606.7), m(918.3, 591.7), m(921.7, 618.3), m(963.3, 624)]),
]
VOID_CROSS = [[(P_W3[1], P_ENS_S[0]), (XI1, YI0)], [(P_W3[1], YI0), (XI1, P_ENS_S[0])]]

# --------------------------------------------------------------------------- #
# Dimension strings (all L, cumulative from the labels); line offsets P
# --------------------------------------------------------------------------- #
DIMS = {
    "top": (my(81), "x", ["2706", "1725", "1783,92", "100", "1887", "736", "100", "1565", "1700", "1187", "300"]),
    "bottom": (my(906), "x", ["1486", "1700", "313", "1700", "1300", "1700", "2202", "2852", "534"]),
    "left": (mx(114.5), "y-down", ["300", "4389", "1250", "750", "2361", "300"]),
    "right": (mx(1341), "y-down", ["300", "4050", "100", "1416", "100", "3084", "300"]),
}

# --------------------------------------------------------------------------- #
# Text: (text, kind, approx px box, rotation)  - anchor/height measured from pixels
# --------------------------------------------------------------------------- #
TEXTS = [
    ("Bedroom 1", "room_label", (310, 225, 480, 262), 0), ("Bathroom", "room_label", (685, 225, 835, 262), 0),
    ("Bedroom 2", "room_label", (970, 225, 1140, 262), 0), ("Ensuite", "room_label", (1045, 520, 1165, 556), 0),
    ("Bedroom 3", "room_label", (310, 630, 480, 668), 0),
    ("Ground Floor Plan", "title", (200, 960, 430, 995), 0), ("Scale 1:50", "title", (200, 997, 335, 1032), 0),
    ("PRODUCED BY AN AUTODESK EDUCATIONAL PRODUCT", "watermark", (540, 0, 1045, 15), 0),
    ("PRODUCED BY AN AUTODESK EDUCATIONAL PRODUCT", "watermark", (550, 1062, 1055, 1079), 180),
    ("PRODUCED BY AN AUTODESK EDUCATIONAL PRODUCT", "watermark", (0, 300, 16, 800), 90),
    ("PRODUCED BY AN AUTODESK EDUCATIONAL PRODUCT", "watermark", (1582, 280, 1600, 790), 270),
    ("Name:", "title_block", (1246, 944, 1278, 954), 0), ("Caroline Maguire", "title_block", (1243, 957, 1355, 976), 0),
    ("Griffith College Dublin", "title_block", (1243, 999, 1360, 1012), 0),
    ("South Circular Road", "title_block", (1243, 1012, 1350, 1025), 0),
    ("Dublin 8", "title_block", (1243, 1025, 1295, 1038), 0),
    ("Project:", "title_block", (1376, 941, 1408, 951), 0), ("Class Exercise", "title_block", (1385, 952, 1485, 970), 0),
    ("Project:", "title_block", (1376, 983, 1408, 993), 0), ("Exam Preperation", "title_block", (1385, 994, 1505, 1012), 0),
    ("Scale:", "title_block", (1376, 1025, 1402, 1034), 0), ("1:50", "title_block", (1385, 1035, 1422, 1054), 0),
    ("Date:", "title_block", (1434, 1025, 1460, 1034), 0), ("24/01/11", "title_block", (1435, 1035, 1490, 1052), 0),
    ("Dwg No:", "title_block", (1492, 1025, 1528, 1034), 0), ("1", "title_block", (1510, 1035, 1528, 1054), 0),
]
DIM_TEXT_BOX = {   # approx px boxes of each dimension label, same order as DIMS
    "top": [(255, 52, 300, 73), (428, 52, 472, 73), (553, 50, 622, 72), (645, 52, 678, 73), (718, 52, 762, 73),
            (824, 52, 858, 73), (860, 52, 890, 73), (918, 52, 962, 73), (1046, 52, 1090, 73), (1160, 52, 1204, 73),
            (1222, 52, 1258, 73)],
    "bottom": [(205, 878, 250, 898), (330, 878, 375, 898), (415, 878, 450, 898), (490, 878, 535, 898),
               (606, 878, 652, 898), (725, 878, 770, 898), (878, 878, 922, 898), (1075, 878, 1120, 898),
               (1198, 878, 1234, 898)],
    "left": [(88, 112, 106, 146), (88, 300, 106, 348), (88, 522, 106, 572), (88, 592, 106, 628),
             (88, 742, 106, 792), (88, 818, 106, 854)],
    "right": [(1315, 110, 1334, 146), (1315, 272, 1334, 322), (1315, 442, 1334, 478), (1315, 498, 1334, 548),
              (1315, 560, 1334, 598), (1315, 678, 1334, 728), (1315, 818, 1334, 854)],
}

# Sheet frame and title block (P)
SHEET_LINES = [
    ("sheet border", [m(18.5, 13), m(1587.5, 13), m(1587.5, 1075.5), m(18.5, 1075.5), m(18.5, 13)]),
    ("title block divider", [m(1373, 938.5), m(1373, 1063.5)]),
    ("title block divider", [m(1373, 980.5), m(1549.5, 980.5)]),
    ("title block divider", [m(1373, 1022), m(1549.5, 1022)]),
    ("title block divider", [m(1431.5, 1022), m(1431.5, 1063.5)]),
    ("title block divider", [m(1490, 1022), m(1490, 1063.5)]),
]
TITLE_BLOCK_OUTLINE = box(*m(1235.5, 1063.5), *m(1549.5, 938.5))


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def door_band(h, e, depth=0.2):
    return LineString([h, e]).buffer(depth, cap_style="flat")


def window_poly(wall, x0, x1):
    return box(x0, YI1, x1, H) if wall == "N" else box(x0, 0.0, x1, YI0)


def text_anchor(img, bx, rot):
    x0, y0, x1, y1 = bx
    sub = img[y0:y1, x0:x1] < 120
    ys, xs = np.where(sub)
    if len(xs) == 0:
        raise ValueError(f"no text pixels in box {bx}")
    cx, cy = x0 + (xs.min() + xs.max()) / 2, y0 + (ys.min() + ys.max()) / 2
    size_px = (ys.max() - ys.min() + 1) if rot in (0, 180) else (xs.max() - xs.min() + 1)
    return m(cx, cy), size_px / SY


def owner_of(pt, polys):
    p = Point(pt)
    inside = [r for r, g in polys.items() if g.contains(p)]
    if inside:
        return min(inside, key=lambda r: polys[r].area)
    return ""


def lines(g):
    if g.is_empty:
        return []
    if g.geom_type in ("LineString", "LinearRing"):
        return [LineString(g.coords)]
    return [x for x in g.geoms if x.geom_type == "LineString"]


def polys(g):
    return [g] if g.geom_type == "Polygon" else list(g.geoms)


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def build():
    img = np.asarray(Image.open(SRC).convert("L")).astype(int)
    room_geo = {r: Polygon(p) for r, (_, p) in ROOMS.items()}
    door_cuts = unary_union([door_band(h, e) for _, _, h, e, _ in DOORS])
    win_polys = {wid: window_poly(w, a, b) for wid, w, a, b, _ in WINDOWS}
    openings = unary_union([door_cuts, *win_polys.values(), POST])

    footprint = box(0, 0, W, H)
    poche = footprint.difference(unary_union(list(room_geo.values()))).difference(openings)
    walls_only = poche.difference(CHIMNEY)

    L = {k: [] for k in ("rooms", "wall_fill", "walls", "windows", "window_lines", "doors", "stairs",
                         "symbols", "dimensions", "annotations", "sheet")}

    # rooms
    for r, (rtype, _) in ROOMS.items():
        g = room_geo[r]
        L["rooms"].append(dict(room_id=r, name=r if r in LABELLED else "", room_type=rtype,
                               area_calc_m2=round(g.area, 2), labels="", status="VERIFIED"
                               if r in LABELLED else "VERIFIED (use not labelled)", geometry=g))
    # wall fill
    for p in polys(walls_only):
        L["wall_fill"].append(dict(element="wall", room_id="", area_m2=round(p.area, 3), geometry=p))
    L["wall_fill"].append(dict(element="chimney breast", room_id="Bedroom 3", area_m2=round(CHIMNEY.area, 3),
                               geometry=CHIMNEY))
    # wall lines: outer face + inner faces, openings removed
    for ln in lines(footprint.exterior.difference(openings)):
        L["walls"].append(dict(part="outer face", room_id="", geometry=ln))
    for r, g in room_geo.items():
        for ln in lines(g.exterior.difference(openings)):
            L["walls"].append(dict(part="inner face", room_id=r, geometry=ln))
    for ln in lines(POST.exterior):
        L["walls"].append(dict(part="unfilled post outline", room_id="", geometry=ln))

    # windows
    for wid, wall, a, b, src in WINDOWS:
        g = win_polys[wid]
        rid = owner_of((0.5 * (a + b), YI1 - 0.2 if wall == "N" else YI0 + 0.2), room_geo)
        L["windows"].append(dict(window_id=wid, wall=wall, part="opening", width_m=round(b - a, 3),
                                 room_id=rid, source=src, geometry=g))
        for gy in GLAZING_Y[wall]:
            L["window_lines"].append(dict(window_id=wid, part="glazing", geometry=LineString([(a, gy), (b, gy)])))
        s0, s1 = SILLS[wid]
        face = H if wall == "N" else 0.0
        L["window_lines"].append(dict(window_id=wid, part="sill", geometry=LineString(
            [(s0, face), (s0, SILL_Y[wall]), (s1, SILL_Y[wall]), (s1, face)])))
    for a, b in MULLIONS:
        L["windows"].append(dict(window_id="W7", wall="S", part="mullion", width_m=round(b - a, 3),
                                 room_id="Cross-marked area (unlabelled)", source="P",
                                 geometry=box(a, GLAZING_Y["S"][1] - 0.03, b, GLAZING_Y["S"][0] + 0.03)))
    L["windows"].append(dict(window_id="POST-SE", wall="S", part="unfilled square (purpose not stated)",
                             width_m=round(POST.bounds[2] - POST.bounds[0], 3), room_id="", source="L+P",
                             geometry=POST))

    # doors
    for did, rid, (hx, hy), (ex, ey), side in DOORS:
        vx, vy = ex - hx, ey - hy
        r = math.hypot(vx, vy)
        lx, ly = (-vy, vx) if side > 0 else (vy, -vx)
        L["doors"].append(dict(door_id=did, room_id=rid, part="leaf", width_m=round(r, 2),
                               geometry=LineString([(hx, hy), (hx + lx, hy + ly)])))
        a0, a1 = math.atan2(vy, vx), math.atan2(ly, lx)
        if side < 0:
            a0, a1 = a1, a0
        if a1 < a0:
            a1 += 2 * math.pi
        arc = [(hx + r * math.cos(t), hy + r * math.sin(t)) for t in np.linspace(a0, a1, 32)]
        L["doors"].append(dict(door_id=did, room_id=rid, part="swing arc", width_m=round(r, 2),
                               geometry=LineString(arc)))

    # stairs + symbols
    for part, pts in STAIR_LINES:
        L["stairs"].append(dict(part=part, room_id="Hall / landing (unlabelled)", geometry=LineString(pts)))
    for pts in VOID_CROSS:
        L["symbols"].append(dict(symbol="cross (diagonals)", room_id="Cross-marked area (unlabelled)",
                                 geometry=LineString(pts)))

    # dimensions: segments from labels; text anchors measured
    for name, (off, axis, vals) in DIMS.items():
        pos = 0.0 if axis == "x" else H
        for v, bx in zip(vals, DIM_TEXT_BOX[name]):
            mm = float(v.replace(",", "."))
            nxt = pos + mm / 1000 if axis == "x" else pos - mm / 1000
            seg = LineString([(pos, off), (nxt, off)]) if axis == "x" else LineString([(off, pos), (off, nxt)])
            L["dimensions"].append(dict(string=name, value_text=v, value_mm=mm, geometry=seg))
            (tx, ty), hgt = text_anchor(img, bx, 0 if axis == "x" else 90)
            L["annotations"].append(dict(text=v, kind="dimension", rotation=0 if axis == "x" else 90,
                                         height_m=round(hgt, 3), room_id="", dim_string=name,
                                         geometry=Point(tx, ty)))
            pos = nxt

    for text, kind, bx, rot in TEXTS:
        (tx, ty), hgt = text_anchor(img, bx, rot)
        rid = owner_of((tx, ty), room_geo) if kind == "room_label" else ""
        L["annotations"].append(dict(text=text, kind=kind, rotation=rot, height_m=round(hgt, 3),
                                     room_id=rid, dim_string="", geometry=Point(tx, ty)))
        if rid:
            for row in L["rooms"]:
                if row["room_id"] == rid:
                    row["labels"] = text

    for part, pts in SHEET_LINES:
        L["sheet"].append(dict(part=part, geometry=LineString(pts)))
    tb = TITLE_BLOCK_OUTLINE.buffer(-0.22, join_style="round").buffer(0.22, join_style="round")
    L["sheet"].append(dict(part="title block outline", geometry=LineString(tb.exterior.coords)))

    if GPKG.exists():
        GPKG.unlink()
    for name, rows in L.items():
        gpd.GeoDataFrame(rows, geometry="geometry", crs=None).to_file(
            GPKG, layer=name, driver="GPKG", mode="w" if name == "rooms" else "a")
    add_styles()
    return L


# --------------------------------------------------------------------------- #
# QGIS default styles (layer_styles table)
# --------------------------------------------------------------------------- #
def _line(color, width, dash=False):
    return (f'<qgis version="3.28"><renderer-v2 type="singleSymbol"><symbols><symbol type="line" name="0">'
            f'<layer class="SimpleLine"><Option type="Map"><Option name="line_color" value="{color}" type="QString"/>'
            f'<Option name="line_width" value="{width}" type="QString"/><Option name="line_width_unit" value="MapUnit" type="QString"/>'
            f'<Option name="line_style" value="{"dash" if dash else "solid"}" type="QString"/></Option></layer>'
            f'</symbol></symbols></renderer-v2></qgis>')


def _fill(fill, outline):
    return (f'<qgis version="3.28"><renderer-v2 type="singleSymbol"><symbols><symbol type="fill" name="0">'
            f'<layer class="SimpleFill"><Option type="Map"><Option name="color" value="{fill}" type="QString"/>'
            f'<Option name="outline_color" value="{outline}" type="QString"/><Option name="outline_width" value="0.015" type="QString"/>'
            f'<Option name="outline_width_unit" value="MapUnit" type="QString"/></Option></layer>'
            f'</symbol></symbols></renderer-v2></qgis>')


ANNO = ('<qgis version="3.28"><renderer-v2 type="nullSymbol"/><labeling type="simple"><settings calloutType="simple">'
        '<text-style fieldName="text" fontSize="0.2" fontSizeUnit="MapUnit" textColor="0,0,0,255"/>'
        '<placement placement="1" quadOffset="4"/><rendering obstacle="0" displayAll="1" upsidedownLabels="2"/>'
        '<dd_properties><Option type="Map"><Option name="properties" type="Map">'
        '<Option name="Size" type="Map"><Option name="active" value="true" type="bool"/>'
        '<Option name="expression" value="&quot;height_m&quot; * 1.35" type="QString"/><Option name="type" value="3" type="int"/></Option>'
        '<Option name="LabelRotation" type="Map"><Option name="active" value="true" type="bool"/>'
        '<Option name="expression" value="-&quot;rotation&quot;" type="QString"/><Option name="type" value="3" type="int"/></Option>'
        '</Option></Option></dd_properties></settings></labeling></qgis>')


def add_styles():
    styles = {"rooms": _fill("250,250,250,255", "200,200,200,255"), "wall_fill": _fill("128,128,128,255", "0,0,0,255"),
              "walls": _line("0,0,0,255", 0.02), "windows": _fill("255,255,255,255", "0,0,0,255"),
              "window_lines": _line("0,0,0,255", 0.01), "doors": _line("0,0,0,255", 0.01),
              "stairs": _line("0,0,0,255", 0.01), "symbols": _line("0,0,0,255", 0.01),
              "dimensions": _line("120,120,120,255", 0.01), "sheet": _line("0,0,0,255", 0.02), "annotations": ANNO}
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
    out = build()
    for k, v in out.items():
        print(f"  {k:13s} {len(v):4d}")
    print("written", GPKG)
