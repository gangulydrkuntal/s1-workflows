import geopandas as gpd
import pytest
from PIL import Image, ImageDraw
from shapely.geometry import LineString

from plan2cad.models import PixelFrame
from plan2cad.verify import verify


@pytest.fixture()
def drawn(tmp_path):
    """A white 400x300 image with a 1 px black rectangle, and a GeoPackage holding the same rectangle in 'metres'."""
    im = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(im).rectangle([50, 50, 350, 250], outline=(0, 0, 0))
    src = tmp_path / "s.png"
    im.save(src)
    # 1 unit == 1 px, y up with origin at the bottom (row 300)
    ring = LineString([(50, 250), (350, 250), (350, 50), (50, 50), (50, 250)])
    gp = tmp_path / "o.gpkg"
    gpd.GeoDataFrame({"part": ["wall"]}, geometry=[ring], crs=None).to_file(gp, layer="walls", driver="GPKG")
    return src, gp


def test_exact_match_passes(drawn):
    src, gp = drawn
    v = verify(src, gp, PixelFrame(lambda x, y: (x, 300 - y)), 2.0, 0.9, (2, 2), 10)
    assert v.passed and v.share_within > 0.99 and v.p95_px <= 1.0 and v.coverage > 0.9


def test_shifted_output_is_caught_and_drawn_red(drawn, tmp_path):
    src, gp = drawn
    ov = tmp_path / "ov.png"
    v = verify(src, gp, PixelFrame(lambda x, y: (x + 8, 300 - y + 8)), 2.0, 0.9, (2, 2), 10, ov)
    assert not v.passed and v.share_within < 0.5
    k = 3                                                   # small images are enlarged 3x in the overlay
    assert ov.is_file() and Image.open(ov).size == (400 * k, 300 * k)
    px = Image.open(ov).convert("RGB")
    assert any(px.getpixel((x * k, (250 + 8) * k))[0] > 200 and px.getpixel((x * k, (250 + 8) * k))[1] < 80
               for x in range(60, 340))                      # red dots mark the unmatched points


def test_output_outside_image_counts_as_unmatched(drawn):
    src, gp = drawn
    v = verify(src, gp, PixelFrame(lambda x, y: (x + 1000, 300 - y)), 2.0, 0.9, (2, 2), 10)
    assert not v.passed and v.share_within == 0.0


def test_no_lines_message(tmp_path):
    from shapely.geometry import Point
    Image.new("RGB", (300, 200), "white").save(tmp_path / "s.png")
    gp = tmp_path / "o.gpkg"
    gpd.GeoDataFrame({"a": [1]}, geometry=[Point(1, 1)], crs=None).to_file(gp, layer="annotations", driver="GPKG")
    v = verify(tmp_path / "s.png", gp, PixelFrame(lambda x, y: (x, y)))
    assert not v.passed and "no line work" in v.message
