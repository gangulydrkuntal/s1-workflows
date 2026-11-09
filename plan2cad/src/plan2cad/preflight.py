"""Environment check (`plan2cad doctor`): Python, required libraries, optional OCR, and a real write/read smoke test."""
from __future__ import annotations

import importlib
import importlib.metadata as md
import platform
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REQUIRED = [("numpy", "numpy"), ("PIL", "Pillow"), ("yaml", "PyYAML"), ("shapely", "shapely"), ("geopandas", "geopandas"),
            ("pyogrio", "pyogrio"), ("ezdxf", "ezdxf"), ("matplotlib", "matplotlib"), ("cv2", "opencv-python-headless")]
OPTIONAL = [("rapidocr_onnxruntime", "rapidocr-onnxruntime")]
MIN_PYTHON = (3, 10)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _ver(dist, mod):
    if getattr(mod, "__name__", "") == "cv2":          # two OpenCV distributions can coexist: report the one that loads
        return getattr(mod, "__version__", "?")
    for d in (dist, "opencv-python", "opencv-python-headless"):
        try:
            return md.version(d)
        except md.PackageNotFoundError:
            continue
    return getattr(mod, "__version__", "?")


def library_versions() -> dict:
    out = {"python": platform.python_version()}
    for mod, dist in REQUIRED + OPTIONAL:
        try:
            m = importlib.import_module(mod)
            out[dist] = _ver(dist, m)
        except Exception:
            out[dist] = "not installed"
    return out


def doctor(smoke: bool = True) -> list:
    checks = [Check("python", sys.version_info >= MIN_PYTHON,
                    f"{platform.python_version()} on {platform.system()} (need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})")]
    for mod, dist in REQUIRED:
        try:
            m = importlib.import_module(mod)
            checks.append(Check(dist, True, _ver(dist, m)))
        except Exception as e:
            checks.append(Check(dist, False, f"import failed: {type(e).__name__}: {e}  ->  pip install -r requirements.txt"))
    for mod, dist in OPTIONAL:
        try:
            importlib.import_module(mod)
            checks.append(Check(f"{dist} (OCR)", True, "installed", required=False))
        except Exception:
            checks.append(Check(f"{dist} (OCR)", False, "not installed - optional; only needed for ocr: auto  ->  pip install -e .[ocr]",
                                required=False))
    if smoke and all(c.ok for c in checks if c.required):
        checks.append(_smoke())
    return checks


def _smoke() -> Check:
    """Write and read back a tiny GeoPackage and DXF - proves the GIS/CAD stack really works on this machine."""
    try:
        import ezdxf
        import geopandas as gpd
        from ezdxf import recover
        from shapely.geometry import Point, Polygon
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.gpkg"
            gpd.GeoDataFrame({"a": [1]}, geometry=[Polygon([(0, 0), (1, 0), (1, 1)])], crs=None).to_file(p, layer="x", driver="GPKG")
            gpd.GeoDataFrame({"a": [1]}, geometry=[Point(0, 0)], crs=None).to_file(p, layer="y", driver="GPKG", mode="a")
            n = len(gpd.read_file(p, layer="x")) + len(gpd.read_file(p, layer="y"))
            q = Path(d) / "t.dxf"
            doc = ezdxf.new("R2010")
            doc.modelspace().add_line((0, 0), (1, 1))
            doc.saveas(q)
            _, aud = recover.readfile(str(q))
            if n != 2 or aud.has_errors:
                return Check("smoke test", False, "GeoPackage/DXF round trip failed")
        return Check("smoke test", True, "GeoPackage + DXF write/read round trip OK")
    except Exception as e:
        return Check("smoke test", False, f"{type(e).__name__}: {e}")
