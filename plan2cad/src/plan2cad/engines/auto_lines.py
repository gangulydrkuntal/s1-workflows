"""Engine: automatic extraction from pixels -> GeoPackage (the method used for the two-unit ft-in plan).

Pipeline inside the engine: colour split -> straight segments -> wall pairs -> door arcs (config boxes, or automatic
detection) -> dimension-line matching -> room flood fill -> snap to wall faces -> GeoPackage.
Everything numeric comes from the pixels and from the dimension strings given in the config (or confirmed OCR text).
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

from .. import ocr as ocrmod
from ..errors import EngineError
from ..models import EngineResult, Flag, PixelFrame
from ..units import FT, parse_length_ft
from . import LEGACY_LOCK, patched
from .doors import detect_door_boxes


def _has_ink(gray, sat, box) -> bool:
    x0, y0, x1, y1 = box
    sub = (gray[y0:y1 + 1, x0:x1 + 1] < 170) & (sat[y0:y1 + 1, x0:x1 + 1] < 40)
    return bool(sub.any())


def _resolve_texts(cfg, out_dir, flags, extra):
    """Return (texts, dim_texts) as the legacy tuples. Config text wins; otherwise OCR proposals (if enabled)."""
    rgb = np.asarray(Image.open(cfg.source).convert("RGB")).astype(int)
    gray, sat = rgb.mean(2), rgb.max(2) - rgb.min(2)
    H, W = gray.shape
    texts, dims = [], []
    if cfg.texts or cfg.dimension_texts:
        for i, t in enumerate(cfg.texts):
            if t["box"][2] >= W or t["box"][3] >= H:
                raise EngineError(f"texts[{i}] '{t['text']}': box {t['box']} lies outside the {W}x{H} image")
            if not _has_ink(gray, sat, t["box"]):
                raise EngineError(f"texts[{i}] '{t['text']}': no text pixels inside box {t['box']} - check the box")
            texts.append((t["text"], t["kind"], tuple(t["box"]), t["rotation"]))
        for i, t in enumerate(cfg.dimension_texts):
            if t["box"][2] >= W or t["box"][3] >= H:
                raise EngineError(f"dimension_texts[{i}] '{t['text']}': box {t['box']} lies outside the {W}x{H} image")
            if not _has_ink(gray, sat, t["box"]):
                raise EngineError(f"dimension_texts[{i}] '{t['text']}': no text pixels inside box {t['box']} - check the box")
            dims.append((t["text"], tuple(t["box"]), t["rotation"]))
        return texts, dims, []
    if cfg.ocr == "off":
        flags.append(Flag("info", "NO_TEXT", "no texts configured and OCR is off: rooms stay unlabelled and no dimension "
                          "lines are matched. Add texts/dimension_texts to the config, or set ocr: auto"))
        return texts, dims, []
    if cfg.ocr == "auto" and not ocrmod.available():
        flags.append(Flag("info", "OCR_NOT_INSTALLED", "ocr: auto but rapidocr-onnxruntime is not installed "
                          "(pip install -e .[ocr]); continuing without text"))
        return texts, dims, []
    words = ocrmod.read_words(cfg.source)            # raises OcrUnavailable if ocr: rapidocr and not installed
    labels, dcand = ocrmod.classify(words)
    prop = ocrmod.proposals_dict(words)
    prop_path = Path(out_dir) / f"{cfg.name}_texts_proposed.yaml"
    prop_path.write_text(yaml.safe_dump(prop, sort_keys=False, default_flow_style=None, allow_unicode=True), encoding="utf-8")
    extra["texts_proposed"] = prop_path
    for w in labels:
        b = ocrmod.pad(w.box)
        if b[2] < W and b[3] < H and _has_ink(gray, sat, b):
            texts.append((w.text, "room_label", tuple(b), w.rotation))
    extra_boxes = [tuple(ocrmod.pad(w.box)) for w in dcand
                   if ocrmod.pad(w.box)[2] < W and ocrmod.pad(w.box)[3] < H]
    flags.append(Flag("review", "OCR_LABELS", f"{len(texts)} room label(s) were read by OCR and adopted - verify spelling "
                      f"and positions", prop_path.name))
    flags.append(Flag("review", "OCR_DIMENSIONS", f"{len(dcand)} dimension candidate(s) found by OCR were NOT adopted "
                      f"(values must be confirmed by a person); see {prop_path.name}. Until they are confirmed in the config, "
                      f"their dimension lines cannot be recognised and may split rooms into fragments", prop_path.name))
    return texts, dims, extra_boxes


def run(cfg, out_dir: Path, log) -> EngineResult:
    from .legacy import auto_lines_core as core

    flags: list = []
    extra: dict = {}
    texts, dims, extra_boxes = _resolve_texts(cfg, out_dir, flags, extra)

    cal = cfg.calibration
    units = cfg.units
    lx = parse_length_ft(cal["x"]["length"], units)
    ly = parse_length_ft(cal["y"]["length"], units)
    (x0p, x1p), (y0p, y1p) = cal["x"]["px"], cal["y"]["px"]
    sx, sy = abs(x1p - x0p) / lx, abs(y1p - y0p) / ly          # px per foot
    px0, py0 = float(x0p), float(y0p)
    if abs(sx / sy - 1) > 0.03:
        flags.append(Flag("review", "SCALE_ANISOTROPY", f"x scale {sx:.3f} and y scale {sy:.3f} px/ft differ by "
                          f"{abs(sx / sy - 1):.1%}; the image may be stretched or skewed", "calibration"))

    all_text_boxes = [t[2] for t in texts] + [d[1] for d in dims] + list(extra_boxes)
    if cfg.door_boxes == "auto":
        with LEGACY_LOCK, patched(core, SRC=Path(cfg.source), INK_THR=int(cfg.params.get("ink_threshold", 225)),
                                  SEG_MIN=int(cfg.params.get("min_segment_px", 10))):
            door_boxes = detect_door_boxes(core, all_text_boxes)
        log.info("automatic door detection found %d door arc(s)", len(door_boxes))
        if not door_boxes:
            flags.append(Flag("review", "NO_DOORS", "automatic door detection found no door arcs"))
        elif cfg.door_overrides:
            flags.append(Flag("info", "OVERRIDES_UNUSED", "door_overrides are keyed by door id; ids from automatic "
                              "detection differ, so overrides are not applied", "door_overrides"))
    else:
        door_boxes = {k: tuple(v) for k, v in cfg.door_boxes.items()}
    overrides = cfg.door_overrides if cfg.door_boxes != "auto" else {}
    for k in overrides:
        if k not in door_boxes:
            flags.append(Flag("review", "OVERRIDE_UNKNOWN_DOOR", f"door_overrides.{k} refers to a door not in door_boxes", k))

    gp = Path(out_dir) / f"{cfg.name}.gpkg"
    wg = cfg.params.get("wall_gap_px", [3.5, 7.5])
    buf = io.StringIO()
    try:
        with LEGACY_LOCK, contextlib.redirect_stdout(buf), patched(
                core, SRC=Path(cfg.source), GPKG=gp, SX=sx, SY=sy, PX0=px0, PY0=py0,
                TEXTS=texts, DIM_TEXTS=dims, DOOR_BOXES=door_boxes, DOOR_OVERRIDES=overrides,
                WALL_GAP=(float(wg[0]), float(wg[1])), SEG_MIN=int(cfg.params.get("min_segment_px", 10)),
                INK_THR=int(cfg.params.get("ink_threshold", 225)), EXTRA_TEXT_BOXES=list(extra_boxes),
                ft_in=lambda v: parse_length_ft(v, units)):
            R = core.build()
            L = core.write(R)
    except EngineError:
        raise
    except Exception as e:
        raise EngineError(
            f"automatic extraction failed ({type(e).__name__}: {e}). The drawing is probably not a thin double-line "
            f"CAD plan, or the calibration / thresholds do not fit it. Check calibration and params in the config.") from e
    if not L["rooms"]:
        raise EngineError("no rooms were detected (walls found: %d). Check params.wall_gap_px (spacing of the two lines "
                          "of a wall, in pixels) and the calibration." % len(R["walls"]))
    # plausibility: how much of the long straight line work was recognised as double-line walls?
    # (proven drawing: 75 %; unsuitable inputs measured: 18 %)
    def _len(sg):
        return (sg["x1"] - sg["x0"] + 1) if sg["o"] == "h" else (sg["y1"] - sg["y0"] + 1)
    long_ = [sg for sg in R["segs"] if sg["cls"] == "ink" and _len(sg) >= 40]
    paired = {w["a"] for w in R["walls"]} | {w["b"] for w in R["walls"]}
    share = sum(_len(sg) for sg in long_ if sg["id"] in paired) / max(sum(_len(sg) for sg in long_), 1)
    res_log = f"wall_line_share={share:.2f}"
    if share < 0.25:
        gp.unlink(missing_ok=True)
        raise EngineError(f"only {share:.0%} of the long straight lines were recognised as double-line walls (a good "
                          f"extraction has ~75%). This engine expects thin double-line walls: check params.wall_gap_px "
                          f"(pixels between the two lines of a wall) and the image, or use a hand-modelled profile.")
    if share < 0.40:
        flags.append(Flag("review", "WALLS_FEW", f"only {share:.0%} of the long straight lines were recognised as "
                          f"double-line walls (good extractions reach ~75%): the result may be unreliable", "params"))
    if units != "ft-in":
        flags.append(Flag("info", "FT_COLUMNS", f"units are {units}: attribute columns named *_ft hold feet-equivalents; "
                          f"use the *_m columns"))
    res = EngineResult(gpkg=gp, flags=flags, extra_files=extra,
                       log_lines=[ln for ln in buf.getvalue().splitlines() if ln.strip()])
    res.log_lines.append(f"walls={len(R['walls'])} doors={len(R['doors'])} rooms={len(L['rooms'])} {res_log}")
    res.frame = PixelFrame(lambda x, y: (px0 + x / FT * sx, py0 - y / FT * sy))
    return res
