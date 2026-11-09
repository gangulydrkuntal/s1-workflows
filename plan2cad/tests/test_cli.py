import subprocess
import sys

import pytest
from conftest import CONFIGS, ROOT, write


def cli(*args, cwd=None):
    return subprocess.run([sys.executable, "-W", "ignore", "-m", "plan2cad", *args], capture_output=True, text=True,
                          cwd=cwd or ROOT, timeout=300)


def test_version_and_help():
    r = cli("--version")
    assert r.returncode == 0 and "plan2cad 1.0.0" in r.stdout
    r = cli("--help")
    assert r.returncode == 0 and "run" in r.stdout and "doctor" in r.stdout


def test_doctor_ok():
    r = cli("doctor")
    assert r.returncode == 0 and "Environment is ready" in r.stdout and "smoke test" in r.stdout


def test_validate_good_and_bad(tmp_path):
    assert cli("validate", str(CONFIGS["drawing3_two_unit_ftin"])).returncode == 0
    bad = write(tmp_path / "bad.yaml", "name: x\nengine: nope\nsource: missing.png\n")
    r = cli("validate", str(bad))
    assert r.returncode == 2 and "engine: must be one of" in r.stdout and "image not found" in r.stdout


def test_run_exit_codes_and_log_level_after_subcommand(tmp_path):
    cfg = str(CONFIGS["drawing2_ground_floor_1to50"])
    r = cli("run", cfg, "--out", str(tmp_path / "o"), "--log-level", "ERROR")
    assert r.returncode == 0 and "REVIEW" in r.stdout and (tmp_path / "o" / "drawing2_ground_floor_1to50" / "review.md").is_file()
    assert cli("run", cfg, "--out", str(tmp_path / "o2"), "--fail-on", "review", "--log-level", "ERROR").returncode == 1
    assert cli("run", str(tmp_path / "missing.yaml"), "--out", str(tmp_path / "o3")).returncode == 2
    bad = write(tmp_path / "bad.yaml", "name: x\nengine: nope\nsource: missing.png\n")
    r = cli("run", str(bad), cfg, "--out", str(tmp_path / "o4"), "--log-level", "ERROR")
    assert r.returncode == 1 and "FAIL" in r.stdout and "REVIEW" in r.stdout            # bad one fails, good one still ran
    assert (tmp_path / "o4" / "drawing2_ground_floor_1to50" / "drawing2_ground_floor_1to50.gpkg").is_file()


def test_run_folder_expands(tmp_path):
    r = cli("run", "configs", "--out", str(tmp_path / "all"), "--log-level", "ERROR")
    assert r.returncode == 0
    for n in CONFIGS:
        assert (tmp_path / "all" / n / "review.md").is_file()


def test_init_skeleton(tmp_path):
    img = ROOT / "inputs" / "drawing3_two_unit_ftin.png"
    out = tmp_path / "new.yaml"
    r = cli("init", str(img), "--name", "new_plan", "--out", str(out))
    assert r.returncode == 0 and "1000 x 708" in out.read_text()
    assert cli("validate", str(out)).returncode == 2            # TODO placeholders must be filled in first
    assert cli("init", str(img), "--name", "new_plan", "--out", str(out)).returncode == 2    # refuses to overwrite
    assert cli("init", str(img), "--name", "new_plan", "--out", str(out), "--force").returncode == 0
    assert cli("init", str(tmp_path / "none.png"), "--name", "n").returncode == 2


def test_init_skeleton_becomes_valid_when_filled(tmp_path):
    import re
    img = tmp_path / "plan.png"
    img.write_bytes((ROOT / "inputs" / "drawing3_two_unit_ftin.png").read_bytes())
    out = tmp_path / "n.yaml"
    assert cli("init", str(img), "--name", "n", "--out", str(out)).returncode == 0
    t = out.read_text()
    t = re.sub(r"x: \{px: \[TODO, TODO\], length: \"TODO\"\}", "x: {px: [98, 886], length: \"60'\"}", t)
    t = re.sub(r"y: \{px: \[TODO, TODO\], length: \"TODO\"\}", "y: {px: [594, 175], length: \"31'11\\\"\"}", t)
    out.write_text(t)
    r = cli("validate", str(out))
    assert r.returncode == 0, r.stdout


def test_ocr_command_without_ocr_installed_or_with(tmp_path):
    r = cli("ocr", str(ROOT / "inputs" / "drawing3_two_unit_ftin.png"), "--out", str(tmp_path / "p.yaml"))
    assert r.returncode in (0, 2)
    if r.returncode == 2:
        assert "pip install -e .[ocr]" in r.stderr
    else:
        assert "texts:" in (tmp_path / "p.yaml").read_text()


@pytest.mark.parametrize("args", [[], ["run"], ["bogus"]])
def test_usage_errors_exit_2(args):
    assert cli(*args).returncode == 2
