"""Per-drawing YAML configuration: loading and strict validation.

Every problem found is reported at once (not just the first) with the field path, so a config can be fixed in
one pass. Unknown keys are errors (they are almost always typos).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .errors import ConfigError
from .units import UNITS, parse_length_ft

ENGINES = {
    # outputs: formats the engine can write; uses: optional config sections it reads
    "auto_lines": dict(outputs={"gpkg"}, uses={"units", "ocr", "calibration", "params", "texts",
                                                "dimension_texts", "door_boxes", "door_overrides"}),
    "dimension_sheet": dict(outputs={"gpkg"}, uses=set()),
    "two_floor_sheet": dict(outputs={"gpkg", "dxf"}, uses=set()),
}
TOP_KEYS = {"name", "engine", "source", "outputs", "review_notes", "open_questions", "verify", "units", "ocr", "calibration",
            "params", "texts", "dimension_texts", "door_boxes", "door_overrides"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
OCR_MODES = ("off", "auto", "rapidocr")
KINDS = ("room_label", "site_label")
PARAM_KEYS = {"wall_gap_px", "min_segment_px", "ink_threshold"}
VERIFY_DEFAULTS = dict(tolerance_px=3.0, min_share=0.85, grid=[3, 3], min_samples=40)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass
class Config:
    name: str
    engine: str
    source: Path
    outputs: list
    config_path: Path | None = None
    units: str = "ft-in"
    ocr: str = "off"
    calibration: dict | None = None
    params: dict = field(default_factory=dict)
    texts: list = field(default_factory=list)
    dimension_texts: list = field(default_factory=list)
    door_boxes: object = "auto"
    door_overrides: dict = field(default_factory=dict)
    review_notes: list = field(default_factory=list)
    open_questions: list = field(default_factory=list)
    verify: dict = field(default_factory=lambda: dict(VERIFY_DEFAULTS))
    warnings: list = field(default_factory=list)

    def resolved_dict(self) -> dict:
        d = dict(name=self.name, engine=self.engine, source=str(self.source), outputs=self.outputs,
                 verify=self.verify, review_notes=self.review_notes, open_questions=self.open_questions)
        if self.engine == "auto_lines":
            d.update(units=self.units, ocr=self.ocr, calibration=self.calibration, params=self.params,
                     texts=self.texts, dimension_texts=self.dimension_texts, door_boxes=self.door_boxes,
                     door_overrides=self.door_overrides)
        return d


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_box(v, where, problems):
    if not (isinstance(v, (list, tuple)) and len(v) == 4 and all(_num(x) for x in v)):
        problems.append(f"{where}: must be a box [x0, y0, x1, y1] of 4 numbers (pixels), got {v!r}")
        return None
    x0, y0, x1, y1 = (int(round(x)) for x in v)
    if not (x0 < x1 and y0 < y1) or min(x0, y0) < 0:
        problems.append(f"{where}: box must satisfy 0 <= x0 < x1 and 0 <= y0 < y1, got {list(v)!r}")
        return None
    return [x0, y0, x1, y1]


def _suggest(key, valid):
    m = difflib.get_close_matches(key, sorted(valid), n=1)
    return f" (did you mean '{m[0]}'?)" if m else ""


def load_config(path) -> Config:
    """Load and validate a YAML config. Raises ConfigError listing every problem."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError([f"file not found: {path}"], path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError([f"not valid YAML: {str(e).splitlines()[0] if str(e) else e}"], path) from e
    if not isinstance(raw, dict):
        raise ConfigError(["top level must be a mapping of settings (name: value)"], path)
    return config_from_dict(raw, base_dir=path.parent, path=path)


