"""Draw ground_floor_plan.gpkg over source_drawing.jpg to check alignment.

Usage: python check_overlay.py out.png
Red = walls, orange = windows, green = doors, magenta = stairs/symbols,
cyan = dimensions/sheet, blue dots = text anchors.
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
K = 3   # upscale for readability


def px(x, y):
    return ((b.X0 + x * b.SX) * K, (b.Y0 - y * b.SY) * K)


def coords(g):
    if g.geom_type == "Polygon":
        return [list(g.exterior.coords)]
    if g.geom_type == "MultiPolygon":
        return [list(p.exterior.coords) for p in g.geoms]
    return [list(g.coords)]


src = Image.open(b.SRC).convert("RGB")
src = src.resize((src.width * K, src.height * K), Image.LANCZOS)
im = Image.blend(src, Image.new("RGB", src.size, "white"), 0.45)
d = ImageDraw.Draw(im)
style = {"wall_fill": (255, 0, 0), "walls": (255, 0, 0), "windows": (255, 140, 0), "window_lines": (255, 140, 0),
         "doors": (0, 170, 0), "stairs": (200, 0, 200), "symbols": (200, 0, 200), "dimensions": (0, 170, 200),
         "sheet": (0, 170, 200)}
for layer, c in style.items():
    for g in gpd.read_file(b.GPKG, layer=layer).geometry:
        for cs in coords(g):
            d.line([px(*p) for p in cs], fill=c, width=2)
for g in gpd.read_file(b.GPKG, layer="annotations").geometry:
    x, y = px(g.x, g.y)
    d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(0, 90, 255))
out = sys.argv[1] if len(sys.argv) > 1 else str(HERE / "source_overlay.png")
im.save(out)
print("written", out)
