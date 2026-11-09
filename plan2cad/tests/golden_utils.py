"""Compare two GeoPackages row by row (geometry to 1e-6 m + every attribute), independent of row order."""
from __future__ import annotations

import warnings
from collections import Counter

import geopandas as gpd
import pyogrio
import shapely


def layers(path):
    return [r[0] for r in pyogrio.list_layers(path) if r[0] != "layer_styles"]


def _rows(path, layer):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gdf = gpd.read_file(path, layer=layer)
    cols = [c for c in gdf.columns if c != "geometry"]
    out = []
    for _, r in gdf.iterrows():
        g = r.geometry
        wkt = shapely.to_wkt(g, rounding_precision=6) if g is not None else "NONE"
        out.append((wkt, tuple(str(r[c]) for c in cols)))
    return cols, out


def compare_gpkg(actual, expected) -> list:
    """Return a list of human-readable differences (empty list = identical)."""
    diffs = []
    la, le = layers(actual), layers(expected)
    if sorted(la) != sorted(le):
        return [f"layer sets differ: only in actual {sorted(set(la) - set(le))}, only in expected {sorted(set(le) - set(la))}"]
    for lay in sorted(le):
        ca, ra = _rows(actual, lay)
        ce, re_ = _rows(expected, lay)
        if sorted(ca) != sorted(ce):
            diffs.append(f"{lay}: columns differ {sorted(set(ca) ^ set(ce))}")
            continue
        if len(ra) != len(re_):
            diffs.append(f"{lay}: {len(ra)} rows, expected {len(re_)}")
            continue
        order = [ca.index(c) for c in ce]                    # align column order
        ra = [(w, tuple(t[i] for i in order)) for w, t in ra]
        extra = Counter(ra) - Counter(re_)
        missing = Counter(re_) - Counter(ra)
        if extra or missing:
            diffs.append(f"{lay}: {sum(extra.values())} row(s) differ (e.g. {next(iter(extra))[0][:90] if extra else ''})")
    return diffs
