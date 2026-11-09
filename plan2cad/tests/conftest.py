import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))
os.environ["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")

CONFIGS = {
    "drawing1_two_floor_sketch": ROOT / "configs" / "drawing1_two_floor_sketch.yaml",
    "drawing2_ground_floor_1to50": ROOT / "configs" / "drawing2_ground_floor_1to50.yaml",
    "drawing3_two_unit_ftin": ROOT / "configs" / "drawing3_two_unit_ftin.yaml",
}
GOLDEN = {"drawing1_two_floor_sketch": "drawing1", "drawing2_ground_floor_1to50": "drawing2",
          "drawing3_two_unit_ftin": "drawing3"}


@pytest.fixture(scope="session")
def root():
    return ROOT


@pytest.fixture(scope="session")
def batch(tmp_path_factory):
    """Run the three shipped drawings once through the real pipeline; reused by many tests."""
    from plan2cad.pipeline import run_many
    out = tmp_path_factory.mktemp("batch")
    results = run_many(list(CONFIGS.values()), out)
    return out, {r.name: r for r in results}


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path
