import numpy as np
import pytest
from conftest import CONFIGS, ROOT

from plan2cad import ocr
from plan2cad.config import load_config

needs_ocr = pytest.mark.skipif(not ocr.available(), reason="rapidocr-onnxruntime not installed (pip install -e .[ocr])")


def test_classify_and_proposals_without_engine():
    W = ocr.Word
    words = [W("Bedroom", 0.9, (10, 10, 60, 20), 0), W("T1", 0.6, (5, 5, 15, 12), 0), W("140'", 0.6, (1, 1, 9, 5), 0),
             W("29'5", 0.5, (1, 9, 9, 15), 0), W("---", 0.5, (1, 1, 2, 2), 0)]
    labels, dims = ocr.classify(words)
    assert [w.text for w in labels] == ["Bedroom", "T1"] and [w.text for w in dims] == ["140'", "29'5", "---"][:2] + []
    p = ocr.proposals_dict(words)
    assert len(p["texts"]) == 2 and len(p["dimension_candidates"]) == 2 and "NOT adopted" in p["note"] or "were NOT" in p["note"]
    assert p["dimension_candidates"][0]["ocr_raw"] == "140'"           # raw OCR string kept verbatim, never "fixed"


def test_back_mapping_roundtrip():
    h, w = 20, 40
    for rot in (0, 90, 270):
        # a point in the original: (x, y); rotate the array and map back
        arr = np.zeros((h, w), np.uint8)
        arr[5, 30] = 1
        k = {0: 0, 90: -1, 270: 1}[rot]
        r = np.rot90(arr, k=k)
        yb, xb = np.argwhere(r == 1)[0]
        assert tuple(int(v) for v in ocr._back(rot, xb, yb, h, w)) == (30, 5)


@needs_ocr
def test_ocr_recovers_most_configured_room_labels():
    cfg = load_config(CONFIGS["drawing3_two_unit_ftin"])
    words = ocr.read_words(cfg.source)
    labels, _ = ocr.classify(words)
    hit = 0
    for t in cfg.texts:
        for w in labels:
            if w.text.lower() == t["text"].lower() and ocr._iou(tuple(t["box"]), w.box) > 0.3:
                hit += 1
                break
    assert hit / len(cfg.texts) >= 0.75, f"OCR recovered only {hit}/{len(cfg.texts)} room labels"


@needs_ocr
def test_ocr_mode_adopts_labels_but_never_dimensions(tmp_path):
    import yaml
    from conftest import write

    from plan2cad.pipeline import run_config
    d = yaml.safe_load((ROOT / "configs" / "examples" / "drawing3_minimal_auto.yaml").read_text())
    d["source"] = str(ROOT / "inputs" / "drawing3_two_unit_ftin.png")
    r = run_config(write(tmp_path / "m.yaml", yaml.safe_dump(d)), tmp_path / "o")
    assert r.status == "REVIEW" and {"OCR_LABELS", "OCR_DIMENSIONS"} <= {f.code for f in r.flags}
    assert "texts_proposed" in r.outputs
    prop = yaml.safe_load(open(r.outputs["texts_proposed"]))
    assert prop["dimension_candidates"] and "ocr_raw" in prop["dimension_candidates"][0]
    from plan2cad.check import read_layer
    assert not (read_layer(r.outputs["gpkg"], "annotations")["kind"] == "dimension").any()     # no dimension adopted


def test_automatic_door_detection_recall():
    import cv2

    import plan2cad.engines.legacy.auto_lines_core as core
    from plan2cad.engines import patched
    from plan2cad.engines.doors import detect_door_boxes
    cfg = load_config(CONFIGS["drawing3_two_unit_ftin"])
    tb = [tuple(t["box"]) for t in cfg.texts] + [tuple(t["box"]) for t in cfg.dimension_texts]
    with patched(core, SRC=cfg.source):
        auto = detect_door_boxes(core, tb)
        im = core.load()
        lm = np.zeros_like(im["ink"])
        for s in core.segments(im["ink"], "ink"):
            (ax, ay), (bx, by) = core.seg_line(s).coords
            cv2.line(lm, (int(round(ax)), int(round(ay))), (int(round(bx)), int(round(by))), 1, s["t"] + 2)
        fit = lambda b: core.fit_door(im["ink_light"], lm, tuple(b))[:3]          # noqa: E731
        hand = {k: fit(v) for k, v in cfg.door_boxes.items()}
        found = {k: fit(v) for k, v in auto.items()}
    matched = sum(any(abs(a[0] - h[0]) < 2.5 and abs(a[1] - h[1]) < 2.5 and abs(a[2] - h[2]) < 2.5 for a in found.values())
                  for h in hand.values())
    # measured on this drawing: 11 of 15 hand-fitted doors recovered with 2 extra detections; guard against regression
    assert matched >= 10, f"only {matched}/15 doors recovered"
    assert len(found) - matched <= 3
