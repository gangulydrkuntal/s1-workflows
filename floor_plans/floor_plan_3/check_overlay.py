"""Draw floor_plan.gpkg over source_drawing.png to check alignment.

Usage: python check_overlay.py out.png
Red = walls, green = doors, magenta = stairs, orange = other lines,
cyan = dimension lines, blue outline = rooms, blue dots = text anchors.
"""
import sys
import warnings
from pathlib import Path

import geopandas as gpd
from PIL import Image, ImageDraw

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import build_geopackage as b  # noqa: E402

warnings.filterwarnings("ignore")
K = 3


def px(x, y):
    return ((b.PX0 + x / b.FT * b.SX) * K, (b.PY0 - y / b.FT * b.SY) * K)


def rings(g):
    if g.geom_type == "Polygon":
        return [list(g.exterior.coords)] + [list(i.coords) for i in g.interiors]
    if g.geom_type == "MultiPolygon":
        return [c for p in g.geoms for c in rings(p)]
    if g.geom_type == "MultiLineString":
        return [list(l.coords) for l in g.geoms]
    return [list(g.coords)]


src = Image.open(b.SRC).convert("RGB")
src = src.resize((src.width * K, src.height * K), Image.LANCZOS)
im = Image.blend(src, Image.new("RGB", src.size, "white"), 0.45)
d = ImageDraw.Draw(im)
for layer, c, w in (("rooms", (0, 90, 255), 1), ("wall_fill", (255, 0, 0), 2), ("doors", (0, 170, 0), 2),
                    ("stairs", (200, 0, 200), 2), ("other_lines", (255, 140, 0), 2), ("dimensions", (0, 170, 200), 2)):
    for g in gpd.read_file(b.GPKG, layer=layer).geometry:
        for cs in rings(g):
            d.line([px(*p) for p in cs], fill=c, width=w)
for g in gpd.read_file(b.GPKG, layer="annotations").geometry:
    x, y = px(g.x, g.y)
    d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(0, 90, 255))
out = sys.argv[1] if len(sys.argv) > 1 else str(HERE / "source_overlay.png")
im.save(out)
print("written", out)
