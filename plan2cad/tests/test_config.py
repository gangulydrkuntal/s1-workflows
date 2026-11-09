import pytest
from conftest import CONFIGS, ROOT, write

from plan2cad.config import config_from_dict, load_config
from plan2cad.errors import ConfigError


@pytest.mark.parametrize("path", [*CONFIGS.values(), ROOT / "configs" / "examples" / "drawing3_minimal_auto.yaml"])
def test_shipped_configs_valid(path):
    cfg = load_config(path)
    assert cfg.source.is_file()


def test_shipped_drawing3_content():
    cfg = load_config(CONFIGS["drawing3_two_unit_ftin"])
    assert (len(cfg.texts), len(cfg.dimension_texts), len(cfg.door_boxes)) == (16, 40, 15)
    assert set(cfg.door_overrides) == {"D05", "D10", "D11", "D13", "D14"}
    assert cfg.calibration["x"]["px"] == [98.0, 886.0]


def _minimal(tmp_path, **over):
    img = tmp_path / "a.png"
    if not img.exists():
        from PIL import Image
        Image.new("RGB", (400, 300), "white").save(img)
    d = dict(name="t", engine="auto_lines", source="a.png",
             calibration=dict(x=dict(px=[10, 390], length="60'"), y=dict(px=[290, 10], length="40'")))
    d.update(over)
    return d


def _problems(tmp_path, **over):
    with pytest.raises(ConfigError) as e:
        config_from_dict(_minimal(tmp_path, **over), base_dir=tmp_path)
    return " | ".join(e.value.problems)


def test_valid_minimal(tmp_path):
    cfg = config_from_dict(_minimal(tmp_path), base_dir=tmp_path)
    assert cfg.door_boxes == "auto" and cfg.ocr == "off" and cfg.verify["tolerance_px"] == 3.0


def test_file_and_yaml_errors(tmp_path):
    with pytest.raises(ConfigError, match="file not found"):
        load_config(tmp_path / "nope.yaml")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(write(tmp_path / "x.yaml", "a: [unclosed"))
    with pytest.raises(ConfigError, match="mapping"):
        load_config(write(tmp_path / "y.yaml", "- just\n- a list\n"))


def test_unknown_field_suggestion(tmp_path):
    assert "did you mean 'texts'" in _problems(tmp_path, textz=[])


@pytest.mark.parametrize("over,needle", [
    (dict(engine="auto_line"), "engine: must be one of"),
    (dict(name="bad name!"), "name: required"),
    (dict(source="missing.png"), "image not found"),
    (dict(source="a.txt"), "unsupported image type"),
    (dict(outputs=["dxf"]), "cannot write ['dxf']"),
    (dict(outputs=["shp"]), "unknown format"),
    (dict(units="furlongs"), "units: must be one of"),
    (dict(ocr="maybe"), "ocr: must be one of"),
    (dict(calibration=dict(x=dict(px=[390, 10], length="60'"), y=dict(px=[290, 10], length="40'"))), "LEFT end"),
    (dict(calibration=dict(x=dict(px=[10, 390], length="60'"), y=dict(px=[10, 290], length="40'"))), "BOTTOM end"),
    (dict(calibration=dict(x=dict(px=[10, 390], length="sixty"), y=dict(px=[290, 10], length="40'"))), "calibration.x.length"),
    (dict(calibration=None), "calibration: required"),
    (dict(params=dict(wall_gap_px=[7, 3])), "wall_gap_px"),
    (dict(params=dict(bogus=1)), "params.bogus"),
    (dict(texts=[dict(text="A", box=[10, 10, 5, 5])]), "box must satisfy"),
    (dict(texts=[dict(text="A", box=[1, 2, 3])]), "[x0, y0, x1, y1]"),
    (dict(texts=[dict(text="A", box=[1, 1, 9, 9], kind="odd")]), "kind"),
    (dict(dimension_texts=[dict(text="abc", box=[1, 1, 9, 9])]), "dimension_texts[0].text"),
    (dict(door_boxes=[1, 2]), "door_boxes"),
    (dict(door_overrides=dict(D1=dict(closed_deg=0))), "needs a 'note'"),
    (dict(verify=dict(tolerance_px=-1)), "tolerance_px"),
    (dict(verify=dict(grid=[0, 3])), "grid"),
    (dict(review_notes="text"), "review_notes"),
    (dict(open_questions=[1]), "open_questions"),
])
def test_invalid_values_have_clear_messages(tmp_path, over, needle):
    assert needle in _problems(tmp_path, **over)


def test_all_problems_reported_at_once(tmp_path):
    msg = _problems(tmp_path, engine="zzz", units="x", ocr="y", outputs=["shp"])
    assert msg.count("|") >= 3


def test_ignored_fields_warn(tmp_path):
    from PIL import Image
    Image.new("RGB", (400, 300), "white").save(tmp_path / "a.png")
    cfg = config_from_dict(dict(name="t", engine="dimension_sheet", source="a.png", units="mm"), base_dir=tmp_path)
    assert any("'units' is ignored" in w for w in cfg.warnings)


def test_unquoted_yaml_off_is_accepted(tmp_path):
    """`ocr: off` (unquoted) is the boolean False in YAML 1.1 - users type it naturally, so it must work."""
    from PIL import Image
    Image.new("RGB", (400, 300), "white").save(tmp_path / "a.png")
    c = write(tmp_path / "c.yaml", "name: t\nengine: auto_lines\nsource: a.png\nocr: off\n"
              "calibration: {x: {px: [10, 390], length: \"60'\"}, y: {px: [290, 10], length: \"40'\"}}\n")
    assert load_config(c).ocr == "off"
    c2 = write(tmp_path / "c2.yaml", "name: t\nengine: auto_lines\nsource: a.png\nocr: true\n"
               "calibration: {x: {px: [10, 390], length: \"60'\"}, y: {px: [290, 10], length: \"40'\"}}\n")
    with pytest.raises(ConfigError, match="ocr: must be one of"):
        load_config(c2)


def test_json_schema_matches_validator():
    """docs/config.schema.json drives VS Code autocompletion; it must accept every shipped config and reject typos."""
    import json

    import jsonschema
    import yaml
    schema = json.loads((ROOT / "docs" / "config.schema.json").read_text())
    for p in [*CONFIGS.values(), ROOT / "configs" / "examples" / "drawing3_minimal_auto.yaml"]:
        jsonschema.validate(yaml.safe_load(p.read_text()), schema)
    bad = yaml.safe_load(CONFIGS["drawing3_two_unit_ftin"].read_text())
    bad["textz"] = []
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
    bad = yaml.safe_load(CONFIGS["drawing3_two_unit_ftin"].read_text())
    bad["engine"] = "nope"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
