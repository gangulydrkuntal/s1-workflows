"""Bad input must produce a clear FAIL (never a crash), must not stop other drawings, and must always leave a report."""
import json
import subprocess
import sys

import pytest
import yaml
from conftest import CONFIGS, ROOT, write

from plan2cad import ocr
from plan2cad.errors import EngineError
from plan2cad.pipeline import run_config, run_many

IMG3 = ROOT / "inputs" / "drawing3_two_unit_ftin.png"


def _cfg3(tmp_path, **over):
    d = yaml.safe_load(CONFIGS["drawing3_two_unit_ftin"].read_text())
    d["source"] = str(IMG3)
    d["name"] = over.pop("name", "variant")
    d.update(over)
    return write(tmp_path / f"{d['name']}.yaml", yaml.safe_dump(d))


def _msgs(r):
    return " | ".join(f.message for f in r.flags if f.level == "error")


def test_corrupt_image(tmp_path):
    (tmp_path / "broken.png").write_text("not an image")
    c = write(tmp_path / "c.yaml", "name: broken\nengine: auto_lines\nsource: broken.png\nunits: mm\n"
              "calibration: {x: {px: [10, 90], length: '1000'}, y: {px: [90, 10], length: '1000'}}\n")
    r = run_config(c, tmp_path / "o")
    assert r.status == "FAIL" and "cannot read the source image" in _msgs(r)
    assert (tmp_path / "o" / "broken" / "review.md").is_file()          # report is written even on failure


def test_image_too_small(tmp_path):
    from PIL import Image
    Image.new("RGB", (100, 80), "white").save(tmp_path / "tiny.png")
    c = write(tmp_path / "c.yaml", "name: tiny\nengine: auto_lines\nsource: tiny.png\nunits: mm\n"
              "calibration: {x: {px: [10, 90], length: '1000'}, y: {px: [70, 10], length: '1000'}}\n")
    assert "too small" in _msgs(run_config(c, tmp_path / "o"))


def test_config_errors_are_reported_per_problem(tmp_path):
    c = write(tmp_path / "c.yaml", "name: bad\nengine: nope\nsource: missing.png\ntextz: []\n")
    r = run_config(c, tmp_path / "o")
    assert r.status == "FAIL" and sum(f.code == "CONFIG" for f in r.flags) == 3


def test_wrong_wall_gap_fails_with_actionable_message(tmp_path):
    r = run_config(_cfg3(tmp_path, params=dict(wall_gap_px=[9, 12])), tmp_path / "o")
    assert r.status == "FAIL" and "recognised as double-line walls" in _msgs(r) and "wall_gap_px" in _msgs(r)
    assert not list((tmp_path / "o" / "variant").glob("*.gpkg"))        # no half-baked output left behind


def test_unsuitable_drawing_fails_not_garbage(tmp_path):
    d = {"name": "wrongkind", "engine": "auto_lines", "source": str(ROOT / "inputs" / "drawing2_ground_floor_1to50.jpg"),
         "units": "mm", "calibration": {"x": {"px": [170, 1250], "length": "13790"}, "y": {"px": [848, 116], "length": "9350"}}}
    r = run_config(write(tmp_path / "w.yaml", yaml.safe_dump(d)), tmp_path / "o")
    assert r.status == "FAIL" and "recognised as double-line walls" in _msgs(r)


def test_text_box_without_text_is_rejected(tmp_path):
    d = yaml.safe_load(CONFIGS["drawing3_two_unit_ftin"].read_text())
    d["texts"][1]["box"] = [850, 20, 900, 40]                              # blank area
    r = run_config(_cfg3(tmp_path, texts=d["texts"]), tmp_path / "o")
    assert r.status == "FAIL" and "no text pixels inside box" in _msgs(r)


def test_text_box_outside_image_is_rejected(tmp_path):
    d = yaml.safe_load(CONFIGS["drawing3_two_unit_ftin"].read_text())
    d["texts"][0]["box"] = [990, 700, 2000, 800]
    r = run_config(_cfg3(tmp_path, texts=d["texts"]), tmp_path / "o")
    assert r.status == "FAIL" and "outside the 1000x708 image" in _msgs(r)


