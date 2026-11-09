"""Independent end-to-end check on a drawing the pipeline has never seen: a synthetic millimetre plan with known
geometry (two rooms, double-line walls). Proves the engine is not just memorising the three exercise drawings."""
import pytest
import yaml
from conftest import write
from PIL import Image, ImageDraw, ImageFont

from plan2cad.check import read_layer
from plan2cad.pipeline import run_config


def make_plan(tmp_path):
    W, H = 800, 600
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    for y in (100, 105, 495, 500):
        d.line([(100, y), (700, y)], fill=(0, 0, 0))
    for x in (100, 105, 695, 700, 398, 403):
        d.line([(x, 100 if x not in (398, 403) else 105), (x, 500 if x not in (398, 403) else 495)], fill=(0, 0, 0))
    font = ImageFont.load_default()
    boxes = {}
    for text, xy in (("Left", (200, 300)), ("Right", (500, 300))):
        b = d.textbbox(xy, text, font=font)
        d.text(xy, text, fill=(0, 0, 0), font=font)
        boxes[text] = [b[0] - 2, b[1] - 2, b[2] + 2, b[3] + 2]
    src = tmp_path / "synthetic.png"
    im.save(src)
    cfg = dict(name="synthetic_mm", engine="auto_lines", source=str(src), units="mm",
               calibration=dict(x=dict(px=[100, 700], length="6000"), y=dict(px=[500, 100], length="4000")),
               texts=[dict(text=t, kind="room_label", box=b) for t, b in boxes.items()], door_boxes="auto")
    return write(tmp_path / "synthetic.yaml", yaml.safe_dump(cfg))


def test_synthetic_millimetre_plan(tmp_path):
    r = run_config(make_plan(tmp_path), tmp_path / "out")
    assert r.status != "FAIL", [f.message for f in r.flags if f.level == "error"]
    rooms = read_layer(r.outputs["gpkg"], "rooms").set_index("name")
    assert set(rooms.index) == {"Left", "Right"}
    # 10 mm per pixel: left room 293 x 390 px = 2.93 x 3.90 m, right room 292 x 390 px
    assert rooms.loc["Left", "area_m2"] == pytest.approx(2.93 * 3.90, rel=0.04)
    assert rooms.loc["Right", "area_m2"] == pytest.approx(2.92 * 3.90, rel=0.04)
    assert r.verify["passed"] and r.verify["share_within"] > 0.95
    assert r.counts["walls"] >= 8 and "doors" not in r.counts and "stairs" not in r.counts   # absent features: no empty layers
    codes = {f.code for f in r.flags}
    assert "NO_DOORS" in codes and "FT_COLUMNS" in codes          # honest about what was not found / unit columns
    ann = read_layer(r.outputs["gpkg"], "annotations")
    assert sorted(ann["text"]) == ["Left", "Right"] and set(ann["room_id"]) == set(rooms["room_id"])


def test_synthetic_with_scale_error_is_visible_in_areas(tmp_path):
    """If a person mis-states a length, areas scale accordingly - the pipeline never silently 'corrects' input."""
    cfgp = make_plan(tmp_path)
    d = yaml.safe_load(cfgp.read_text())
    d["calibration"]["x"]["length"] = "12000"
    d["calibration"]["y"]["length"] = "8000"
    write(cfgp, yaml.safe_dump(d))
    r = run_config(cfgp, tmp_path / "out")
    rooms = read_layer(r.outputs["gpkg"], "rooms").set_index("name")
    assert rooms.loc["Left", "area_m2"] == pytest.approx(4 * 2.93 * 3.90, rel=0.04)
