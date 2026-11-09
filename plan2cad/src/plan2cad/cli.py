"""Command line interface:  python -m plan2cad <command>   (or the `plan2cad` script after `pip install -e .`)

Exit codes: 0 = finished (drawings PASS/REVIEW), 1 = at least one drawing FAILED (or REVIEW with --fail-on review),
            2 = usage / configuration problem, 130 = interrupted.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .errors import ConfigError, Plan2CadError
from .log import setup_console

INIT_TEMPLATE = '''# plan2cad configuration for a NEW drawing (engine auto_lines).
# Fill in every TODO, then:  python -m plan2cad validate {out}   and   python -m plan2cad run {out}
# Source image: {source}  ({w} x {h} px)
name: {name}
engine: auto_lines
source: {source_rel}
outputs: [gpkg]
units: ft-in            # ft-in | m | mm   (how dimension strings are written on the drawing)
ocr: off                # off | auto  (auto reads room labels with RapidOCR if installed; dimensions are only proposed)

# Scale: two pixel positions of the two ends of a length printed on the drawing, for each axis.
# x runs left->right (first px < second px). y runs bottom->top, so the FIRST px is the bottom end (larger row).
calibration:
  x: {{px: [TODO, TODO], length: "TODO"}}     # e.g. {{px: [98, 886], length: "60'"}}
  y: {{px: [TODO, TODO], length: "TODO"}}     # e.g. {{px: [594, 175], length: "31'11\\""}}

# Defaults proven on the exercise drawing; change only if walls are drawn with a different line spacing.
params: {{wall_gap_px: [3.5, 7.5], min_segment_px: 10, ink_threshold: 225}}

# Room labels and dimension strings, verbatim, with a pixel box around each. Leave empty to use OCR (ocr: auto).
texts: []
dimension_texts: []
door_boxes: auto        # auto = detect door arcs; or give boxes: {{D01: [x0, y0, x1, y1], ...}}
review_notes: []
verify: {{tolerance_px: 3.0, min_share: 0.85}}
'''


def _print_table(rows, headers):
    w = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = "  ".join(str(h).ljust(n) for h, n in zip(headers, w))
    print(line)
    print("  ".join("-" * n for n in w))
    for r in rows:
        print("  ".join(str(c).ljust(n) for c, n in zip(r, w)))


def cmd_run(args) -> int:
    from .pipeline import expand_paths, run_many
    paths = expand_paths(args.configs)
    if not paths:
        print("error: no configuration files found", file=sys.stderr)
        return 2
    missing = [p for p in paths if not Path(p).is_file()]
    if missing:
        print("error: not found: " + ", ".join(map(str, missing)), file=sys.stderr)
        return 2
    results = run_many(paths, args.out, do_verify=not args.no_verify)
    print()
    _print_table([[r.name, r.status, r.engine, sum(1 for f in r.flags if f.level == "error"),
                   sum(1 for f in r.flags if f.level == "review"), f"{r.seconds:.1f}s", f"{args.out}/{r.name}/review.md"]
                  for r in results], ["drawing", "status", "engine", "errors", "review", "time", "report"])
    print(f"\nBatch summary: {Path(args.out) / 'batch_summary.md'}")
    if any(r.status == "FAIL" for r in results):
        return 1
    if args.fail_on == "review" and any(r.status == "REVIEW" for r in results):
        return 1
    return 0


def cmd_validate(args) -> int:
    from .config import load_config
    from .pipeline import _ingest, expand_paths
    bad = 0
    for p in expand_paths(args.configs):
        try:
            cfg = load_config(p)
            size, _ = _ingest(cfg)
            print(f"OK    {p}  (engine {cfg.engine}, image {size[0]}x{size[1]}, outputs {cfg.outputs})")
            for w in cfg.warnings:
                print(f"      warning: {w}")
        except ConfigError as e:
            bad += 1
            print(f"FAIL  {p}")
            for pr in e.problems:
                print(f"      - {pr}")
        except Plan2CadError as e:
            bad += 1
            print(f"FAIL  {p}\n      - {e}")
    return 2 if bad else 0


def cmd_doctor(args) -> int:
    from .preflight import doctor
    checks = doctor()
    for c in checks:
        mark = "OK  " if c.ok else ("MISS" if not c.required else "FAIL")
        print(f"[{mark}] {c.name:<28} {c.detail}")
    failed = [c for c in checks if c.required and not c.ok]
    print("\nEnvironment is ready." if not failed else f"\n{len(failed)} required check(s) failed.")
    return 1 if failed else 0


def cmd_ocr(args) -> int:
    import yaml

    from . import ocr
    if not ocr.available():
        print("error: OCR needs rapidocr-onnxruntime:  pip install -e .[ocr]", file=sys.stderr)
        return 2
    words = ocr.read_words(args.image)
    text = yaml.safe_dump(ocr.proposals_dict(words), sort_keys=False, default_flow_style=None, allow_unicode=True)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(words)} text items)")
    else:
        print(text)
    return 0


def cmd_init(args) -> int:
    from PIL import Image
    src = Path(args.image)
    if not src.is_file():
        print(f"error: image not found: {src}", file=sys.stderr)
        return 2
    with Image.open(src) as im:
        w, h = im.size
    out = Path(args.out or f"configs/{args.name}.yaml")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not args.force:
        print(f"error: {out} exists (use --force to overwrite)", file=sys.stderr)
        return 2
    rel = Path(src).resolve().relative_to(out.parent.resolve()) if str(src.resolve()).startswith(str(out.parent.resolve())) \
        else Path(__import__("os").path.relpath(src.resolve(), out.parent.resolve()))
    out.write_text(INIT_TEMPLATE.format(name=args.name, out=out, source=src, source_rel=str(rel).replace("\\", "/"), w=w, h=h),
                   encoding="utf-8")
    print(f"wrote {out}\nNext: fill in the TODO calibration values, then run:  python -m plan2cad validate {out}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="plan2cad", description="Floor-plan image -> DXF + GeoPackage pipeline",
                                epilog="Exit codes: 0 ok, 1 a drawing failed, 2 usage/config problem.")
    p.add_argument("--version", action="version", version=f"plan2cad {__version__}")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    common = argparse.ArgumentParser(add_help=False)          # lets --log-level also follow the sub-command
    common.add_argument("--log-level", default=argparse.SUPPRESS, choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", parents=[common], help="convert one or more drawings (config files or folders of configs)")
    r.add_argument("configs", nargs="+")
    r.add_argument("--out", default="output", help="output root folder (default: output)")
    r.add_argument("--no-verify", action="store_true", help="skip the automatic check against the source image")
    r.add_argument("--fail-on", choices=["error", "review"], default="error",
                   help="exit code 1 also when a drawing needs review (use in CI)")
    r.set_defaults(func=cmd_run)
    v = sub.add_parser("validate", parents=[common], help="check configuration files (and that their image opens) without converting")
    v.add_argument("configs", nargs="+")
    v.set_defaults(func=cmd_validate)
    d = sub.add_parser("doctor", parents=[common], help="check Python, libraries and the GIS/CAD stack on this machine")
    d.set_defaults(func=cmd_doctor)
    o = sub.add_parser("ocr", parents=[common], help="propose texts for an image with OCR (needs the [ocr] extra)")
    o.add_argument("image")
    o.add_argument("--out", help="write proposals to this YAML file")
    o.set_defaults(func=cmd_ocr)
    i = sub.add_parser("init", parents=[common], help="create a skeleton config for a new drawing")
    i.add_argument("image")
    i.add_argument("--name", required=True)
    i.add_argument("--out")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    setup_console(args.log_level)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Plan2CadError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
