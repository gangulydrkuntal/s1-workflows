"""Helpers shared by the two hand-modelled profile engines."""
from __future__ import annotations

import hashlib

from ..errors import IngestError
from ..models import Flag


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_profile_source(cfg, expected_size, expected_sha_prefix):
    """A profile's numbers are tied to the exact image it was modelled on."""
    from PIL import Image
    with Image.open(cfg.source) as im:
        size = im.size
    flags = []
    if expected_size and size != tuple(expected_size):
        raise IngestError(
            f"engine '{cfg.engine}' is a hand-modelled profile tied to a {expected_size[0]}x{expected_size[1]} image; "
            f"{cfg.source.name} is {size[0]}x{size[1]}. Use the original image, or use engine 'auto_lines' for new drawings.")
    if not sha256_of(cfg.source).startswith(expected_sha_prefix):
        flags.append(Flag("review", "SOURCE_DIFFERS",
                          "source image differs from the image this profile was modelled on (same size, different content); "
                          "check the overlay carefully", cfg.source.name))
    return flags
