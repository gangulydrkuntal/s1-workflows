import json

import pytest
from conftest import CONFIGS, ROOT
from golden_utils import compare_gpkg

from plan2cad.check import read_layer
from plan2cad.pipeline import run_config


def test_statuses_and_no_errors(batch):
    _, res = batch
    for name, r in res.items():
        assert not [f for f in r.flags if f.level == "error"], (name, [f.message for f in r.flags if f.level == "error"])
        assert all(s.status in ("ok", "skipped") for s in r.stages)
        # each drawing carries open questions from the exercise -> REVIEW, never a silent PASS
        assert r.status == "REVIEW", name


def test_outputs_exist(batch):
    out, res = batch
    for name, r in res.items():
        d = out / name
        for f in (f"{name}.gpkg", f"{name}_preview.png", f"{name}_overlay.png", "review.md", "summary.json", "run.log"):
            assert (d / f).is_file() and (d / f).stat().st_size > 0, (name, f)
    assert (out / "drawing1_two_floor_sketch" / "drawing1_two_floor_sketch.dxf").is_file()
    assert (out / "drawing1_two_floor_sketch" / "drawing1_two_floor_sketch_dxf_preview.png").is_file()
    assert not (out / "drawing2_ground_floor_1to50" / "drawing2_ground_floor_1to50.dxf").exists()
    assert (out / "batch_summary.md").is_file() and len(json.loads((out / "batch_summary.json").read_text())) == 3


@pytest.mark.parametrize("name,min_share", [("drawing1_two_floor_sketch", 0.93), ("drawing2_ground_floor_1to50", 0.93),
                                            ("drawing3_two_unit_ftin", 0.98)])
def test_verification_against_source(batch, name, min_share):
    _, res = batch
    v = res[name].verify
    assert v["passed"], v["zones"]
    assert v["share_within"] >= min_share
    assert v["n_samples"] > 10000 and len(v["zones"]) >= 6


def test_review_md_and_summary_contents(batch):
    out, _ = batch
    md = (out / "drawing3_two_unit_ftin" / "review.md").read_text()
    for heading in ("# drawing3_two_unit_ftin - REVIEW", "## Outputs", "## Review items", "## Verification against the source image",
                    "## Rooms and areas", "## Stages", "## Environment", "QUESTION", "UNLABELLED_SPACES"):
        assert heading in md
    s = json.loads((out / "drawing3_two_unit_ftin" / "summary.json").read_text())
    assert s["status"] == "REVIEW" and s["engine"] == "auto_lines" and s["sha256"] and s["counts"]["rooms"] == 20
    assert {"python", "geopandas", "ezdxf"} <= set(s["versions"])


def test_known_areas_from_the_exercise(batch):
    out, _ = batch
    r1 = read_layer(out / "drawing1_two_floor_sketch" / "drawing1_two_floor_sketch.gpkg", "rooms").set_index("room_id")
    assert r1.loc["06-OFF", "area_calc_m2"] == pytest.approx(62.88, abs=0.01)
    assert r1.loc["13-OFF", "area_calc_m2"] == pytest.approx(49.12, abs=0.01)     # net of piers; gross label 49.53
    assert r1.loc["13-OFF", "area_label_m2"] == pytest.approx(49.53)
    r2 = read_layer(out / "drawing2_ground_floor_1to50" / "drawing2_ground_floor_1to50.gpkg", "rooms").set_index("room_id")
    assert r2.loc["Bedroom 1", "area_calc_m2"] == pytest.approx(27.43, abs=0.01)
    r3 = read_layer(out / "drawing3_two_unit_ftin" / "drawing3_two_unit_ftin.gpkg", "rooms")
    beds = sorted(r3[r3["name"] == "Bedroom"]["area_ft2"].round(1))
    assert beds == [130.3, 135.3, 147.1, 148.4]


def test_every_annotation_is_linked_or_a_title(batch):
    out, _ = batch
    a = read_layer(out / "drawing3_two_unit_ftin" / "drawing3_two_unit_ftin.gpkg", "annotations")
    assert len(a) == 56
    labels = a[a["kind"] == "room_label"]
    assert len(labels) == 15 and (labels["room_id"] != "").all()          # every room label is linked to its room
    assert set(a[a["room_id"] == ""]["kind"]) <= {"site_label", "dimension"}   # only outside labels / overall dims are unlinked


def test_deterministic_reruns(batch, tmp_path):
    out, _ = batch
    r = run_config(CONFIGS["drawing3_two_unit_ftin"], tmp_path)
    assert compare_gpkg(r.outputs["gpkg"], out / "drawing3_two_unit_ftin" / "drawing3_two_unit_ftin.gpkg") == []


def test_rerun_into_same_folder_overwrites_cleanly(tmp_path):
    n = "drawing2_ground_floor_1to50"
    run_config(CONFIGS[n], tmp_path)
    d = tmp_path / n
    (d / f"{n}_old_texts_proposed.yaml").write_text("stale output of an earlier run")     # ours: removed on re-run
    (d / "my_notes.txt").write_text("a person's own file")                                  # not ours: must survive
    run_config(CONFIGS[n], tmp_path)
    names = {p.name for p in d.iterdir()}
    assert f"{n}.gpkg" in names and f"{n}_old_texts_proposed.yaml" not in names and "my_notes.txt" in names


def test_no_verify_option(tmp_path):
    r = run_config(CONFIGS["drawing2_ground_floor_1to50"], tmp_path, do_verify=False)
    assert r.verify is None and "overlay" not in r.outputs and r.status == "REVIEW"


def test_profile_rejects_wrong_size_image(tmp_path):
    from PIL import Image
    src = Image.open(ROOT / "inputs" / "drawing2_ground_floor_1to50.jpg")
    src.resize((800, 540)).save(tmp_path / "small.jpg")
    cfg = tmp_path / "c.yaml"
    cfg.write_text("name: wrongsize\nengine: dimension_sheet\nsource: small.jpg\n")
    r = run_config(cfg, tmp_path / "out")
    assert r.status == "FAIL" and "tied to a 1600x1079 image" in " ".join(f.message for f in r.flags)


def test_profile_flags_modified_image_of_same_size(tmp_path):
    from PIL import Image, ImageDraw
    im = Image.open(ROOT / "inputs" / "drawing2_ground_floor_1to50.jpg").convert("RGB")
    ImageDraw.Draw(im).rectangle([5, 5, 30, 30], fill=(0, 0, 0))
    im.save(tmp_path / "mod.jpg", quality=95)
    cfg = tmp_path / "c.yaml"
    cfg.write_text("name: modified\nengine: dimension_sheet\nsource: mod.jpg\n")
    r = run_config(cfg, tmp_path / "out")
    assert r.status in ("REVIEW", "FAIL")
    assert "SOURCE_DIFFERS" in {f.code for f in r.flags}


def test_legacy_modules_restored_after_run(batch):
    import plan2cad.engines.legacy.auto_lines_core as core
    import plan2cad.engines.legacy.dimension_sheet as m
    assert core.WALL_GAP == (3.5, 7.5) and core.TEXTS[0][0] == "Road" and core.EXTRA_TEXT_BOXES == []
    assert m.SRC.name == "source_drawing.jpg"          # engine patches must not leak between runs
