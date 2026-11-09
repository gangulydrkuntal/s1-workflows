# Using plan2cad in VS Code: prerequisites and step-by-step guide

Everything runs locally and offline after installation. No AI service, no internet, and no prompts are needed to run
the pipeline: it reads a YAML config and an image, and writes files.

## 1. Prerequisites

| What | Version | Notes |
|---|---|---|
| **Python** | 3.10 or newer (tested: 3.11, 3.12, 3.13) | Windows: install from python.org and tick **"Add python.exe to PATH"**. macOS: `brew install python` or python.org. Linux: your package manager (`python3`, `python3-venv`). |
| **Visual Studio Code** | current | https://code.visualstudio.com |
| VS Code extensions | | **Python** (ms-python.python), **Pylance**, **Ruff** (optional), **YAML** (redhat.vscode-yaml, gives autocompletion for the configs). VS Code offers to install them when you open the folder (`.vscode/extensions.json`). |
| Git | any | only to clone the repository; you can also download a ZIP |
| Disk / RAM | ~1.5 GB disk for libraries, 4 GB RAM is comfortable | |
| **Optional:** QGIS | any 3.x | to look at the GeoPackage (free). |
| **Optional:** a CAD viewer | | to open the DXF (AutoCAD, DraftSight, LibreCAD, QCAD, ...). |
| **Optional:** an image viewer that shows the pixel position of the cursor | | needed once per *new* drawing, to read the scale positions (Paint on Windows, GIMP, Photoshop, many others). |

You do **not** need to install GDAL, Tesseract or any system library: the Python wheels bundle what is needed
(`pyogrio` bundles GDAL; the optional OCR is a pip package).

## 2. One-time setup

1. **Open the folder.** VS Code: *File > Open Folder...* and choose the `plan2cad` folder (the one containing `pyproject.toml`).
   Accept the prompt to install the recommended extensions.
2. **Open a terminal:** *Terminal > New Terminal*.
3. **Create a virtual environment** (an isolated Python for this project):

   | | Windows (PowerShell) | macOS / Linux |
   |---|---|---|
   | create | `py -3.12 -m venv .venv` (or `python -m venv .venv`) | `python3 -m venv .venv` |
   | activate | `.\.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |

   If PowerShell refuses to activate: `Set-ExecutionPolicy -Scope Process RemoteSigned`, then activate again.
   The prompt should now start with `(.venv)`.
4. **Install:**

   ```bash
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   pip install -e .
   ```

   Optional extras: `pip install -e ".[ocr]"` (text proposals), `pip install -e ".[dev]"` (tests, linter).
   `requirements-lock.txt` pins the exact versions the project was tested with (`pip install -r requirements-lock.txt`)
   if you need to reproduce that environment exactly.
5. **Select the interpreter:** press `Ctrl+Shift+P` (macOS `Cmd+Shift+P`), run **Python: Select Interpreter**, choose the one in `.venv`.
6. **Check everything works:**

   ```bash
   python -m plan2cad doctor
   ```

   Expect `[OK  ]` for every line and `Environment is ready.` The last check writes and reads back a real GeoPackage
   and DXF, so it proves the GIS/CAD stack works on your machine. `[MISS]` for `rapidocr-onnxruntime` only means the optional OCR is not installed.

## 3. Convert the three shipped drawings

**Terminal:** `python -m plan2cad run configs`  
**Or in VS Code:** *Terminal > Run Task... > plan2cad: run all drawings* (also the default build task, `Ctrl+Shift+B`).

Takes about 10 seconds. Results are in `output/`:

```
output/
  batch_summary.md                 <- start here for a batch
  drawing3_two_unit_ftin/
    review.md                      <- read this per drawing
    drawing3_two_unit_ftin.gpkg    <- the GIS result
    drawing3_two_unit_ftin_overlay.png, ..._preview.png, summary.json, run.log
