"""Logging setup: console + optional per-drawing file."""
from __future__ import annotations

import logging
import sys

LOGGER = "plan2cad"


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER if name is None else f"{LOGGER}.{name}")


def setup_console(level: str = "INFO") -> None:
    root = logging.getLogger(LOGGER)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not any(getattr(h, "_p2c_console", False) for h in root.handlers):
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        h._p2c_console = True
        root.addHandler(h)
    root.propagate = False


def attach_file(path) -> logging.Handler:
    root = logging.getLogger(LOGGER)
    if root.level == logging.NOTSET or root.level > logging.INFO:   # library use: make sure run.log is not empty
        root.setLevel(logging.INFO)
    h = logging.FileHandler(path, mode="w", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    logging.getLogger(LOGGER).addHandler(h)
    return h


def detach(handler: logging.Handler) -> None:
    logging.getLogger(LOGGER).removeHandler(handler)
    handler.close()
