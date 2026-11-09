"""Preview image rendered from the GeoPackage layers themselves (what a GIS user will see)."""
from __future__ import annotations

import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .check import layer_names, read_layer  # noqa: E402


def render_preview(gpkg, png, dpi=80):
    names = layer_names(gpkg)
    L = {n: read_layer(gpkg, n) for n in names}
    fig, ax = plt.subplots(figsize=(20, 14))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if "rooms" in L:
            L["rooms"].plot(ax=ax, color="#f3f6ff", edgecolor="#c8c8dc", linewidth=0.3)
        for n, kw in (("wall_fill", dict(color="#bdbdbd", edgecolor="#555", linewidth=0.4)),
                      ("wall_hatch", dict(color="#777", edgecolor="#333", linewidth=0.4)),
                      ("windows", dict(facecolor="white", edgecolor="k", linewidth=0.4))):
            if n in L and len(L[n]):
                L[n].plot(ax=ax, **kw)
        for n, kw in (("walls", dict(color="k", linewidth=0.5)), ("glazing", dict(color="b", linewidth=0.6, linestyle="--")),
                      ("window_lines", dict(color="k", linewidth=0.4)), ("doors", dict(color="k", linewidth=0.6)),
                      ("stairs", dict(color="#963c5a", linewidth=0.9)), ("symbols", dict(color="k", linewidth=0.5)),
                      ("dimensions", dict(color="#888", linewidth=0.4)), ("other_lines", dict(color="#3c8c3c", linewidth=0.8)),
                      ("sheet", dict(color="k", linewidth=0.8))):
            if n in L and len(L[n]):
                L[n].plot(ax=ax, **kw)
        if "annotations" in L:
            for _, a in L["annotations"].iterrows():
                h = float(a.get("height_m", 0.2) or 0.2)
                ax.text(a.geometry.x, a.geometry.y, str(a["text"]), rotation=float(a.get("rotation", 0) or 0),
                        ha="center", va="center", fontsize=max(h * 40, 4))
    ax.set_aspect("equal")
    ax.axis("off")
    fig.savefig(png, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
