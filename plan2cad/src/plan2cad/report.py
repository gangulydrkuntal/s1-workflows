"""Human (review.md) and machine (summary.json) reports for a drawing and for a batch."""
from __future__ import annotations

import json
from pathlib import Path

STATUS_ICON = {"PASS": "PASS", "REVIEW": "REVIEW", "FAIL": "FAIL"}


def _table(rows, headers):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def write_drawing_report(res, out_dir):
    out_dir = Path(out_dir)
    d = res.to_dict()
    (out_dir / "summary.json").write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")
    L = [f"# {res.name} - {res.status}", "",
         _table([["engine", res.engine], ["config", res.config], ["source", res.source],
                 ["image size", "x".join(map(str, res.image_size)) if res.image_size else "-"],
                 ["source sha256", (res.sha256[:16] + "...") if res.sha256 else "-"],
                 ["started (UTC)", res.started], ["duration", f"{res.seconds:.1f} s"]], ["item", "value"]), ""]
    L.append("## Outputs")
    if res.outputs:
        L.append(_table([[k, Path(v).name] for k, v in res.outputs.items()], ["output", "file"]))
    else:
        L.append("_none produced_")
    if res.counts:
        L += ["", "Features per layer: " + ", ".join(f"{k} {v}" for k, v in res.counts.items())]
    L += ["", "## Review items", ""]
    for level, title in (("error", "Errors (drawing failed)"), ("review", "Needs a decision"), ("info", "For information")):
        fl = [f for f in res.flags if f.level == level]
        if fl:
            L.append(f"**{title}**")
            L += [f"- `{f.code}`{f' ({f.where})' if f.where else ''}: {f.message}" for f in fl]
            L.append("")
    if not res.flags:
        L.append("_nothing to review_")
    v = res.verify
    L += ["## Verification against the source image", ""]
    if v:
        L.append(f"Tolerance {v['tolerance_px']} px, required share per zone {v['min_share']:.0%}. "
                 f"{v['n_samples']} output points sampled: **{v['share_within']:.1%}** lie within tolerance "
                 f"(95th percentile distance {v['p95_px']:.1f} px). Coverage of long source edges by the output: "
                 f"{v['coverage']:.0%} (informational). Result: **{'PASS' if v['passed'] else 'REVIEW'}**.")
        if v.get("zones"):
            L += ["", _table([[z["row"], z["col"], z["samples"], f"{z['share']:.1%}", z["p95_px"], "ok" if z["ok"] else "BELOW"]
                             for z in v["zones"]], ["zone row", "zone col", "points", "within tol.", "p95 px", "status"])]
        if v.get("overlay"):
            L.append(f"\nOverlay: `{Path(v['overlay']).name}` (blue = output line work, red dots = points outside tolerance).")
    else:
        L.append("_not run_")
    if res.areas:
        L += ["", "## Rooms and areas", "", _table(res.areas["rows"], res.areas["headers"])]
    L += ["", "## Stages", "", _table([[s.name, s.status, f"{s.seconds:.2f} s", s.message] for s in res.stages],
                                       ["stage", "status", "time", "message"])]
    L += ["", "## Environment", "", ", ".join(f"{k} {v}" for k, v in res.versions.items())]
    (out_dir / "review.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def write_batch_report(results, out_root):
    out_root = Path(out_root)
    rows = [[r.name, r.status, r.engine, sum(1 for f in r.flags if f.level == "error"),
             sum(1 for f in r.flags if f.level == "review"), f"{r.seconds:.1f}"] for r in results]
    md = ["# plan2cad batch summary", "", _table(rows, ["drawing", "status", "engine", "errors", "review items", "seconds"]), ""]
    for r in results:
        md.append(f"- [{r.name}]({r.name}/review.md): {r.status}")
    (out_root / "batch_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out_root / "batch_summary.json").write_text(json.dumps([r.to_dict() for r in results], indent=2, default=str), encoding="utf-8")