def config_from_dict(raw: dict, base_dir: Path = Path("."), path: Path | None = None) -> Config:
    problems: list[str] = []
    warns: list[str] = []
    for k in raw:
        if k not in TOP_KEYS:
            problems.append(f"unknown field '{k}'{_suggest(k, TOP_KEYS)}")

    name = raw.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        problems.append("name: required; letters, digits, '_', '-', '.' only (used for output file names)")
        name = "invalid"
    engine = raw.get("engine")
    if engine not in ENGINES:
        problems.append(f"engine: must be one of {sorted(ENGINES)}, got {engine!r}")
        engine = None
    src = raw.get("source")
    source = None
    if not isinstance(src, str) or not src.strip():
        problems.append("source: required; path to the drawing image (relative to this config file)")
    else:
        source = (base_dir / src).resolve() if not Path(src).is_absolute() else Path(src)
        if source.suffix.lower() not in IMAGE_EXT:
            problems.append(f"source: unsupported image type '{source.suffix}' (use {sorted(IMAGE_EXT)})")
        elif not source.is_file():
            problems.append(f"source: image not found: {source}")

    outputs = raw.get("outputs", ["gpkg"])
    if not (isinstance(outputs, list) and outputs and all(isinstance(o, str) for o in outputs)):
        problems.append("outputs: must be a non-empty list such as [gpkg] or [gpkg, dxf]")
        outputs = ["gpkg"]
    else:
        bad = [o for o in outputs if o not in ("gpkg", "dxf")]
        if bad:
            problems.append(f"outputs: unknown format(s) {bad}; allowed: gpkg, dxf")
        if engine and not bad:
            na = [o for o in outputs if o not in ENGINES[engine]["outputs"]]
            if na:
                problems.append(f"outputs: engine '{engine}' cannot write {na}; it supports {sorted(ENGINES[engine]['outputs'])}")
        outputs = list(dict.fromkeys(outputs))

    uses = ENGINES[engine]["uses"] if engine else set()
    for k in ("units", "ocr", "calibration", "params", "texts", "dimension_texts", "door_boxes", "door_overrides"):
        if k in raw and engine and k not in uses:
            warns.append(f"'{k}' is ignored by engine '{engine}'")

    units = raw.get("units", "ft-in")
    if units not in UNITS:
        problems.append(f"units: must be one of {list(UNITS)}, got {units!r}")
        units = "ft-in"
    ocr = raw.get("ocr", "off")
    if ocr is False:                  # YAML 1.1 reads an unquoted `off` as the boolean False
        ocr = "off"
    if ocr not in OCR_MODES:
        problems.append(f"ocr: must be one of {list(OCR_MODES)}, got {ocr!r}")
        ocr = "off"

    calibration = None
    if engine == "auto_lines":
        cal = raw.get("calibration")
        if not isinstance(cal, dict) or set(cal) - {"x", "y"} or "x" not in cal or "y" not in cal:
            problems.append("calibration: required for engine 'auto_lines'; give x and y, e.g. "
                            "x: {px: [98, 886], length: \"60'\"}")
        else:
            calibration = {}
            for ax in ("x", "y"):
                c = cal[ax]
                if not (isinstance(c, dict) and set(c) == {"px", "length"}):
                    problems.append(f"calibration.{ax}: must be {{px: [p0, p1], length: \"...\"}}")
                    continue
                px = c["px"]
                if not (isinstance(px, list) and len(px) == 2 and all(_num(v) for v in px) and px[0] != px[1]):
                    problems.append(f"calibration.{ax}.px: two different pixel positions of the two ends of a known length")
                    continue
                try:
                    ln = parse_length_ft(str(c["length"]), units)
                    if ln <= 0:
                        raise ValueError("length must be > 0")
                except ValueError as e:
                    problems.append(f"calibration.{ax}.length: {e} (units: {units})")
                    continue
                if ax == "x" and not px[0] < px[1]:
                    problems.append("calibration.x.px: first value must be the LEFT end, second the right end")
                    continue
                if ax == "y" and not px[0] > px[1]:
                    problems.append("calibration.y.px: first value must be the BOTTOM end (larger pixel row), "
                                    "second the top end; the model's Y axis points up")
                    continue
                calibration[ax] = dict(px=[float(px[0]), float(px[1])], length=str(c["length"]))
            if set(calibration) != {"x", "y"}:
                calibration = None

    params = raw.get("params", {}) or {}
    if not isinstance(params, dict):
        problems.append("params: must be a mapping")
        params = {}
    for k, v in params.items():
        if k not in PARAM_KEYS:
            problems.append(f"params.{k}: unknown{_suggest(k, PARAM_KEYS)}")
        elif k == "wall_gap_px" and not (isinstance(v, list) and len(v) == 2 and all(_num(x) for x in v) and 0 < v[0] < v[1]):
            problems.append("params.wall_gap_px: must be [min, max] pixels with 0 < min < max")
        elif k == "min_segment_px" and not (isinstance(v, int) and not isinstance(v, bool) and v >= 3):
            problems.append("params.min_segment_px: integer >= 3")
        elif k == "ink_threshold" and not (isinstance(v, int) and not isinstance(v, bool) and 50 <= v <= 254):
            problems.append("params.ink_threshold: integer between 50 and 254")

    texts = []
    for i, t in enumerate(raw.get("texts", []) or []):
        w = f"texts[{i}]"
        if not isinstance(t, dict) or set(t) - {"text", "kind", "box", "rotation"} or not isinstance(t.get("text"), str) or not t["text"].strip():
            problems.append(f"{w}: needs text, box and optionally kind/rotation")
            continue
        kind = t.get("kind", "room_label")
        rot = t.get("rotation", 0)
        if kind not in KINDS:
            problems.append(f"{w}.kind: must be one of {list(KINDS)}")
        if rot not in (0, 90, 180, 270):
            problems.append(f"{w}.rotation: must be 0, 90, 180 or 270")
        bx = _check_box(t.get("box"), f"{w}.box", problems)
        if bx and kind in KINDS and rot in (0, 90, 180, 270):
            texts.append(dict(text=t["text"], kind=kind, box=bx, rotation=rot))

    dtexts = []
    for i, t in enumerate(raw.get("dimension_texts", []) or []):
        w = f"dimension_texts[{i}]"
        if not isinstance(t, dict) or set(t) - {"text", "box", "rotation"} or not isinstance(t.get("text"), str):
            problems.append(f"{w}: needs text, box and optionally rotation")
            continue
        rot = t.get("rotation", 0)
        if rot not in (0, 90, 180, 270):
            problems.append(f"{w}.rotation: must be 0, 90, 180 or 270")
        try:
            parse_length_ft(t["text"], units)
        except ValueError as e:
            problems.append(f"{w}.text: {e} (units: {units})")
        bx = _check_box(t.get("box"), f"{w}.box", problems)
        if bx and rot in (0, 90, 180, 270):
            dtexts.append(dict(text=t["text"], box=bx, rotation=rot))

    door_boxes = raw.get("door_boxes", "auto")
    if door_boxes != "auto":
        if not isinstance(door_boxes, dict):
            problems.append("door_boxes: must be 'auto' or a mapping id -> [x0, y0, x1, y1]")
            door_boxes = "auto"
        else:
            db = {}
            for k, v in door_boxes.items():
                bx = _check_box(v, f"door_boxes.{k}", problems)
                if bx:
                    db[str(k)] = bx
            door_boxes = db
    overrides = raw.get("door_overrides", {}) or {}
    if not isinstance(overrides, dict):
        problems.append("door_overrides: must be a mapping")
        overrides = {}
    for k, v in overrides.items():
        if not isinstance(v, dict) or not v.get("note"):
            problems.append(f"door_overrides.{k}: needs a 'note' explaining the visual decision (audit trail)")
        elif "closed_deg" not in v:
            problems.append(f"door_overrides.{k}: needs closed_deg")

    notes = raw.get("review_notes", []) or []
    if not (isinstance(notes, list) and all(isinstance(n, str) for n in notes)):
        problems.append("review_notes: must be a list of strings")
        notes = []

    questions = raw.get("open_questions", []) or []
    if not (isinstance(questions, list) and all(isinstance(n, str) for n in questions)):
        problems.append("open_questions: must be a list of strings (questions a person still has to answer)")
        questions = []

    verify = dict(VERIFY_DEFAULTS)
    vr = raw.get("verify", {}) or {}
    if not isinstance(vr, dict):
        problems.append("verify: must be a mapping")
        vr = {}
    for k, v in vr.items():
        if k not in VERIFY_DEFAULTS:
            problems.append(f"verify.{k}: unknown{_suggest(k, VERIFY_DEFAULTS)}")
        elif k == "tolerance_px" and not (_num(v) and v > 0):
            problems.append("verify.tolerance_px: number > 0")
        elif k == "min_share" and not (_num(v) and 0 < v <= 1):
            problems.append("verify.min_share: number in (0, 1]")
        elif k == "grid" and not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) and 1 <= x <= 8 for x in v)):
            problems.append("verify.grid: [rows, cols], each 1..8")
        elif k == "min_samples" and not (isinstance(v, int) and v >= 1):
            problems.append("verify.min_samples: integer >= 1")
        else:
            verify[k] = v

    if problems:
        raise ConfigError(problems, path)
    cfg = Config(name=name, engine=engine, source=source, outputs=outputs, config_path=path, units=units, ocr=ocr,
                 calibration=calibration, params=params, texts=texts, dimension_texts=dtexts,
                 door_boxes=door_boxes, door_overrides=overrides, review_notes=notes, open_questions=questions,
                 verify=verify, warnings=warns)
    return cfg
