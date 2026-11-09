"""plan2cad - floor-plan drawing (image) -> CAD (DXF) + GIS (GeoPackage) pipeline.

Public API (loaded lazily so that importing a sub-module stays cheap)::

    from plan2cad import load_config, run_config, run_many
"""
__version__ = "1.0.0"

_LAZY = {
    "Config": "config", "ConfigError": "errors", "load_config": "config",
    "DrawingResult": "pipeline", "run_config": "pipeline", "run_many": "pipeline",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib
        return getattr(importlib.import_module(f".{_LAZY[name]}", __name__), name)
    raise AttributeError(f"module 'plan2cad' has no attribute {name!r}")


__all__ = ["__version__", *_LAZY]
