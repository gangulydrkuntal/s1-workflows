"""Engine: dimensioned sheet (hand-modelled profile) -> GeoPackage. Wraps the exact exercise script."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

from ..errors import EngineError
from ..models import EngineResult, PixelFrame
from . import LEGACY_LOCK, patched
from .profile_common import check_profile_source

EXPECTED_SIZE = (1600, 1079)
EXPECTED_SHA = "0edaad88b306"


def run(cfg, out_dir: Path, log) -> EngineResult:
    from .legacy import dimension_sheet as m

    flags = check_profile_source(cfg, EXPECTED_SIZE, EXPECTED_SHA)
    gpkg_path = Path(out_dir) / f"{cfg.name}.gpkg"
    buf = io.StringIO()
    try:
        with LEGACY_LOCK, contextlib.redirect_stdout(buf), patched(m, SRC=Path(cfg.source), GPKG=gpkg_path):
            m.build()
            sx, x0, sy, y0 = m.SX, m.X0, m.SY, m.Y0
    except Exception as e:
        raise EngineError(f"dimension_sheet engine failed: {type(e).__name__}: {e}") from e
    res = EngineResult(gpkg=gpkg_path, flags=flags, log_lines=[ln for ln in buf.getvalue().splitlines() if ln.strip()])
    res.frame = PixelFrame(lambda x, y: (x0 + x * sx, y0 - y * sy))
    return res
