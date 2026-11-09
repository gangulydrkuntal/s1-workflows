"""Export the floor plan DXF (floor_plan.dxf) to a GeoPackage (floor_plan.gpkg).

Run build_floor_plan_dxf.py first, then:  python build_geopackage.py

Coordinates are local metres (no real-world location is known), stored without
a CRS. Floors are laid out side by side exactly as on the source sketch and the
DXF (first floor 14 m to the right of the ground floor); `floor` / `level`
identify which floor each feature belongs to.
Each feature carries `room_id`, linking walls, doors, glazing and annotations
to the room they belong to.

Layers
  rooms        Polygon     rooms + corridor/lobby: name, type, floor, labelled / computed area
  wall_fill    Polygon     solid wall bands (door openings left open)
  wall_hatch   Polygon     hatched walls (13-OFF 0.4 m external wall, 08-OFF chimney breast)
  walls        LineString  wall faces: inner faces, outer building line, piers, partitions
  glazing      LineString  glass partitions
  doors        LineString  door leaves and swing arcs
  stairs       LineString  stair treads / winders
  annotations  Point       every text label, with rotation, height and kind
"""
import sqlite3
import warnings
from pathlib import Path

import ezdxf
import geopandas as gpd
from ezdxf import path as ezpath
from shapely.geometry import LineString, Point, Polygon

from . import two_floor_dxf as plan

HERE = Path(__file__).parent
DXF = HERE / "floor_plan.dxf"
GPKG = HERE / "floor_plan.gpkg"

ROOM_TYPES = {"OFF": "Office", "REC": "Reception", "BRD": "Boardroom",
              "WCS": "WC", "KTN": "Kitchen", "CBD": "Cleaning cupboard",
              "STAIR": "Stair hall", "CORRIDOR": "Corridor", "LOBBY": "Lobby"}
FLOOR_NAME = {"GF": "Ground Floor", "FF": "First Floor"}
LEVEL = {"GF": 0, "FF": 1}

warnings.filterwarnings("ignore", message=".*crs.*")


def room_of(doc):
    """entity handle -> room id, from the DXF groups."""
    owner = {}
    for name, grp in doc.groups:
        room = name.replace("_", "-")
        for e in grp:
            owner[e.dxf.handle] = room
    return owner


def floor_of(room, x):
    for table in (plan.ROOMS, plan.CIRCULATION):
        if room in table:
            return table[room][0]
    if room.endswith("-SHELL"):
        return room[:2]
    return "FF" if x >= plan.FF_DX - 1 else "GF"


def stacked(geom, floor):
    """Keep DXF sheet coordinates: floors side by side as on the source sketch."""
    return geom


def line_geom(e):
    if e.dxftype() == "LINE":
        return LineString([e.dxf.start.vec2, e.dxf.end.vec2])
    g = LineString([v.vec2 for v in ezpath.make_path(e).flattening(0.005)])
    if e.dxftype() == "LWPOLYLINE" and e.closed:
        g = LineString(list(g.coords) + [g.coords[0]])
    return g


def polys(geom):
    return list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]


