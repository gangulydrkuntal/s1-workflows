# plan2cad

Converts floor-plan **drawings (images)** into a **GeoPackage** (GIS) and, for the sketch profile, a **DXF** (CAD),
with every label linked to its room, every value traceable to the drawing, and an automatic check of the result
against the source image. It is the exercise we ran three times by hand-and-script, turned into one repeatable pipeline.

```
config (YAML) -> ingest -> [OCR proposals] -> engine -> output checks -> preview -> verification -> review report
```

**Start here:** [docs/VSCODE_GUIDE.md](docs/VSCODE_GUIDE.md) (prerequisites and step-by-step use in VS Code), or run the
single-file guided walkthrough, which explains and executes the pipeline one stage at a time:
`python step_by_step.py --pause` (see the docstring of [step_by_step.py](step_by_step.py); `--list` describes every step).

## 60-second quick start

```bash
python -m venv .venv                      # Windows: py -3.12 -m venv .venv
source .venv/bin/activate                 # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt && pip install -e .
python -m plan2cad doctor                 # checks the whole stack, including a real GeoPackage/DXF round trip
python -m plan2cad run configs            # converts the three shipped drawings into ./output/
```

Open `output/<drawing>/review.md` first. It lists what is verified and what a person still has to decide.

## What it does and does not do automatically

| Engine | For | What is automatic |
|---|---|---|
| `auto_lines` | **New drawings**: CAD-style plans with thin double-line walls | line extraction, wall pairing, door-arc fitting, room detection, dimension-line matching, snapping to wall faces, GeoPackage, scale from two known lengths |
| `dimension_sheet` | The Griffith 1:50 ground-floor plan (exercise 2) | reproduces the accepted output exactly (hand-modelled from its dimension strings) |
| `two_floor_sheet` | The two-floor office sketch (exercise 1) | reproduces the accepted output exactly, DXF + GeoPackage |

What a person still supplies (by design: the pipeline never guesses):
* the **scale** of a new drawing: two pixel positions of a printed length per axis;
* the **text** of labels and dimension strings (or confirm OCR's proposals);
* decisions on everything flagged in `review.md`.

OCR (optional, `pip install -e ".[ocr]"`) *adopts room labels* (flagged for review) but only *proposes dimension strings*:
on the exercise drawing it reads room labels well but garbles tiny dimension text (`14'0"` becomes `140'`), so a value is never adopted from OCR alone.

## Results on the three exercise drawings

| Check | Result |
|---|---|
| GeoPackage vs the accepted exercise output | **row-for-row identical** (geometry to 1e-6 m and every attribute), all 3 drawings |
| DXF vs the accepted exercise output | identical entity census per layer/type, identical text, 0 audit errors |
| Output line work within 3 px of the source drawing (drawings 2, 3; 6 px for the photographed sketch) | 96.7 % / 95.7 % / 99.8 % of 169,000 sampled points; every zone of a 3x3 grid passes |
| Clean installs (fresh venvs, newer libraries: numpy 2.4/2.5, Pillow 13, OpenCV 5) | Python 3.11, 3.12 (107 passed, 2 OCR tests skipped) and 3.13 with OCR (all pass); golden reproduction identical in all |
| Status | `REVIEW` for all three: each carries open questions from the exercise (unlabelled spaces, unknown line meanings, ...) |

## Outputs per drawing (`output/<name>/`)

| File | Content |
|---|---|
| `<name>.gpkg` | GeoPackage: `rooms`, `wall_fill`, `walls`, `doors`, `stairs`, `dimensions`, `annotations`, ... plus QGIS default styles |
| `<name>.dxf` | CAD drawing (only with engine `two_floor_sheet`) |
| `<name>_preview.png` | picture rendered from the GeoPackage layers |
| `<name>_overlay.png` | output drawn over the source: blue = line work, **red dots = points outside tolerance** |
| `review.md` / `summary.json` | human / machine report: status, flags, verification table, room areas, stage timings, library versions |
| `<name>_texts_proposed.yaml` | OCR proposals (only when OCR ran) |
| `run.log` | detailed log |

`output/batch_summary.md|json` summarises a whole run.

## Status and exit codes

| Drawing status | Meaning |
|---|---|
| `PASS` | all automatic checks passed and nothing needs a decision |
| `REVIEW` | outputs are produced and checked, but a person must look at flagged items (open questions, unverified doors, unlabelled spaces, OCR use, verification zones below tolerance) |
| `FAIL` | no trustworthy output: bad config or image, extraction implausible (e.g. under 25 % of long lines recognised as walls), failed read-back or DXF audit |

CLI exit code: `0` finished, `1` a drawing failed (or `REVIEW` with `--fail-on review`), `2` usage/config problem.
One failing drawing never stops the others, and a report is written even for failures.

## Commands

```text
python -m plan2cad run <config|folder> ...  [--out DIR] [--no-verify] [--fail-on error|review]
python -m plan2cad validate <config|folder> ...      check configs and that their image opens
python -m plan2cad doctor                            check Python, libraries, GIS/CAD stack
python -m plan2cad init <image> --name NAME          skeleton config for a new drawing
python -m plan2cad ocr <image> [--out file.yaml]     propose texts (needs the [ocr] extra)
```

Python API:

```python
from plan2cad import run_config, run_many
result = run_config("configs/drawing3_two_unit_ftin.yaml", "output")
print(result.status, result.outputs["gpkg"])
print([f.message for f in result.flags if f.level == "review"])
```

## Project layout

```
configs/            one YAML per drawing (+ examples/ minimal config for a new drawing)
inputs/             the three source images
src/plan2cad/       cli, config (strict validation), pipeline, check, verify, preview, report, ocr, preflight
  engines/          auto_lines, dimension_sheet, two_floor_sheet  (+ legacy/ = the exact exercise scripts)
step_by_step.py     ONE file: explains and runs the whole pipeline step by step (--list, --pause, --until N)
tests/              115 tests incl. golden comparison against the accepted outputs (tests/golden/)
docs/               VSCODE_GUIDE.md, config.schema.json (VS Code autocompletion for configs)
.vscode/            tasks, debug configurations, recommended extensions
```

## Limits, stated plainly

* `auto_lines` is proven on one real drawing (the two-unit plan) and on a synthetic millimetre plan. It expects thin
  double-line walls, orthogonal geometry and door arcs; other styles (filled walls, hatched walls, photographs, skew)
  fail loudly or need a hand-modelled profile like the two exercise sketches.
* Automatic door detection recovered 11 of 15 doors on the exercise drawing (2 extra detections); doors with arcs
  hidden under text need boxes in the config. Missed or ambiguous doors are flagged, never guessed.
* Without confirmed dimension strings, dimension lines cannot be recognised and may split rooms into fragments
  (the OCR flag says so). The workflow in the guide (OCR proposes, a person confirms, re-run) avoids this.
* Developed and tested on Linux (Python 3.11, 3.12, 3.13). Windows and macOS are expected to work (pure pip wheels,
  `pathlib` throughout) but were **not** tested here.
* Output is in local metres with no coordinate system; georeferencing is not part of this pipeline.
* A hand-modelled profile for a *new* sketch of the exercise-1/2 kind is a developer task (copy a legacy module and edit
  its data tables); it is not config-only.

Design background: `floor_plans/ARCHITECTURE_HLD_LLD.md` and `floor_plans/PROMPT_floor_plan_to_cad_gis.md` (repository root).
