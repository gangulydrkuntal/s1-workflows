"""Plain data classes shared by the pipeline stages."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

LEVELS = ("error", "review", "info")


@dataclass
class Flag:
    """Something a person should look at. `error` fails the drawing, `review` needs a decision, `info` is FYI."""
    level: str
    code: str
    message: str
    where: str = ""

    def to_dict(self):
        return dict(level=self.level, code=self.code, message=self.message, where=self.where)


@dataclass
class PixelFrame:
    """Maps output (metre) coordinates back to *source image pixels* so results can be checked against the source."""
    to_px: Callable[[float, float], tuple]


@dataclass
class EngineResult:
    gpkg: Optional[Path] = None
    dxf: Optional[Path] = None
    extra_files: dict = field(default_factory=dict)
    frame: Optional[PixelFrame] = None
    flags: list = field(default_factory=list)
    log_lines: list = field(default_factory=list)


@dataclass
class StageRecord:
    name: str
    status: str  # ok | skipped | failed
    seconds: float
    message: str = ""

    def to_dict(self):
        return dict(name=self.name, status=self.status, seconds=round(self.seconds, 3), message=self.message)