def main():
    doc = ezdxf.readfile(DXF)
    msp = doc.modelspace()
    owner = room_of(doc)

    rows = {k: [] for k in ("wall_fill", "wall_hatch", "walls", "glazing", "doors",
                            "stairs", "annotations")}
    layer_map = {"A-WALL": "walls", "A-WALL-OUTER": "walls", "A-GLAZ": "glazing",
                 "A-DOOR": "doors", "A-STAIR": "stairs"}
    texts_by_room = {}

    def base(room, fl):
        return dict(room_id=room if room not in ("TITLES",) else "",
                    floor=FLOOR_NAME[fl], level=LEVEL[fl])

    for e in msp:
        lay = e.dxf.layer
        room = owner.get(e.dxf.handle, "")
        t = e.dxftype()
        if t == "TEXT":
            x, y = e.dxf.align_point.vec2
            fl = floor_of(room, x)
            kind = {"A-ANNO-ROOM": "room_label", "A-ANNO-DIM": "dimension",
                    "A-ANNO-TITLE": "title"}[lay]
            rows["annotations"].append(dict(
                base(room, fl), text=e.dxf.text, kind=kind, rotation=e.dxf.rotation,
                height_m=e.dxf.height, geometry=stacked(Point(x, y), fl)))
            texts_by_room.setdefault(room, []).append(e.dxf.text)
        elif lay in layer_map and t in ("LINE", "ARC", "LWPOLYLINE"):
            g = line_geom(e)
            fl = floor_of(room, g.coords[0][0])
            r = dict(base(room, fl), length_m=round(g.length, 3), geometry=stacked(g, fl))
            if lay == "A-DOOR":
                r["part"] = "swing arc" if t == "ARC" else "leaf"
            elif lay == "A-WALL-OUTER":
                r["part"] = "outer face"
            elif lay == "A-WALL":
                closed = t == "LWPOLYLINE" and e.closed
                r["part"] = ("pier / partition / chimney" if closed else "inner face")
            rows[layer_map[lay]].append(r)

    # solid wall bands and hatched walls straight from the plan geometry
    for fl in ("GF", "FF"):
        g = plan.floor_geometry(fl)
        for p in polys(g["poche"]):
            rows["wall_fill"].append(dict(base(f"{fl}-SHELL", fl), element="wall",
                                          area_m2=round(p.area, 2), geometry=stacked(p, fl)))
        if g["band"] is not None:
            band = g["band"].difference(plan.door_bands(fl))
            for p in polys(band):
                rows["wall_hatch"].append(dict(base("13-OFF", fl), element="external wall (0.4 m)",
                                               area_m2=round(p.area, 2), geometry=stacked(p, fl)))
    croom, cfl, cpoly = plan.CHIMNEY
    cp = Polygon(plan.shift(cfl, cpoly))
    rows["wall_hatch"].append(dict(base(croom, cfl), element="chimney breast",
                                   area_m2=round(cp.area, 2), geometry=stacked(cp, cfl)))

    # rooms (+ circulation) with attributes taken from their linked labels
    rooms = []
    for rid, (fl, poly) in {**plan.ROOMS, **plan.CIRCULATION}.items():
        labels = texts_by_room.get(rid, [])
        area_lbl = next((float(s.split()[0]) for s in labels if s.endswith("m²")), None)
        code = next((s for s in labels if s.isdigit()), None)
        geom = Polygon(plan.shift(fl, poly))
        if rid == croom:   # chimney breast is not floor area
            geom = geom.difference(cp)
        rtype = rid.split("-")[-1]
        rooms.append(dict(
            room_id=rid, room_type=ROOM_TYPES.get(rtype, rtype), floor=FLOOR_NAME[fl],
            level=LEVEL[fl], ref_code=code, area_label_m2=area_lbl,
            area_calc_m2=round(geom.area, 2), labels="; ".join(labels),
            geometry=stacked(geom, fl)))

    if GPKG.exists():
        GPKG.unlink()
    gpd.GeoDataFrame(rooms, geometry="geometry", crs=None).to_file(
        GPKG, layer="rooms", driver="GPKG")
    for name, data in rows.items():
        gpd.GeoDataFrame(data, geometry="geometry", crs=None).to_file(
            GPKG, layer=name, driver="GPKG", mode="a")
    add_qgis_styles()
    for name in ["rooms", *rows]:
        n = len(rooms) if name == "rooms" else len(rows[name])
        print(f"  {name:12s} {n:4d} features")
    print("written", GPKG)


