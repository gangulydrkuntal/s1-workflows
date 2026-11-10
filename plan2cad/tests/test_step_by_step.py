"""The single-file walkthrough must really run the pipeline, explain each step, and use the same exit codes."""
import subprocess
import sys

import yaml
from conftest import CONFIGS, ROOT, write

SCRIPT = ROOT / "step_by_step.py"


def walk(*args):
    return subprocess.run([sys.executable, "-W", "ignore", str(SCRIPT), *args], capture_output=True, text=True,
                          cwd=ROOT, timeout=300)


def test_list_explains_every_step():
    r = walk("--list")
    assert r.returncode == 0
    for i in range(10):
        assert f"[{i}] " in r.stdout
    for word in ("WHAT", "WHY", "IF IT FAILS", "THE PIPELINE IN ONE PICTURE"):
        assert word in r.stdout


def test_full_run_matches_the_pipeline_output(tmp_path):
    from golden_utils import compare_gpkg
    r = walk("--config", str(CONFIGS["drawing2_ground_floor_1to50"]), "--out", str(tmp_path))
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr
    for i in range(10):
        assert f"STEP {i}/9" in r.stdout
    assert "FINISHED" in r.stdout and "REVIEW" in r.stdout
    d = tmp_path / "drawing2_ground_floor_1to50"
    for f in ("drawing2_ground_floor_1to50.gpkg", "drawing2_ground_floor_1to50_overlay.png", "review.md", "summary.json"):
        assert (d / f).is_file()
    assert compare_gpkg(d / "drawing2_ground_floor_1to50.gpkg", ROOT / "tests" / "golden" / "drawing2.gpkg") == []


def test_until_and_no_verify(tmp_path):
    r = walk("--until", "3", "--out", str(tmp_path))
    assert r.returncode == 0 and "Stopped after step 3" in r.stdout and "STEP 4/9" not in r.stdout
    assert "px per foot" in r.stdout
    r = walk("--config", str(CONFIGS["drawing2_ground_floor_1to50"]), "--no-verify", "--out", str(tmp_path / "nv"))
    assert r.returncode == 0 and "skipped (--no-verify)" in r.stdout
    assert not list((tmp_path / "nv").rglob("*_overlay.png"))


def test_usage_and_config_errors_exit_2(tmp_path):
    assert walk("--until", "99").returncode == 2
    assert walk("--config", str(tmp_path / "missing.yaml")).returncode == 2
    bad = write(tmp_path / "bad.yaml", "name: x\nengine: nope\nsource: missing.png\n")
    r = walk("--config", str(bad), "--out", str(tmp_path / "o"))
    assert r.returncode == 2 and "engine: must be one of" in r.stdout


def test_failing_step_exits_1_and_writes_a_failure_report(tmp_path):
    d = yaml.safe_load(CONFIGS["drawing3_two_unit_ftin"].read_text())
    d["source"] = str(ROOT / "inputs" / "drawing3_two_unit_ftin.png")
    d["name"] = "walk_fail"
    d["params"] = {"wall_gap_px": [9, 12]}                       # wrong wall spacing: extraction is implausible
    r = walk("--config", str(write(tmp_path / "f.yaml", yaml.safe_dump(d))), "--out", str(tmp_path / "o"))
    assert r.returncode == 1 and "STEP 5 FAILED" in r.stdout and "recognised as double-line walls" in r.stdout
    assert "walk_fail - FAIL" in (tmp_path / "o" / "walk_fail" / "review.md").read_text()
