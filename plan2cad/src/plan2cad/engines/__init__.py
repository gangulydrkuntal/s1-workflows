"""Conversion engines. Each engine module exposes ``run(cfg, out_dir, log) -> EngineResult``.

* ``auto_lines``      - automatic: straight-line extraction, wall pairing, door-arc fitting, room flood-fill.
* ``dimension_sheet`` - profile: hand-modelled from dimension strings (reproduces the Griffith 1:50 exercise).
* ``two_floor_sheet`` - profile: hand-modelled two-floor sketch, DXF + GeoPackage (reproduces exercise 1).

The "legacy" modules are the exact scripts that produced the accepted exercise outputs. They keep module-level
settings, so access is serialised with LEGACY_LOCK and every engine restores what it changes.
"""
from __future__ import annotations

import contextlib
import importlib
import threading

LEGACY_LOCK = threading.RLock()
_REG = {"auto_lines": ".auto_lines", "dimension_sheet": ".dimension_sheet", "two_floor_sheet": ".two_floor_sheet"}


def get_engine(name: str):
    if name not in _REG:
        raise KeyError(f"unknown engine {name!r}; available: {sorted(_REG)}")
    return importlib.import_module(_REG[name], __package__).run


@contextlib.contextmanager
def patched(module, **attrs):
    """Temporarily set module-level attributes of a legacy module; always restore them."""
    saved = {k: getattr(module, k) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(module, k, v)
        yield module
    finally:
        for k, v in saved.items():
            setattr(module, k, v)