```

**Open the results**
* GeoPackage: QGIS > *Layer > Add Layer > Add Vector Layer*, pick the `.gpkg`, add the layers. Default styles are stored
  in the file; if your QGIS does not apply them, label the `annotations` layer by its `text` field.
* DXF (`drawing1_two_floor_sketch.dxf`): open in any CAD viewer. Units are metres. Layers `A-WALL`, `A-DOOR`, ... ;
  each room is a CAD *group* containing its walls and labels.

### How to read `review.md`

1. **The title status:** `PASS`, `REVIEW` or `FAIL` (see the README table).
2. **Review items**, in three levels:
   * *Errors*: why a drawing failed (and what to change).
   * *Needs a decision*: open questions, unverified doors, unlabelled spaces, OCR-read text, verification zones below tolerance.
   * *For information*: decisions already taken and caveats carried from the exercise.
3. **Verification table:** the output was mapped back onto the source image and 1 pixel steps along every line were
   measured against the drawn line work. The table shows, per zone of a 3x3 grid, the share of points within tolerance.
   Open `<name>_overlay.png`: blue lines are fine, **red dots are where the output is not on a drawn line**.
4. **Rooms and areas:** calculated area per room; where the drawing prints an area, the difference is shown.

## 4. Convert your own drawing (engine `auto_lines`)

Works for CAD-style plans drawn with thin double-line walls. See README "Limits".

1. Put the image in `inputs/` (PNG/JPG/TIFF; the larger and cleaner the better; no skew).
2. Create a skeleton config: `python -m plan2cad init inputs/myplan.png --name myplan`. This writes `configs/myplan.yaml`.
3. **Fill in the scale.** In an image viewer that shows pixel coordinates, find a length printed on the drawing (an overall
   dimension is best) and note the pixel positions of its two ends, in the *original* image pixels:

   ```yaml
   units: ft-in            # or m / mm: how dimension strings are written
   calibration:
     x: {px: [98, 886], length: "60'"}        # left end, right end  (px along x)
     y: {px: [594, 175], length: "31'11\""}   # BOTTOM end first, then top end (px rows)
   ```
4. `python -m plan2cad validate configs/myplan.yaml` lists every problem in one go (the YAML extension also
   underlines mistakes live, thanks to `docs/config.schema.json`).
5. **Get text proposals (optional, needs `pip install -e ".[ocr]"`):** set `ocr: auto` and run. Room labels are adopted and
   flagged; dimension candidates are only written to `output/myplan/myplan_texts_proposed.yaml`, with OCR's raw reading.
6. **Confirm the text.** Copy labels and **correct** dimension strings into the config exactly as printed on the drawing,
   using the boxes from the proposals file:

   ```yaml
   texts:
     - {text: "Bedroom", kind: room_label, box: [207, 271, 260, 284]}
   dimension_texts:
     - {text: "14'0\"", box: [185, 223, 206, 232]}
   ```

   Confirming the dimension strings matters: it is what lets the pipeline recognise dimension lines instead of treating them as walls.
7. **Run, then check the overlay:** `python -m plan2cad run configs/myplan.yaml`. Fix what the red dots and `review.md`
   point to, and run again. Doors that were missed need a box: `door_boxes: {D01: [x0, y0, x1, y1], ...}`. Record decisions
   in `review_notes:` and unanswered questions in `open_questions:`.

A minimal example is `configs/examples/drawing3_minimal_auto.yaml` (calibration + `ocr: auto` + automatic doors).

## 5. Everyday commands in VS Code

| Task | How |
|---|---|
| Run all drawings | *Terminal > Run Task > plan2cad: run all drawings* |
| Run the open config file | *Run Task > plan2cad: run current config file* (with the YAML open) |
| Check configs | *Run Task > plan2cad: validate all configs* |
| Check the environment | *Run Task > plan2cad: doctor* |
| Run the tests | *Testing* sidebar (beaker icon) or *Run Task > plan2cad: run tests* (needs `pip install -e ".[dev]"`) |
| Debug a run | *Run and Debug* sidebar > **plan2cad: run current config file** (F5), set breakpoints anywhere in `src/plan2cad` |

## 6. Using it from other systems

* **Command line / CI:** `python -m plan2cad run configs --out output --fail-on review` returns exit code `0` ok, `1` a drawing
  failed (or needs review with `--fail-on review`), `2` usage or config problem.
* **Machine-readable results:** `output/<name>/summary.json` (status, flags with level/code/message, counts per layer,
  verification numbers, stage timings, library versions, sha256 of the source) and `output/batch_summary.json`.
* **Python:** `from plan2cad import run_config, run_many` (see the README). Calls never raise for drawing-level
  problems: inspect `result.status` and `result.flags`.
* **Parallelism:** run separate *processes* (two parallel runs are tested). Do not run several drawings in parallel threads
  of one process: the engines serialise access to the exact exercise scripts and share one log.
* **Folder watching, queues, containers:** not included. The CLI/exit-code/`summary.json` contract above is the integration surface.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `python` not found | Python not on PATH: reinstall with "Add to PATH", or use `py -3.12` (Windows) / `python3` (macOS, Linux). |
| `.\.venv\Scripts\Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope Process RemoteSigned` in that terminal. |
| `ModuleNotFoundError: plan2cad` | Virtual environment not active or interpreter not selected; run `pip install -e .` inside the `.venv`. |
| `ImportError: libGL.so.1` (Linux) | A GUI OpenCV (`opencv-python`) is installed. Run `pip uninstall -y opencv-python opencv-python-headless && pip install opencv-python-headless`. |
| Two OpenCV packages installed | Installing the OCR extra also installs `opencv-python`. If anything misbehaves, run the same two commands as in the row above. `doctor` prints the OpenCV version that actually loads. |
| Pip fails building `shapely`/`pyogrio`/`geopandas` | Use a supported Python (3.10-3.13) and the latest pip (`python -m pip install --upgrade pip`) so binary wheels are chosen. |
| `PermissionError` or "database is locked" on the `.gpkg` | The file is open in QGIS. Close it (or the project) and run again. |
| Status `FAIL` with "recognised as double-line walls" | The drawing is not a thin double-line plan or `params.wall_gap_px` does not match its wall thickness (pixels between the two lines). |
| Status `FAIL` with "tied to a 1600x1079 image" | The two hand-modelled profiles only work with their original images. Use `auto_lines` for new drawings. |
| Many tiny rooms / many unlabelled spaces | Dimension strings are not confirmed in the config, so dimension lines act as walls. Add `dimension_texts` (step 6). |
| Red dots along a whole wall in the overlay | Scale is off: re-check `calibration` pixel positions and printed lengths. A `SCALE_ANISOTROPY` flag appears if x and y scales differ by more than 3 %. |
| `doctor` shows `[FAIL] smoke test` | The GIS/CAD stack is broken in this environment; recreate the `.venv` and reinstall. |

## 8. What was tested

* **109+ automated tests**, ~85 s: units, strict config validation (every error message), exact golden reproduction of the
  three accepted outputs, pipeline behaviour, CLI exit codes, bad input (corrupt/tiny images, unsuitable drawings, wrong
  parameters, simulated crashes, batch isolation, OCR missing), verification metric on synthetic data, an end-to-end
  millimetre plan with known geometry, automatic doors/OCR regression guards, determinism and parallel processes.
* **Clean installs** into fresh virtual environments on Python 3.11, 3.12 and 3.13 (the last with the OCR extra), each running
  `doctor`, the installed `plan2cad` command from another folder, and the full test suite (see the README status line).
* **Not tested:** Windows and macOS, QGIS/CAD applications opening the files, drawings other than the three exercise ones plus one synthetic plan.