# --------------------------------------------------------------------------- #
# Default QGIS styles stored in the GeoPackage (layer_styles table), so the
# plan opens in QGIS looking like the drawing: labels at the right size/angle.
# --------------------------------------------------------------------------- #
def _line_qml(color, width, dash=False):
    pen = "dash" if dash else "solid"
    return f"""<qgis version="3.28"><renderer-v2 type="singleSymbol"><symbols>
<symbol type="line" name="0"><layer class="SimpleLine">
<Option type="Map"><Option name="line_color" value="{color}" type="QString"/>
<Option name="line_width" value="{width}" type="QString"/>
<Option name="line_width_unit" value="MapUnit" type="QString"/>
<Option name="line_style" value="{pen}" type="QString"/></Option>
</layer></symbol></symbols></renderer-v2></qgis>"""


def _fill_qml(fill, outline, brush="solid"):
    return f"""<qgis version="3.28"><renderer-v2 type="singleSymbol"><symbols>
<symbol type="fill" name="0"><layer class="SimpleFill">
<Option type="Map"><Option name="color" value="{fill}" type="QString"/>
<Option name="style" value="{brush}" type="QString"/>
<Option name="outline_color" value="{outline}" type="QString"/>
<Option name="outline_width" value="0.02" type="QString"/>
<Option name="outline_width_unit" value="MapUnit" type="QString"/></Option>
</layer></symbol></symbols></renderer-v2></qgis>"""


ANNO_QML = """<qgis version="3.28">
<renderer-v2 type="nullSymbol"/>
<labeling type="simple"><settings calloutType="simple">
<text-style fieldName="text" fontSize="0.2" fontSizeUnit="MapUnit" textColor="0,0,200,255"/>
<placement placement="1" quadOffset="4" rotationAngle="0"/>
<rendering obstacle="0" displayAll="1" upsidedownLabels="2"/>
<dd_properties><Option type="Map"><Option name="properties" type="Map">
<Option name="Size" type="Map"><Option name="active" value="true" type="bool"/>
<Option name="expression" value="&quot;height_m&quot; * 1.35" type="QString"/>
<Option name="type" value="3" type="int"/></Option>
<Option name="LabelRotation" type="Map"><Option name="active" value="true" type="bool"/>
<Option name="expression" value="-&quot;rotation&quot;" type="QString"/>
<Option name="type" value="3" type="int"/></Option>
<Option name="Color" type="Map"><Option name="active" value="true" type="bool"/>
<Option name="expression" value="if(&quot;kind&quot;='dimension','0,0,0,255','0,0,200,255')" type="QString"/>
<Option name="type" value="3" type="int"/></Option>
</Option></Option></dd_properties>
</settings></labeling></qgis>"""


def add_qgis_styles():
    styles = {
        "rooms": _fill_qml("235,240,255,255", "160,170,200,255"),
        "wall_fill": _fill_qml("150,150,150,255", "150,150,150,255"),
        "walls": _line_qml("0,0,0,255", 0.03),
        "wall_hatch": _fill_qml("90,90,90,255", "0,0,0,255", brush="b_diagonal"),
        "glazing": _line_qml("0,0,200,255", 0.04, dash=True),
        "doors": _line_qml("0,0,200,255", 0.025),
        "stairs": _line_qml("0,0,200,255", 0.025),
        "annotations": ANNO_QML,
    }
    con = sqlite3.connect(GPKG)
    con.execute("""CREATE TABLE IF NOT EXISTS layer_styles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, f_table_catalog TEXT, f_table_schema TEXT,
        f_table_name TEXT, f_geometry_column TEXT, styleName TEXT, styleQML TEXT,
        styleSLD TEXT, useAsDefault BOOLEAN, description TEXT, owner TEXT, ui TEXT,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    for layer, qml in styles.items():
        con.execute("INSERT INTO layer_styles (f_table_catalog, f_table_schema, f_table_name,"
                    " f_geometry_column, styleName, styleQML, styleSLD, useAsDefault,"
                    " description) VALUES ('', '', ?, 'geom', ?, ?, '', 1, 'floor plan')",
                    (layer, layer, qml))
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
