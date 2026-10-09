from argparse import ArgumentParser
from importlib.metadata import version
import json
from pathlib import Path

from . import __version__
from .detector import DetectionSettings, detect
from .evaluation import evaluate
from .simulate import generate


def main() -> None:
    """生成合成测量、逐点判断及事件评价，并记录配置与依赖。"""
    parser = ArgumentParser(
        description="Generate synthetic pipeline data, alarms, and event-level metrics."
    )
    parser.add_argument("--config", type=Path, default=Path("configs/starter.json"))
    parser.add_argument("--output", type=Path, default=Path("data/generated"))
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    frames = generate(config)
    settings = DetectionSettings(**config["detection"])
    frames["detector_results"] = detect(
        frames["readings"], settings,
        nominal_flow_m3h=config["pipeline"]["nominal_flow_m3h"],
        nominal_density_kgm3=config["pipeline"]["density_kgm3"],
    )
    frames.update(evaluate(frames["detector_results"], frames["runs"], frames["truth"]))
    args.output.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        destination = args.output / f"{name}.csv"
        frame.to_csv(destination, index=False, float_format="%.9f")
        print(f"{name}: {len(frame)} rows -> {destination}")
    manifest = {
        "data_origin": "100% synthetic; assumed parameters; not an Enbridge asset",
        "scope": (
            "Starter steady-state demo with sample-level detection and event evaluation; "
            "not held-out or field validation"
        ),
        "evaluation_rule": (
            "One-to-one matching of new alarms within truth intervals; "
            "timely target at most 120 seconds after onset"
        ),
        "model": (
            "Prescribed midpoint sink; fixed inlet flow and outlet pressure; "
            "no transient solver"
        ),
        "package_version": __version__,
        "config": config,
        "dependencies": {name: version(name) for name in ("numpy", "pandas", "fluids")},
        "rows": {name: len(frame) for name, frame in frames.items()},
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
