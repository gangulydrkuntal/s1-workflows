"""Read-back checks of the written outputs and extraction of review flags from their content."""
from __future__ import annotations

import sqlite3
import warnings

import geopandas as gpd
import pyogrio

from .models import Flag

REQUIRED_LAYERS = {
    "auto_lines": {"rooms", "wall_fill", "walls"},
    "dimension_sheet": {"rooms", "wall_fill", "walls", "windows", "window_lines", "doors", "stairs", "symbols",
                        "dimensions", "annotations", "sheet"},
    "two_floor_sheet": {"rooms", "wall_fill", "wall_hatch", "walls", "glazing", "doors", "stairs", "annotations"},
}


def read_layer(path, layer):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return gpd.read_file(path, layer=layer)


def layer_names(path) -> list:
    return [r[0] for r in pyogrio.list_layers(path) if r[0] != "layer_styles"]


def check_gpkg(path, engine):
    """Return (feature counts per layer, flags). Errors: unreadable file, missing layers, invalid/empty geometry."""
    flags, counts = [], {}
    try:
        names = layer_names(path)
    except Exception as e:
        return counts, [Flag("error", "GPKG_UNREADABLE", f"GeoPackage cannot be read back: {e}", str(path))]
    missing = sorted(REQUIRED_LAYERS.get(engine, set()) - set(names))
    if missing:
        flags.append(Flag("error", "LAYERS_MISSING", f"required layer(s) missing: {missing}", str(path)))
    for lay in names:
        try:
            gdf = read_layer(path, lay)
        except Exception as e:
            flags.append(Flag("error", "LAYER_UNREADABLE", f"layer '{lay}' cannot be read: {e}", lay))
            continue
        counts[lay] = int(len(gdf))
        if len(gdf) == 0:
            flags.append(Flag("error", "LAYER_EMPTY", f"layer '{lay}' has no features", lay))
            continue
        nulls = int(gdf.geometry.isna().sum() + gdf.geometry.is_empty.sum())
        if nulls:
            flags.append(Flag("error", "GEOMETRY_EMPTY", f"layer '{lay}': {nulls} empty/null geometr(ies)", lay))
        bad = int((~gdf.geometry.dropna().is_valid).sum())
        if bad:
            flags.append(Flag("error", "GEOMETRY_INVALID", f"layer '{lay}': {bad} invalid geometr(ies)", lay))
    try:
        con = sqlite3.connect(path)
        has = con.execute("select count(*) from sqlite_master where name='layer_styles'").fetchone()[0]
        con.close()
        if not has:
            flags.append(Flag("info", "NO_QGIS_STYLES", "no QGIS default styles stored in the GeoPackage"))
    except Exception:
        pass
    return counts, flags


def check_dxf(path):
    """Audit the DXF with ezdxf (must report 0 errors). Returns (entity count, flags)."""
    from ezdxf import recover
    try:
        doc, auditor = recover.readfile(str(path))
    except Exception as e:
        return 0, [Flag("error", "DXF_UNREADABLE", f"DXF cannot be read back: {e}", str(path))]
    flags = []
    if auditor.has_errors:
        flags.append(Flag("error", "DXF_AUDIT", f"DXF audit found {len(auditor.errors)} error(s): "
                          f"{[str(e.message) for e in auditor.errors[:3]]}", str(path)))
    n = len(doc.modelspace())
    if n == 0:
        flags.append(Flag("error", "DXF_EMPTY", "DXF model space is empty", str(path)))
    return n, flags


def content_flags(path) -> list:
    """Review items recorded *inside* the data: UNVERIFIED status, dimension labels without/with mismatching lines."""
    flags = []
    for lay in layer_names(path):
        try:
            gdf = read_layer(path, lay)
        except Exception:
            continue
        if "status" in gdf.columns:
            unv = gdf[gdf["status"].astype(str).str.startswith("UNVERIFIED")]
            seen = set()
            for _, r in unv.iterrows():                    # a door has several rows (leaf, arc, ...): flag it once
                ident = r.get("door_id") or r.get("room_id") or r.get("window_id") or lay
                if (ident, r["status"]) in seen:
                    continue
                seen.add((ident, r["status"]))
                flags.append(Flag("review", "UNVERIFIED", f"{lay}: {r['status']}", str(ident)))
        if lay == "annotations" and "dim_line" in gdf.columns:
            d = gdf[gdf.get("kind", "") == "dimension"]
            differs = d[d["dim_line"].astype(str).str.contains("differs|no reliable", regex=True)]
            for _, r in differs.iterrows():
                flags.append(Flag("review", "DIM_MISMATCH", f"dimension {r['text']}: {r['dim_line']}", str(r["text"])))
            none = d[d["dim_line"].astype(str).str.contains("no measurable")]
            if len(none):
                flags.append(Flag("info", "DIM_NO_LINE", f"{len(none)} dimension label(s) have no measurable line "
                                  f"(too faint / not drawn): {sorted(set(none['text']))}", lay))
        if lay == "rooms" and "name" in gdf.columns:
            un = gdf[gdf["name"].astype(str).str.contains("nlabelled")]
            if len(un):
                flags.append(Flag("review", "UNLABELLED_SPACES", f"{len(un)} space(s) carry no label on the drawing "
                                  f"(use not stated): {', '.join(un['room_id'])}", lay))
        if lay == "other_lines" and "part" in gdf.columns:
            ns = gdf[gdf["part"].astype(str).str.contains("meaning not stated")]
            if len(ns):
                flags.append(Flag("review", "LINES_MEANING_UNKNOWN", f"{len(ns)} coloured line(s) whose meaning the drawing "
                                  f"does not state (kept as drawn)", lay))
    return flags
