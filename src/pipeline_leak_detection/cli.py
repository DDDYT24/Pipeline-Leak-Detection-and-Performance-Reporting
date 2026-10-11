from argparse import ArgumentParser
from importlib.metadata import version
import json
from pathlib import Path

from . import __version__
from .detector import DetectionSettings, detect
from .evaluation import evaluate
from .simulate import generate
from .study import (
    comparison_exposure, expand_study, fingerprint, freeze_rule,
    report_text, run_detection, summarize, validate_frozen_rule,
)


def main() -> None:
    """生成合成测量、逐点判断及事件评价，并记录配置与依赖。"""
    parser = ArgumentParser(
        description="Generate synthetic pipeline data, alarms, and event-level metrics."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--config", type=Path, default=Path("configs/starter.json"))
    parser.add_argument("--output", type=Path)
    source.add_argument("--study", type=Path, help="Stage 4-5 study configuration (separate from starter).")
    parser.add_argument("--phase", choices=("calibration", "test"))
    parser.add_argument("--frozen-rule", type=Path, help="Required pre-existing rule file for test phase.")
    args = parser.parse_args()
    if args.study:
        if not args.phase:
            parser.error("--study requires --phase calibration or test")
        if args.phase == "test" and not args.frozen_rule:
            parser.error("test phase requires --frozen-rule from calibration")
        if args.phase == "calibration" and args.frozen_rule:
            parser.error("calibration phase creates its own frozen rule")
        study = json.loads(args.study.read_text(encoding="utf-8"))
        config = expand_study(study, args.phase)
        output = args.output or Path("data/generated/study") / args.phase
        if args.phase == "test" and output.resolve() == args.frozen_rule.resolve().parent:
            parser.error("test output must be separate from the source calibration directory")
        # 测试规则验证先于测试信号生成；不根据测试表现再次调参。
        frozen = None
        if args.phase == "test":
            frozen = json.loads(args.frozen_rule.read_text(encoding="utf-8"))
            settings = validate_frozen_rule(study, frozen)
        frames = generate(config)
        if args.phase == "calibration":
            frozen = freeze_rule(study, frames)
            settings = validate_frozen_rule(study, frozen)
        frames["runs"]["rule_sha256"] = frozen["rule_sha256"]
        run_detection(frames, config, settings)
        frames["comparison_exposure"] = comparison_exposure(frames)
        frames["study_summary"] = summarize(frames)
        frames["study_summary"]["rule_sha256"] = frozen["rule_sha256"]
        manifest = {
            "data_origin": "100% synthetic; assumed parameters; not an Enbridge asset",
            "scope": "Seed-held-out synthetic study; no field or independent-physics validation",
            "model": "Prescribed flow ramps and imbalances with steady pressure references; no transient solver or inventory compensation",
            "phase": args.phase, "study_config": study, "study_sha256": fingerprint(study),
            "rule_sha256": frozen["rule_sha256"], "frozen_rule": frozen,
            "seeds": frames["runs"]["seed"].tolist(),
        }
        export(frames, output, config, manifest)
        (output / "frozen_rule.json").write_text(json.dumps(frozen, indent=2), encoding="utf-8")
        (output / "experiment_report.md").write_text(report_text(frames, frozen), encoding="utf-8")
        return
    if args.phase or args.frozen_rule:
        parser.error("--phase and --frozen-rule require --study")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    frames = generate(config)
    settings = DetectionSettings(**config["detection"])
    frames["detector_results"] = detect(
        frames["readings"], settings,
        nominal_flow_m3h=config["pipeline"]["nominal_flow_m3h"],
        nominal_density_kgm3=config["pipeline"]["density_kgm3"],
    )
    frames.update(evaluate(frames["detector_results"], frames["runs"], frames["truth"]))
    export(frames, args.output or Path("data/generated"), config, {
        "scope": "Starter steady-state demo with sample-level detection and event evaluation; not held-out or field validation",
        "model": "Prescribed midpoint sink; fixed inlet flow and outlet pressure; no transient solver",
    })


def export(frames: dict, output: Path, config: dict, details: dict) -> None:
    """统一 CSV 精度、来源披露和导出清单。"""
    output.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        destination = output / f"{name}.csv"
        frame.to_csv(destination, index=False, float_format="%.9f")
        print(f"{name}: {len(frame)} rows -> {destination}")
    manifest = {
        "data_origin": "100% synthetic; assumed parameters; not an Enbridge asset",
        "evaluation_rule": (
            "One-to-one matching of new alarms within truth intervals; "
            "timely target at most 120 seconds after onset"
        ),
        "package_version": __version__,
        "config": config,
        "dependencies": {name: version(name) for name in ("numpy", "pandas", "fluids")},
        "rows": {name: len(frame) for name, frame in frames.items()},
        **details,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
