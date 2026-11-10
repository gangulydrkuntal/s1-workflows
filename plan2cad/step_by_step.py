#!/usr/bin/env python3
"""plan2cad, step by step: ONE file that explains the whole pipeline and runs it one stage at a time.

WHAT THIS FILE IS
    A guided walkthrough. It does exactly what ``python -m plan2cad run <config>`` does, but stops at every stage to
    explain what happens, why, what it produced, and what to do if it fails. It calls the project's own functions
    (nothing is re-implemented here), so what you see is what the real pipeline does.

THE PIPELINE IN ONE PICTURE
    drawing image + YAML config
        0 environment check      are Python and the GIS/CAD libraries usable on this machine?
        1 load config            strict validation: every problem reported at once
        2 ingest image           the image opens, is large enough, fingerprint (sha256) recorded
        3 scale                  pixels per foot/metre from two known printed lengths (engine auto_lines)
        4 text and doors         which labels / dimension strings / door boxes will be used
        5 engine                 extraction -> GeoPackage (and DXF for the sketch profile)
        6 read-back checks       the written files are re-opened and inspected; review flags collected
        7 preview                a picture rendered from the GeoPackage layers
        8 verify against source  output mapped back onto the source image and measured pixel by pixel
        9 report                 review.md + summary.json: status PASS / REVIEW / FAIL and what to look at

PREREQUISITES (details: docs/VSCODE_GUIDE.md)
    * Python 3.10+ (tested 3.11, 3.12, 3.13) and VS Code with the Python extension.
    * A virtual environment with the project installed:
          python -m venv .venv                       (Windows: py -3.12 -m venv .venv)
          source .venv/bin/activate                  (Windows: .\\.venv\\Scripts\\Activate.ps1)
          pip install -r requirements.txt
          pip install -e .                           (optional: pip install -e ".[ocr]" for text proposals)
    * Run this file from the project folder (the one that contains pyproject.toml). If the package is not
      installed it falls back to ./src automatically.

HOW TO RUN
    python step_by_step.py --list                     show all steps, run nothing
    python step_by_step.py                            run all steps on the default drawing (drawing 3, ~5 s)
    python step_by_step.py --pause                    stop after every step and wait for Enter (best for learning)
    python step_by_step.py --until 3                  run steps 0-3 only (e.g. to check config and scale)
    python step_by_step.py --config configs/drawing1_two_floor_sketch.yaml     another drawing (DXF + GeoPackage)
    python step_by_step.py --config configs/myplan.yaml --out my_output        your own drawing
    python step_by_step.py --no-verify                skip step 8

    In VS Code: open this file and press F5 or the Run button; add arguments in .vscode/launch.json if needed.

EXIT CODES
    0 finished (status PASS or REVIEW)    1 a step failed (status FAIL)    2 usage problem / invalid config

WHERE THE RESULTS GO
    <out>/<drawing name>/   <name>.gpkg, <name>.dxf (sketch only), <name>_preview.png, <name>_overlay.png,
                            review.md, summary.json, run.log
    Open review.md first. Red dots in the overlay image are output points that are NOT on a drawn line.

THE ONE RULE
    Nothing is guessed. Anything unclear becomes a flag (error / review / info) instead of a made-up value.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

try:
    import plan2cad  # noqa: F401
except ImportError:
    sys.path.insert(0, str(HERE / "src"))          # not installed: use the source tree next to this file
    try:
        import plan2cad  # noqa: F401
    except ImportError as exc:
        sys.exit(f"plan2cad cannot be imported ({exc}).\n"
                 "Create and activate a virtual environment, then:  pip install -r requirements.txt && pip install -e .")

import warnings  # noqa: E402

warnings.filterwarnings("ignore", message=".*crs.*was not provided.*")   # outputs use local metres: no CRS by design

from plan2cad import ConfigError, load_config  # noqa: E402
from plan2cad.check import check_dxf, check_gpkg, content_flags  # noqa: E402
from plan2cad.engines import get_engine  # noqa: E402
from plan2cad.errors import Plan2CadError  # noqa: E402
from plan2cad.log import get_logger  # noqa: E402
from plan2cad.models import Flag, StageRecord  # noqa: E402
from plan2cad.pipeline import DrawingResult, _areas, _clean, _ingest  # noqa: E402
from plan2cad.preflight import doctor, library_versions  # noqa: E402
from plan2cad.preview import render_preview  # noqa: E402
from plan2cad.report import write_drawing_report  # noqa: E402
from plan2cad.units import FT, parse_length_ft  # noqa: E402
from plan2cad.verify import verify as run_verify  # noqa: E402

DEFAULT_CONFIG = HERE / "configs" / "drawing3_two_unit_ftin.yaml"


# --------------------------------------------------------------------------------------------------------------------
# Plumbing: shared state, output helpers
# --------------------------------------------------------------------------------------------------------------------
class StepFailed(Exception):
    """A step could not complete; the message says why and what to change."""


class Ctx:
    """Everything the steps hand to each other."""

    def __init__(self, args):
        self.args = args
        self.config_path = Path(args.config).resolve()
        self.out_root = Path(args.out).resolve()
        self.cfg = None
        self.out_dir = None
        self.engine_result = None
        self.res = None            # DrawingResult, filled in step by step (same object the real pipeline builds)
        self.log = get_logger("walkthrough")
        self.bad_checks = False


def show(label, value, width=22):
    print(f"    {label:<{width}} {value}")


def table(rows, headers, indent="    "):
    w = [max(len(str(c)) for c in col) for col in zip(headers, *rows)] if rows else [len(h) for h in headers]
    print(indent + "  ".join(str(h).ljust(n) for h, n in zip(headers, w)))
    print(indent + "  ".join("-" * n for n in w))
    for r in rows:
        print(indent + "  ".join(str(c).ljust(n) for c, n in zip(r, w)))


def show_flags(flags, indent="    "):
    if not flags:
        print(indent + "(no flags)")
    for f in flags:
        where = f" ({f.where})" if f.where else ""
        msg = f.message if len(f.message) < 160 else f.message[:157] + "..."
        print(f"{indent}[{f.level:<6}] {f.code}{where}: {msg}")


# --------------------------------------------------------------------------------------------------------------------
# The steps. Each docstring is printed before the step runs: WHAT / WHY / IF IT FAILS.
# --------------------------------------------------------------------------------------------------------------------
def step_environment(ctx):
    """WHAT   Checks Python, every required library, the optional OCR package, and does a real write/read round
            trip of a tiny GeoPackage and DXF on this machine.
    WHY    A broken GIS/CAD stack is the most common reason for cryptic errors later; this finds it in a second.
    IF IT FAILS   Create a fresh virtual environment and reinstall (pip install -r requirements.txt).
            A [MISS] for rapidocr-onnxruntime is fine: OCR is optional (pip install -e ".[ocr]")."""
    checks = doctor()
    for c in checks:
        mark = "OK  " if c.ok else ("MISS" if not c.required else "FAIL")
        print(f"    [{mark}] {c.name:<28} {c.detail}")
    failed = [c for c in checks if c.required and not c.ok]
    if failed:
        raise StepFailed(f"{len(failed)} required check(s) failed; see above")
    ctx.versions = library_versions()


def step_config(ctx):
    """WHAT   Reads the YAML file that describes ONE drawing and validates it strictly: unknown keys (typos), wrong
            types, boxes outside the image, calibration pointing the wrong way, unparseable lengths...
    WHY    All problems are listed at once with the field path, so a config is fixed in one pass. Nothing runs on a
            half-valid config.
    OUTPUT  A Config object. Sections: name, engine, source, outputs, units, ocr, calibration, params, texts,
            dimension_texts, door_boxes, door_overrides, review_notes (decisions already taken, shown as info),
            open_questions (still to be answered: they make the drawing REVIEW), verify (tolerance settings).
    IF IT FAILS   Each message names the field and what is allowed. docs/config.schema.json gives live
            autocompletion/validation for configs in VS Code (YAML extension)."""
    if not ctx.config_path.is_file():
        print(f"error: config not found: {ctx.config_path}", file=sys.stderr)
        raise SystemExit(2)
    try:
        cfg = load_config(ctx.config_path)
    except ConfigError as e:
        print("    The configuration has problems:")
        for p in e.problems:
            print(f"      - {p}")
        raise SystemExit(2)
    ctx.cfg = cfg
    ctx.out_dir = ctx.out_root / cfg.name
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    _clean(ctx.out_dir, cfg.name)                      # remove this tool's own outputs from an earlier run
    ctx.res = DrawingResult(name=cfg.name, engine=cfg.engine, config=str(ctx.config_path), source=str(cfg.source),
                            out_dir=str(ctx.out_dir), versions=getattr(ctx, "versions", library_versions()),
                            started=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    ctx.res.flags += [Flag("review", "QUESTION", q) for q in cfg.open_questions]
    ctx.res.flags += [Flag("info", "NOTE", n) for n in cfg.review_notes]
    ctx.res.flags += [Flag("info", "CONFIG_WARNING", w) for w in cfg.warnings]
    show("name", cfg.name)
    show("engine", {"auto_lines": "auto_lines (automatic extraction from pixels)",
                    "dimension_sheet": "dimension_sheet (hand-modelled profile, exercise 2)",
                    "two_floor_sheet": "two_floor_sheet (hand-modelled profile, exercise 1)"}[cfg.engine])
    show("source image", cfg.source)
    show("outputs", ", ".join(cfg.outputs))
    if cfg.engine == "auto_lines":
        show("units / OCR", f"{cfg.units} / {cfg.ocr}")
        show("texts / dimension texts", f"{len(cfg.texts)} / {len(cfg.dimension_texts)}")
        show("door boxes", "automatic detection" if cfg.door_boxes == "auto" else f"{len(cfg.door_boxes)} given "
             f"({len(cfg.door_overrides)} visual overrides)")
    show("open questions / notes", f"{len(cfg.open_questions)} / {len(cfg.review_notes)}")
    show("verification", f"within {cfg.verify['tolerance_px']} px, {cfg.verify['min_share']:.0%} per zone, "
                         f"grid {cfg.verify['grid'][0]}x{cfg.verify['grid'][1]}")
    for w in cfg.warnings:
        print(f"    warning: {w}")


def step_ingest(ctx):
    """WHAT   Opens the source image (and verifies it is not corrupt), checks it is big enough to measure, and
            records its SHA-256 fingerprint.
    WHY    The fingerprint proves later which exact image a result came from. The two hand-modelled profiles
            are tied to their original image; a different image of the same size is flagged.
    IF IT FAILS   Re-export the drawing as PNG/JPG/TIFF at a larger size; the minimum is 200 px on the short side."""
    try:
        size, sha = _ingest(ctx.cfg)
    except Plan2CadError as e:
        raise StepFailed(str(e))
    ctx.res.image_size, ctx.res.sha256 = size, sha
    ctx.image_size = size
    show("image size", f"{size[0]} x {size[1]} px")
    show("sha256", sha[:32] + "...")
    show("file size", f"{ctx.cfg.source.stat().st_size / 1024:.0f} KB")


def step_scale(ctx):
    """WHAT   (Engine auto_lines) Converts pixels to real length. The config gives, per axis, two pixel positions
            of the two ends of a length printed on the drawing (e.g. the overall width "60'"). The scale is
            pixels per foot = pixel distance / printed length.
    WHY    Every coordinate in the output is (pixel - origin) / scale. If x and y scales differ by more than 3 %
            the image is probably stretched or skewed and a review flag is raised.
    IF IT FAILS / LOOKS WRONG   Re-read the pixel positions in an image viewer; remember y is measured from the
            BOTTOM end (larger row number first). The hand-modelled profiles carry their own calibration."""
    cfg = ctx.cfg
    if cfg.engine != "auto_lines":
        print("    Not used by this engine: the hand-modelled profile carries its own pixel -> metre calibration.")
        return
    cal = cfg.calibration
    lx = parse_length_ft(cal["x"]["length"], cfg.units)
    ly = parse_length_ft(cal["y"]["length"], cfg.units)
    sx = abs(cal["x"]["px"][1] - cal["x"]["px"][0]) / lx
    sy = abs(cal["y"]["px"][1] - cal["y"]["px"][0]) / ly
    table([["x", cal["x"]["px"], cal["x"]["length"], f"{lx:.4f} ft", f"{sx:.3f}", f"{FT / sx * 100:.2f} cm"],
           ["y", cal["y"]["px"], cal["y"]["length"], f"{ly:.4f} ft", f"{sy:.3f}", f"{FT / sy * 100:.2f} cm"]],
          ["axis", "pixel ends", "printed length", "= feet", "px per foot", "1 px equals"])
    show("x/y scale difference", f"{abs(sx / sy - 1):.2%}  " + ("(ok)" if abs(sx / sy - 1) <= 0.03 else "(> 3 %: will be flagged)"))
    show("output origin", "the left end of the x length, bottom end of the y length; Y points up; units metres")


def step_text_and_doors(ctx):
    """WHAT   (Engine auto_lines) Shows the text the extraction will use: room labels and dimension strings, each with
            a pixel box, and how doors are found.
            * Configured text is used verbatim (including the drawing's own spelling).
            * With no text configured and ocr: auto, OCR PROPOSES text: room labels are adopted and flagged; dimension
              strings are only proposed in <name>_texts_proposed.yaml, because OCR garbles small digits and a
              dimension value must never be guessed.
            * Doors: boxes from the config, or automatic detection of quarter-circle arcs.
    WHY    Text boxes keep lettering from being measured as walls; dimension strings let dimension lines be
            recognised instead of being treated as walls.
    IF IT LOOKS WRONG   Without confirmed dimension strings, rooms may split into fragments: confirm the strings
            in the config (see docs/VSCODE_GUIDE.md, section 4)."""
    cfg = ctx.cfg
    if cfg.engine != "auto_lines":
        print("    Not used by this engine: text and geometry are part of the hand-modelled profile.")
        return
    from plan2cad import ocr
    rows = [[t["text"], t["kind"], t["box"], t["rotation"]] for t in cfg.texts[:6]]
    if rows:
        table(rows, ["room/site label (first 6)", "kind", "box [x0,y0,x1,y1]", "rotation"])
        if len(cfg.texts) > 6:
            print(f"    ... {len(cfg.texts) - 6} more")
    if cfg.dimension_texts:
        print("    dimension strings (first 6): " + ", ".join(t["text"] for t in cfg.dimension_texts[:6]) + " ...")
    if not cfg.texts and not cfg.dimension_texts:
        show("configured text", "none")
        show("OCR setting", f"{cfg.ocr} (OCR installed: {ocr.available()})")
    show("doors", "automatic detection" if cfg.door_boxes == "auto" else ", ".join(cfg.door_boxes))
    if cfg.door_overrides:
        print("    visual door decisions on record: " + "; ".join(f"{k}: {v['note']}" for k, v in cfg.door_overrides.items())[:200])


def step_engine(ctx):
    """WHAT   Runs the conversion engine and writes the GeoPackage (and DXF for the sketch profile).
            auto_lines: split colours (black/grey lines, red stairs, green lines) -> straight line segments ->
            pairs of parallel lines are walls -> door arcs are fitted with circles -> dimension lines matched to
            their labels by length -> free space between walls is flood-filled into rooms -> room edges snapped to
            wall faces -> layers rooms, wall_fill, walls, doors, stairs, dimensions, other_lines, annotations.
            Profiles: the exact scripts of the accepted exercise outputs.
    WHY    Every number comes from a printed dimension or from measured pixels; labels are linked to their room.
    IF IT FAILS   The message says what to change. Typical: "only N % of the long lines were recognised as
            double-line walls" means the drawing is not a thin double-line plan or params.wall_gap_px is wrong."""
    cfg = ctx.cfg
    t = time.perf_counter()
    try:
        er = get_engine(cfg.engine)(cfg, ctx.out_dir, ctx.log)
    except Plan2CadError as e:
        raise StepFailed(str(e))
    ctx.engine_result = er
    r = ctx.res
    r.flags += er.flags
    if er.gpkg:
        r.outputs["gpkg"] = str(er.gpkg)
    if er.dxf:
        r.outputs["dxf"] = str(er.dxf)
    r.outputs.update({k: str(v) for k, v in er.extra_files.items()})
    r.stages.append(StageRecord("engine", "ok", time.perf_counter() - t, ", ".join(sorted(r.outputs))))
    for k, v in r.outputs.items():
        show(k, Path(v).name)
    for ln in er.log_lines[-3:]:
        print(f"    engine log: {ln[:150]}")
    if er.flags:
        print("    flags raised by the engine:")
        show_flags(er.flags, indent="      ")


def step_checks(ctx):
    """WHAT   Re-opens the written files and inspects them: the GeoPackage must be readable, required layers present,
            no empty or invalid geometry; the DXF is audited by ezdxf (must report 0 errors). Then the data are
            scanned for review items: doors with an ambiguous opening, dimension labels with no measurable line,
            unlabelled spaces, lines whose meaning the drawing does not state.
    WHY    A file that was written is not necessarily a file that is correct; this catches broken output
            before anybody uses it. Only error-level findings fail the drawing.
    IF IT FAILS   The message names the layer; the cause is usually in the engine step's flags."""
    er, r = ctx.engine_result, ctx.res
    t = time.perf_counter()
    n0 = len(r.flags)
    if er.gpkg:
        r.counts, fl = check_gpkg(er.gpkg, ctx.cfg.engine)
        r.flags += fl + content_flags(er.gpkg)
        r.areas = _areas(er.gpkg)
    if er.dxf:
        n, fl = check_dxf(er.dxf)
        r.counts["dxf_entities"] = n
        r.flags += fl
    ctx.bad_checks = any(f.level == "error" for f in r.flags[n0:])
    r.stages.append(StageRecord("checks", "failed" if ctx.bad_checks else "ok", time.perf_counter() - t))
    table([[k, v] for k, v in r.counts.items()], ["layer / file", "features"])
    print()
    show_flags(r.flags[n0:])
    if r.areas:
        print("\n    room areas (first 8):")
        table(r.areas["rows"][:8], r.areas["headers"])
    if ctx.bad_checks:
        raise StepFailed("output read-back failed; see the error flags above")


def step_preview(ctx):
    """WHAT   Renders a picture from the GeoPackage layers themselves (what a GIS user will see).
    WHY    A quick visual sanity check of the result, independent of the source image.
    IF IT FAILS   Never fatal: it only adds an information flag."""
    t = time.perf_counter()
    try:
        p = ctx.out_dir / f"{ctx.cfg.name}_preview.png"
        render_preview(ctx.engine_result.gpkg, p)
        ctx.res.outputs["preview"] = str(p)
        ctx.res.stages.append(StageRecord("preview", "ok", time.perf_counter() - t))
        show("preview image", p)
    except Exception as e:                                  # noqa: BLE001 - preview must never stop the pipeline
        ctx.res.flags.append(Flag("info", "PREVIEW_FAILED", f"preview could not be rendered: {e}"))
        ctx.res.stages.append(StageRecord("preview", "skipped", time.perf_counter() - t, str(e)))
        print(f"    preview skipped: {e}")


def step_verify(ctx):
    """WHAT   The automatic replacement for "look at the overlay": every output line is mapped back to source-image
            pixels and sampled every pixel; for each sample the distance to the nearest drawn line pixel is measured.
            The drawing is cut into a grid of zones; a zone passes when at least min_share of its samples are
            within tolerance_px. Samples that are not on a drawn line are painted RED in the overlay image.
    WHY    Catches misplaced walls, wrong scale, missed or invented geometry, with numbers instead of opinion.
            'Coverage' (informational) says how much of the long straight source edges the output covers.
    IF A ZONE IS BELOW TARGET   Open the overlay: red dots show where. Check calibration, wall_gap_px, or whether
            that part of the drawing is a different style. This raises a review flag, not an error."""
    cfg, er, r = ctx.cfg, ctx.engine_result, ctx.res
    t = time.perf_counter()
    v = cfg.verify
    try:
        vr = run_verify(cfg.source, er.gpkg, er.frame, v["tolerance_px"], v["min_share"], tuple(v["grid"]),
                        v["min_samples"], ctx.out_dir / f"{cfg.name}_overlay.png")
    except Exception as e:                                  # noqa: BLE001
        r.flags.append(Flag("review", "VERIFY_NOT_RUN", f"verification could not run: {type(e).__name__}: {e}"))
        r.stages.append(StageRecord("verify", "skipped", time.perf_counter() - t, str(e)))
        print(f"    verification could not run: {e}")
        return
    r.verify = vr.to_dict()
    if vr.overlay:
        r.outputs["overlay"] = vr.overlay
    if not vr.passed:
        low = [f"({z['row']},{z['col']}) {z['share']:.0%}" for z in vr.zones if not z["ok"]]
        r.flags.append(Flag("review", "VERIFY", vr.message or f"zone(s) below {vr.min_share:.0%} within {vr.tolerance_px} px: "
                            f"{', '.join(low) or 'no zone had enough line work'} - inspect the overlay"))
    r.stages.append(StageRecord("verify", "ok", time.perf_counter() - t, f"{vr.share_within:.1%} within {vr.tolerance_px} px"))
    show("points sampled", vr.n_samples)
    show("within tolerance", f"{vr.share_within:.1%}   (95th percentile distance {vr.p95_px:.1f} px)")
    show("source edges covered", f"{vr.coverage:.0%}  (informational)")
    show("result", "PASS" if vr.passed else "REVIEW (some zone below target)")
    print("\n    zones (row, col): share of points within tolerance")
    grid = {(z["row"], z["col"]): z for z in vr.zones}
    for i in range(cfg.verify["grid"][0]):
        print("      " + "   ".join((f"{grid[(i, j)]['share']:>6.1%}" if (i, j) in grid else "   n/a ") for j in range(cfg.verify["grid"][1])))
    show("overlay image", vr.overlay)


def step_report(ctx):
    """WHAT   Writes review.md (for people) and summary.json (for other systems), and derives the final status:
              FAIL    an error-level flag exists or a stage failed (no trustworthy output)
              REVIEW  outputs are checked, but a person must decide on the flagged items
              PASS    every automatic check passed and nothing needs a decision
    WHY    The report is the hand-over: it lists outputs, flags, verification numbers, room areas, timings and the
            exact library versions and source fingerprint, so a result can be audited later.
    NEXT   Open review.md, then the overlay PNG, then load the .gpkg in QGIS (and the .dxf in a CAD viewer)."""
    r = ctx.res
    r.seconds = (dt.datetime.now(dt.timezone.utc) - dt.datetime.strptime(r.started, "%Y-%m-%d %H:%M:%S")
                 .replace(tzinfo=dt.timezone.utc)).total_seconds()
    write_drawing_report(r, ctx.out_dir)
    by = {lv: sum(1 for f in r.flags if f.level == lv) for lv in ("error", "review", "info")}
    show("status", r.status)
    show("flags", f"{by['error']} error, {by['review']} need a decision, {by['info']} information")
    show("report", ctx.out_dir / "review.md")
    show("machine-readable", ctx.out_dir / "summary.json")
    review = [f for f in r.flags if f.level == "review"]
    if review:
        print("\n    items that need a decision:")
        show_flags(review[:8], indent="      ")
        if len(review) > 8:
            print(f"      ... {len(review) - 8} more in review.md")


STEPS = [
    ("Environment check", step_environment),
    ("Load and validate the configuration", step_config),
    ("Ingest the source image", step_ingest),
    ("Scale (pixels to real length)", step_scale),
    ("Text and doors", step_text_and_doors),
    ("Engine: extract geometry and write outputs", step_engine),
    ("Read-back checks of the outputs", step_checks),
    ("Preview image", step_preview),
    ("Verify against the source image", step_verify),
    ("Report and final status", step_report),
]


# --------------------------------------------------------------------------------------------------------------------
def list_steps():
    print(__doc__.split("PREREQUISITES")[0].rstrip())
    print("\nSTEPS (what each one does):\n")
    for i, (title, fn) in enumerate(STEPS):
        print(f"[{i}] {title}")
        print("    " + (fn.__doc__ or "").strip().replace("\n", "\n    ") + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="plan2cad step by step: explains and runs the pipeline one stage at a time.",
                                 epilog="Exit codes: 0 finished, 1 a step failed, 2 usage/config problem.")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="drawing config YAML (default: drawing 3)")
    ap.add_argument("--out", default=str(HERE / "output_walkthrough"), help="output folder (default: ./output_walkthrough)")
    ap.add_argument("--until", type=int, default=len(STEPS) - 1, metavar="N", help="stop after step N (0-%d)" % (len(STEPS) - 1))
    ap.add_argument("--pause", action="store_true", help="wait for Enter after every step")
    ap.add_argument("--no-verify", action="store_true", help="skip step 8 (verification against the source image)")
    ap.add_argument("--list", action="store_true", help="describe all steps and exit")
    args = ap.parse_args(argv)
    if args.list:
        list_steps()
        return 0
    if not 0 <= args.until < len(STEPS):
        print(f"error: --until must be between 0 and {len(STEPS) - 1}", file=sys.stderr)
        return 2

    ctx = Ctx(args)
    t_all = time.perf_counter()
    print("plan2cad step by step  -  config:", ctx.config_path)
    for i, (title, fn) in enumerate(STEPS):
        if i > args.until:
            break
        print("\n" + "=" * 100)
        print(f"STEP {i}/{len(STEPS) - 1}: {title}")
        print("=" * 100)
        print("    " + (fn.__doc__ or "").strip().replace("\n", "\n    "))
        print("    " + "-" * 60)
        if fn is step_verify and args.no_verify:
            print("    skipped (--no-verify)")
            continue
        t = time.perf_counter()
        try:
            fn(ctx)
        except StepFailed as e:
            print(f"\n    STEP {i} FAILED: {e}")
            if ctx.res is not None:
                ctx.res.flags.append(Flag("error", "STEP_FAILED", f"step {i} ({title}): {e}"))
                ctx.res.stages.append(StageRecord(title, "failed", time.perf_counter() - t, str(e)))
                try:
                    write_drawing_report(ctx.res, ctx.out_dir)
                    print(f"    A failure report was written to {ctx.out_dir / 'review.md'}")
                except Exception:                           # noqa: BLE001
                    pass
            return 1
        except KeyboardInterrupt:
            print("\ninterrupted")
            return 130
        print(f"    -> step {i} done in {time.perf_counter() - t:.1f} s")
        if args.pause and sys.stdin.isatty() and i < args.until:
            try:
                input("\n    Press Enter for the next step (Ctrl+C to stop)... ")
            except (EOFError, KeyboardInterrupt):
                print()
                return 130
    print("\n" + "=" * 100)
    if args.until == len(STEPS) - 1 and ctx.res is not None:
        print(f"FINISHED in {time.perf_counter() - t_all:.1f} s  -  {ctx.res.name}: {ctx.res.status}")
        print(f"Read next: {ctx.out_dir / 'review.md'}")
        return 1 if ctx.res.status == "FAIL" else 0
    print(f"Stopped after step {args.until} as requested (--until).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
