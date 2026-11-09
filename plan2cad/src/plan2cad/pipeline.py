"""End-to-end orchestration: config -> ingest -> (OCR) -> engine -> output checks -> preview -> verification -> report.

Design rules
* one drawing never stops another (errors are captured per drawing);
* nothing is guessed: anything unclear becomes a flag in review.md; only `error` flags fail a drawing;
* every stage is timed and recorded.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from pathlib import Path

from .check import check_dxf, check_gpkg, content_flags, read_layer
from .config import Config, load_config
from .engines import get_engine
from .engines.profile_common import sha256_of
from .errors import ConfigError, IngestError, Plan2CadError
from .log import attach_file, detach, get_logger
from .models import Flag, StageRecord
from .preflight import library_versions
from .preview import render_preview
from .report import write_batch_report, write_drawing_report
from .verify import verify as run_verify


@dataclass
class DrawingResult:
    name: str
    engine: str = ""
    config: str = ""
    source: str = ""
    sha256: str = ""
    image_size: tuple | None = None
    out_dir: str = ""
    started: str = ""
    seconds: float = 0.0
    stages: list = field(default_factory=list)
    outputs: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    flags: list = field(default_factory=list)
    verify: dict | None = None
    areas: dict | None = None
    versions: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        if any(f.level == "error" for f in self.flags) or any(s.status == "failed" for s in self.stages):
            return "FAIL"
        if any(f.level == "review" for f in self.flags):
            return "REVIEW"
        return "PASS"

    def to_dict(self):
        return dict(name=self.name, status=self.status, engine=self.engine, config=self.config, source=self.source,
                    sha256=self.sha256, image_size=self.image_size, out_dir=self.out_dir, started=self.started,
                    seconds=round(self.seconds, 3), stages=[s.to_dict() for s in self.stages], outputs=self.outputs,
                    counts=self.counts, flags=[f.to_dict() for f in self.flags], verify=self.verify, areas=self.areas,
                    versions=self.versions)


def _ingest(cfg):
    from PIL import Image
    try:
        with Image.open(cfg.source) as im:
            im.verify()
        with Image.open(cfg.source) as im:
            size = im.size
    except Exception as e:
        raise IngestError(f"cannot read the source image {cfg.source}: {type(e).__name__}: {e}") from e
    if min(size) < 200:
        raise IngestError(f"source image is too small ({size[0]}x{size[1]} px); line work cannot be measured")
    return size, sha256_of(cfg.source)


def _areas(gpkg):
    try:
        g = read_layer(gpkg, "rooms")
    except Exception:
        return None
    ac = "area_m2" if "area_m2" in g.columns else "area_calc_m2" if "area_calc_m2" in g.columns else None
    if ac is None:
        return None
    nc = "name" if "name" in g.columns else "room_type" if "room_type" in g.columns else None
    headers = ["room", "name", "area m2"] + (["labelled m2", "diff"] if "area_label_m2" in g.columns else [])
    rows = []
    for _, r in g.iterrows():
        row = [r.get("room_id", ""), (r[nc] if nc else "") or "(unlabelled)", f"{r[ac]:.2f}"]
        if "area_label_m2" in g.columns:
            lab = r["area_label_m2"]
            ok = lab == lab and lab not in (None, "")
            row += [f"{lab:.2f}" if ok else "-", f"{(r[ac] - lab) / lab:+.1%}" if ok and lab else "-"]
        rows.append(row)
    return dict(headers=headers, rows=rows)


def _clean(out_dir: Path, name: str):
    for p in list(out_dir.glob(f"{name}*")) + [out_dir / "review.md", out_dir / "summary.json", out_dir / "run.log"]:
        if p.is_file():
            p.unlink()


def run_config(config, out_root="output", *, do_verify: bool = True) -> DrawingResult:
    """Run one drawing. `config` is a path or a Config. Never raises for drawing-level problems: see result.status."""
    log = get_logger("run")
    t0 = time.perf_counter()
    stem = Path(config).stem if not isinstance(config, Config) else config.name
    res = DrawingResult(name=stem, config=str(config.config_path if isinstance(config, Config) else config),
                        started=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), versions=library_versions())
    out_dir = Path(out_root) / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    fh = None
    cfg = None
    try:
        # ---- stage: config
        t = time.perf_counter()
        try:
            cfg = config if isinstance(config, Config) else load_config(config)
        except ConfigError as e:
            res.stages.append(StageRecord("config", "failed", time.perf_counter() - t, "invalid configuration"))
            res.flags += [Flag("error", "CONFIG", p) for p in e.problems]
            return res
        res.name, res.engine, res.source = cfg.name, cfg.engine, str(cfg.source)
        if cfg.name != stem:                       # config passed as path: output folder follows the config's name
            out_dir = Path(out_root) / cfg.name
            out_dir.mkdir(parents=True, exist_ok=True)
        _clean(out_dir, cfg.name)
        fh = attach_file(out_dir / "run.log")
        res.out_dir = str(out_dir)
        res.stages.append(StageRecord("config", "ok", time.perf_counter() - t, f"engine {cfg.engine}"))
        res.flags += [Flag("info", "CONFIG_WARNING", w) for w in cfg.warnings]
        res.flags += [Flag("review", "QUESTION", q) for q in cfg.open_questions]
        res.flags += [Flag("info", "NOTE", n) for n in cfg.review_notes]
        log.info("[%s] start (engine %s)", cfg.name, cfg.engine)

        # ---- stage: ingest
        t = time.perf_counter()
        try:
            res.image_size, res.sha256 = _ingest(cfg)
        except Plan2CadError as e:
            res.stages.append(StageRecord("ingest", "failed", time.perf_counter() - t, str(e)))
            res.flags.append(Flag("error", "INGEST", str(e)))
            return res
        res.stages.append(StageRecord("ingest", "ok", time.perf_counter() - t, f"{res.image_size[0]}x{res.image_size[1]} px"))

        # ---- stage: engine (text proposals/OCR happen inside the engine that needs them)
        t = time.perf_counter()
        try:
            er = get_engine(cfg.engine)(cfg, out_dir, log)
        except Plan2CadError as e:
            res.stages.append(StageRecord("engine", "failed", time.perf_counter() - t, str(e)))
            res.flags.append(Flag("error", "ENGINE", str(e)))
            return res
        except Exception as e:                      # a bug: never crash the batch, but say so plainly
            log.exception("unexpected engine error")
            res.stages.append(StageRecord("engine", "failed", time.perf_counter() - t, f"{type(e).__name__}: {e}"))
            res.flags.append(Flag("error", "UNEXPECTED", f"unexpected {type(e).__name__}: {e} (see run.log)"))
            return res
        res.flags += er.flags
        for ln in er.log_lines:
            log.info("[%s] %s", cfg.name, ln)
        if er.gpkg:
            res.outputs["gpkg"] = str(er.gpkg)
        if er.dxf:
            res.outputs["dxf"] = str(er.dxf)
        res.outputs.update({k: str(v) for k, v in er.extra_files.items()})
        res.stages.append(StageRecord("engine", "ok", time.perf_counter() - t, ", ".join(sorted(res.outputs))))

        # ---- stage: output checks
        t = time.perf_counter()
        n_before = len(res.flags)
        if er.gpkg:
            res.counts, fl = check_gpkg(er.gpkg, cfg.engine)
            res.flags += fl + content_flags(er.gpkg)
            res.areas = _areas(er.gpkg)
        if er.dxf:
            n, fl = check_dxf(er.dxf)
            res.counts["dxf_entities"] = n
            res.flags += fl
        bad = [f for f in res.flags[n_before:] if f.level == "error"]
        res.stages.append(StageRecord("checks", "failed" if bad else "ok", time.perf_counter() - t,
                                      f"{sum(res.counts.values())} features/entities read back"))

        # ---- stage: preview (never fatal)
        t = time.perf_counter()
        if er.gpkg and not bad:
            try:
                p = out_dir / f"{cfg.name}_preview.png"
                render_preview(er.gpkg, p)
                res.outputs["preview"] = str(p)
                res.stages.append(StageRecord("preview", "ok", time.perf_counter() - t))
            except Exception as e:
                res.flags.append(Flag("info", "PREVIEW_FAILED", f"preview could not be rendered: {e}"))
                res.stages.append(StageRecord("preview", "skipped", time.perf_counter() - t, str(e)))

        # ---- stage: verification against the source
        t = time.perf_counter()
        if do_verify and er.gpkg and er.frame and not bad:
            try:
                v = cfg.verify
                vr = run_verify(cfg.source, er.gpkg, er.frame, v["tolerance_px"], v["min_share"], tuple(v["grid"]),
                                v["min_samples"], out_dir / f"{cfg.name}_overlay.png")
                res.verify = vr.to_dict()
                if vr.overlay:
                    res.outputs["overlay"] = vr.overlay
                if not vr.passed:
                    low = [f"({z['row']},{z['col']}) {z['share']:.0%}" for z in vr.zones if not z["ok"]]
                    res.flags.append(Flag("review", "VERIFY", vr.message or
                                          f"zone(s) below {vr.min_share:.0%} within {vr.tolerance_px} px of the source: "
                                          f"{', '.join(low) or 'no zone had enough line work'} - inspect the overlay"))
                res.stages.append(StageRecord("verify", "ok", time.perf_counter() - t,
                                              f"{vr.share_within:.1%} within {vr.tolerance_px} px"))
            except Exception as e:
                log.exception("verification failed to run")
                res.flags.append(Flag("review", "VERIFY_NOT_RUN", f"verification could not run: {type(e).__name__}: {e}"))
                res.stages.append(StageRecord("verify", "skipped", time.perf_counter() - t, str(e)))
        else:
            res.stages.append(StageRecord("verify", "skipped", 0.0, "disabled" if not do_verify else "no output to verify"))
        return res
    finally:
        res.seconds = time.perf_counter() - t0
        try:
            write_drawing_report(res, out_dir)
        except Exception:
            log.exception("could not write report")
        log.info("[%s] %s in %.1fs", res.name, res.status, res.seconds)
        if fh is not None:
            detach(fh)


def expand_paths(paths) -> list:
    """Files as given; directories expand to their *.yaml / *.yml files (sorted)."""
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += sorted(list(p.glob("*.yaml")) + list(p.glob("*.yml")))
        else:
            out.append(p)
    return out


def run_many(paths, out_root="output", *, do_verify: bool = True) -> list:
    """Run several drawings; a failure in one never stops the others. Writes batch_summary.{md,json}."""
    results = [run_config(p, out_root, do_verify=do_verify) for p in expand_paths(paths)]
    Path(out_root).mkdir(parents=True, exist_ok=True)
    write_batch_report(results, out_root)
    return results
