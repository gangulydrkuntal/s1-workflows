"""Engine: two-floor sketch (hand-modelled profile) -> DXF + GeoPackage. Wraps the exact exercise scripts."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

from ..errors import EngineError
from ..models import EngineResult, PixelFrame
from . import LEGACY_LOCK, patched
from .profile_common import check_profile_source

EXPECTED_SIZE = (4080, 1884)
EXPECTED_SHA = "d9fd9b60a853"
MODEL_WIDTH = 2000.0   # the profile's pixel constants refer to the sketch viewed at 2000 px width


def run(cfg, out_dir: Path, log) -> EngineResult:
    from PIL import Image

    from .legacy import two_floor_dxf as dxfm
    from .legacy import two_floor_gpkg as gp

    flags = check_profile_source(cfg, EXPECTED_SIZE, EXPECTED_SHA)
    out_dir = Path(out_dir)
    dxf_path = out_dir / f"{cfg.name}.dxf"
    gpkg_path = out_dir / f"{cfg.name}.gpkg"
    res = EngineResult(flags=flags)
    buf = io.StringIO()
    try:
        with LEGACY_LOCK, contextlib.redirect_stdout(buf), patched(dxfm, OUT=dxf_path), \
                patched(gp, DXF=dxf_path, GPKG=gpkg_path):
            doc = dxfm.build()
            dxfm.report()
            dxfm.preview(doc)                      # writes <dxf folder>/floor_plan_preview.png
            prev = dxf_path.with_name("floor_plan_preview.png")
            if prev.exists():
                target = out_dir / f"{cfg.name}_dxf_preview.png"
                prev.replace(target)
                res.extra_files["dxf_preview"] = target
            if "gpkg" in cfg.outputs:
                gp.main()
                res.gpkg = gpkg_path
            res.dxf = dxf_path
    except Exception as e:  # legacy code raises assorted errors; surface them as one clear type
        raise EngineError(f"two_floor_sheet engine failed: {type(e).__name__}: {e}") from e
    finally:
        res.log_lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    if "dxf" not in cfg.outputs:
        dxf_path.unlink(missing_ok=True)           # DXF was only an intermediate for the GeoPackage
        res.dxf = None

    with Image.open(cfg.source) as im:
        k = im.width / MODEL_WIDTH
    ffdx = dxfm.FF_DX

    def to_px(x, y):                               # inverse of the profile's pixel -> metre mapping
        if x >= ffdx - 1:                          # first floor (drawn 14 m to the right on the sheet)
            return ((1125.8 + 46.18 * (x - ffdx)) * k, (879.9 - 45.36 * y) * k)
        return ((365.0 + 48.0 * x) * k, (905.0 - 48.0 * y) * k)

    res.frame = PixelFrame(to_px)
    return res
