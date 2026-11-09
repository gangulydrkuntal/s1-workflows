"""Optional OCR (RapidOCR, pure pip install, no system binary) used to *propose* drawing text.

Policy (learned in the exercise): OCR reads room labels reliably but garbles tiny dimension strings
(``14'0"`` -> ``140'``). So room labels are adopted (and flagged for review) while dimension strings are only
*proposed* in ``texts_proposed.yaml`` for a person to confirm - a dimension value is never guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .errors import Plan2CadError

_ENGINE = None


class OcrUnavailable(Plan2CadError):
    """RapidOCR is not installed."""


@dataclass
class Word:
    text: str
    conf: float
    box: tuple          # x0, y0, x1, y1 in source pixels
    rotation: int       # 0, 90 (reads bottom-to-top) or 270 (reads top-to-bottom)


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _engine():
    global _ENGINE
    if _ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as e:
            raise OcrUnavailable("OCR requested but 'rapidocr-onnxruntime' is not installed. "
                                 "Run: pip install -e .[ocr]   (or set ocr: off in the config)") from e
        _ENGINE = RapidOCR()
    return _ENGINE


def _iou(a, b):
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _back(rot, xb, yb, hs, ws):
    """Map a point from a rotated+scaled image back to scaled-original coordinates."""
    if rot == 0:
        return xb, yb
    if rot == 90:      # pass used np.rot90(k=-1) (clockwise)
        return yb, hs - 1 - xb
    return ws - 1 - yb, xb   # rot 270: np.rot90(k=1) (counter-clockwise)


def read_words(path, scale: int = 3, min_conf: float = 0.3) -> list:
    """OCR the whole drawing in three passes (upright, and rotated both ways to catch vertical text)."""
    eng = _engine()
    arr = np.asarray(Image.open(path).convert("RGB"))
    big = np.asarray(Image.fromarray(arr).resize((arr.shape[1] * scale, arr.shape[0] * scale), Image.LANCZOS))
    hs, ws = big.shape[:2]
    cand = []
    for rot, k in ((0, 0), (90, -1), (270, 1)):
        img = np.ascontiguousarray(np.rot90(big, k=k)[:, :, ::-1])
        res, _ = eng(img)
        for poly, txt, conf in (res or []):
            conf = float(conf)
            txt = str(txt).strip()
            if conf < min_conf or not re.search(r"[A-Za-z0-9]", txt):
                continue
            pts = [_back(rot, p[0], p[1], hs, ws) for p in poly]
            x0, x1 = min(p[0] for p in pts) / scale, max(p[0] for p in pts) / scale
            y0, y1 = min(p[1] for p in pts) / scale, max(p[1] for p in pts) / scale
            w, h = x1 - x0, y1 - y0
            if rot == 0 and w < 0.9 * h:
                continue                      # tall box in the upright pass is vertical text; the rotated pass owns it
            if rot != 0 and h < 1.1 * w:
                continue
            cand.append(Word(txt, conf, (int(x0), int(y0), int(np.ceil(x1)), int(np.ceil(y1))), rot))
    words = []
    for c in sorted(cand, key=lambda w: -w.conf):
        if all(_iou(c.box, w.box) < 0.3 for w in words):
            words.append(c)
    return sorted(words, key=lambda w: (w.box[1], w.box[0]))


def classify(words):
    """Split into room labels (contain letters) and dimension candidates (digits only)."""
    labels, dims = [], []
    for w in words:
        t = w.text
        if re.search(r"[A-Za-z]{2,}", t) or re.fullmatch(r"[A-Za-z]\d+", t):
            labels.append(w)
        elif re.search(r"\d", t):
            dims.append(w)
    return labels, dims


def pad(box, p=1):
    return [max(0, box[0] - p), max(0, box[1] - p), box[2] + p, box[3] + p]


def proposals_dict(words) -> dict:
    labels, dims = classify(words)
    return dict(
        note="OCR proposals. Room labels were adopted for this run; dimension candidates were NOT (OCR often drops "
             "the inch marks). Correct and paste into the config under texts / dimension_texts to confirm.",
        texts=[dict(text=w.text, kind="room_label", box=pad(w.box), rotation=w.rotation, confidence=round(w.conf, 2))
               for w in labels],
        dimension_candidates=[dict(ocr_raw=w.text, box=pad(w.box), rotation=w.rotation, confidence=round(w.conf, 2))
                              for w in dims],
    )
