"""The pipeline must reproduce the accepted exercise outputs exactly (geometry to 1e-6 m and every attribute)."""
import pytest
from conftest import CONFIGS, GOLDEN, ROOT
from golden_utils import compare_gpkg


@pytest.mark.parametrize("name", list(CONFIGS))
def test_gpkg_identical_to_accepted_output(batch, name):
    out, results = batch
    diffs = compare_gpkg(out / name / f"{name}.gpkg", ROOT / "tests" / "golden" / f"{GOLDEN[name]}.gpkg")
    assert diffs == []


def test_dxf_matches_accepted_output(batch):
    from ezdxf import recover
    out, _ = batch
    a, aud = recover.readfile(str(out / "drawing1_two_floor_sketch" / "drawing1_two_floor_sketch.dxf"))
    e, _ = recover.readfile(str(ROOT / "tests" / "golden" / "drawing1.dxf"))
    assert not aud.has_errors

    def census(doc):
        c = {}
        for ent in doc.modelspace():
            k = (ent.dxf.layer, ent.dxftype())
            c[k] = c.get(k, 0) + 1
        return c
    assert census(a) == census(e)
    assert sorted(n for n, _ in a.groups) == sorted(n for n, _ in e.groups)
    # annotation text identical (verbatim labels)
    ta = sorted(t.dxf.text for t in a.modelspace().query("TEXT"))
    te = sorted(t.dxf.text for t in e.modelspace().query("TEXT"))
    assert ta == te
