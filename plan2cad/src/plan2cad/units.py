"""Length parsing for dimension strings (feet-inches, metres, millimetres)."""
from __future__ import annotations

import re

FT = 0.3048  # metres per foot
UNITS = ("ft-in", "m", "mm")

_QUOTES = str.maketrans({"’": "'", "′": "'", "‘": "'", "”": '"', "″": '"', "“": '"'})
_FTIN = re.compile(r"""^(?:(?P<ft>\d+(?:\.\d+)?)')?(?:-|\s)*(?:(?P<inch>\d+(?:\.\d+)?)(?:"|''))?$""")
_NUM = re.compile(r"^\s*(?P<v>\d+(?:[.,]\d+)?)\s*(?P<u>mm|m)?\s*$", re.I)


def normalise(text: str) -> str:
    """Unify typographic quotes so `14'7”` and `14'7"` parse the same."""
    return text.translate(_QUOTES).strip()


def parse_length_ft(text: str, units: str = "ft-in") -> float:
    """Parse a dimension string to *feet* (the engine's internal unit). Raises ValueError if unparseable."""
    t = normalise(str(text))
    if units == "ft-in":
        m = _FTIN.match(t)
        if not m or (m.group("ft") is None and m.group("inch") is None):
            raise ValueError(f"cannot parse feet-inches length {text!r}")
        inch = m.group("inch")
        return float(m.group("ft") or 0.0) + (float(inch) / 12 if inch is not None else 0.0)
    if units in ("m", "mm"):
        m = _NUM.match(t)
        if not m:
            raise ValueError(f"cannot parse {units} length {text!r}")
        v = float(m.group("v").replace(",", "."))
        u = (m.group("u") or units).lower()
        metres = v / 1000.0 if u == "mm" else v
        return metres / FT
    raise ValueError(f"unknown units {units!r}")


def parse_length_m(text: str, units: str = "ft-in") -> float:
    return parse_length_ft(text, units) * FT