def test_stretched_scale_is_flagged(tmp_path):
    cal = {"x": {"px": [98, 886], "length": "60'"}, "y": {"px": [594, 175], "length": "26'"}}     # y wrong by ~20 %
    r = run_config(_cfg3(tmp_path, calibration=cal), tmp_path / "o")
    assert "SCALE_ANISOTROPY" in {f.code for f in r.flags}


def test_unknown_door_override_flagged(tmp_path):
    ov = {"D99": {"closed_deg": 0, "note": "refers to a door that does not exist"}}
    r = run_config(_cfg3(tmp_path, door_overrides=ov), tmp_path / "o")
    assert "OVERRIDE_UNKNOWN_DOOR" in {f.code for f in r.flags} and r.status != "FAIL"


def test_batch_isolation_and_unexpected_errors(tmp_path, monkeypatch):
    import plan2cad.engines.dimension_sheet as ds
    good = CONFIGS["drawing3_two_unit_ftin"]
    bad = write(tmp_path / "bad.yaml", "name: bad\nengine: nope\nsource: missing.png\n")
    boom = tmp_path / "boom.yaml"
    boom.write_text(f"name: boom\nengine: dimension_sheet\nsource: {ROOT / 'inputs' / 'drawing2_ground_floor_1to50.jpg'}\n")
    monkeypatch.setattr(ds, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated bug")))
    results = run_many([bad, boom, good], tmp_path / "o")
    st = {r.name: r.status for r in results}
    assert st == {"bad": "FAIL", "boom": "FAIL", "drawing3_two_unit_ftin": "REVIEW"}
    boom_r = next(r for r in results if r.name == "boom")
    assert "UNEXPECTED" in {f.code for f in boom_r.flags} and "simulated bug" in _msgs(boom_r)
    assert (tmp_path / "o" / "boom" / "review.md").is_file()
    assert (tmp_path / "o" / "drawing3_two_unit_ftin" / "drawing3_two_unit_ftin.gpkg").is_file()
    assert json.loads((tmp_path / "o" / "batch_summary.json").read_text())[0]["status"] == "FAIL"


def test_ocr_not_installed_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    base = dict(texts=[], dimension_texts=[], door_boxes="auto", door_overrides={})
    r = run_config(_cfg3(tmp_path, ocr="auto", **base), tmp_path / "o1")
    assert r.status != "FAIL" and "OCR_NOT_INSTALLED" in {f.code for f in r.flags}

    def unavailable():
        raise ocr.OcrUnavailable("rapidocr-onnxruntime is not installed")
    monkeypatch.setattr(ocr, "_engine", unavailable)
    r = run_config(_cfg3(tmp_path, name="v2", ocr="rapidocr", **base), tmp_path / "o2")
    assert r.status == "FAIL" and "not installed" in _msgs(r)


def test_ocr_off_without_texts_is_flagged_not_failed(tmp_path):
    r = run_config(_cfg3(tmp_path, texts=[], dimension_texts=[], door_boxes="auto", door_overrides={}), tmp_path / "o")
    assert r.status == "REVIEW" and "NO_TEXT" in {f.code for f in r.flags}


def test_two_processes_in_parallel(tmp_path):
    cmds = [[sys.executable, "-W", "ignore", "-m", "plan2cad", "run", str(CONFIGS[n]), "--out", str(tmp_path / f"p{i}"),
             "--log-level", "ERROR"] for i, n in enumerate(("drawing2_ground_floor_1to50", "drawing3_two_unit_ftin"))]
    procs = [subprocess.Popen(c, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for c in cmds]
    assert [p.wait(timeout=300) for p in procs] == [0, 0]
    assert (tmp_path / "p0" / "drawing2_ground_floor_1to50" / "drawing2_ground_floor_1to50.gpkg").is_file()
    assert (tmp_path / "p1" / "drawing3_two_unit_ftin" / "drawing3_two_unit_ftin.gpkg").is_file()


def test_engine_error_type():
    with pytest.raises(EngineError):
        raise EngineError("x")
