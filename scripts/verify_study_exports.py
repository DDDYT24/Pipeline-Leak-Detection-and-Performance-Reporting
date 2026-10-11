"""对全部合成导出重新检测和评价，并复算冻结校准证据。"""

from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pandas as pd

from pipeline_leak_detection import __version__
from pipeline_leak_detection.detector import DetectionSettings, REQUIRED_COLUMNS, detect
from pipeline_leak_detection.evaluation import evaluate
from pipeline_leak_detection.simulate import generate
from pipeline_leak_detection.study import comparison_exposure, freeze_rule, summarize, validate_frozen_rule


def _assert_frame(actual: pd.DataFrame, expected: pd.DataFrame, label: str) -> None:
    """允许CSV九位小数舍入，但状态、计数与标识必须一致。"""
    actual, expected = actual.copy(), expected.copy()
    for column in expected:
        if pd.api.types.is_string_dtype(expected[column]) or pd.api.types.is_object_dtype(expected[column]):
            actual[column] = actual[column].fillna("")
            expected[column] = expected[column].fillna("")
    try:
        pd.testing.assert_frame_equal(actual[expected.columns].reset_index(drop=True), expected.reset_index(drop=True),
                                      check_dtype=False, check_exact=False, rtol=1e-7, atol=1e-8)
    except AssertionError as exc:
        raise AssertionError(f"Verification failed: {label}\n{exc}") from exc


def verify_all(generated: Path) -> dict:
    """重新计算完整数据链，不包含报表应用验证。"""
    records = {"data_origin": "100% synthetic; assumed parameters; no field validation", "package_version": __version__,
               "scope": "Complete starter/calibration/test CSV replay, event evaluation and exact frozen calibration regeneration; no report-application validation", "phases": {}}
    calibration = None
    split_seeds = {}
    for phase, folder in (("starter", generated), ("calibration", generated / "study/calibration"), ("test", generated / "study/test")):
        print(f"Verifying {phase}: reading exports and replaying every measurement", flush=True)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if manifest["package_version"] != __version__:
            raise ValueError("Generated output package version differs from verification version.")
        frames = {name: pd.read_csv(folder / f"{name}.csv", low_memory=False) for name in manifest["rows"]}
        for name, frame in frames.items():
            if len(frame) != manifest["rows"][name]:
                raise ValueError(f"Manifest row count mismatch: {phase}/{name}.")
        for name, key in (("readings", ["run_id", "sample_time_utc"]), ("runs", ["run_id"]),
                          ("truth", ["truth_event_id"]), ("detector_results", ["run_id", "method_version", "elapsed_s"]),
                          ("alarms", ["alarm_id"]), ("event_evaluation", ["truth_event_id", "method_version"]),
                          ("run_metrics", ["run_id", "method_version"])):
            if frames[name].duplicated(key).any():
                raise ValueError(f"Duplicate export keys: {phase}/{name}.")
        if {"true_leak_m3h", "scenario", "split", "leak_fraction", "noise_profile"}.intersection(frames["readings"].columns):
            raise ValueError("Measurement exports must remain separate from labels and truth.")
        config = manifest["config"]
        if phase == "starter":
            settings = DetectionSettings(**config["detection"])
        else:
            settings = validate_frozen_rule(manifest["study_config"], manifest["frozen_rule"])
            if json.loads((folder / "frozen_rule.json").read_text(encoding="utf-8")) != manifest["frozen_rule"]:
                raise ValueError("Standalone frozen rule differs from the manifest.")
            seeds = frames["runs"]["seed"]
            if not seeds.is_unique:
                raise ValueError("Duplicate seeds within study split.")
            split_seeds[phase] = set(seeds)
            rule_hash = manifest["frozen_rule"]["rule_sha256"]
            for table in ("runs", "study_summary"):
                if not frames[table]["rule_sha256"].eq(rule_hash).all():
                    raise ValueError("Exported rule identity differs from the frozen record.")
            if phase == "calibration":
                calibration = manifest
        replay = detect(frames["readings"].loc[:, REQUIRED_COLUMNS], settings,
                        config["pipeline"]["nominal_flow_m3h"], config["pipeline"]["density_kgm3"])
        _assert_frame(frames["detector_results"], replay, phase + "/detector_results")
        evaluated = evaluate(replay, frames["runs"], frames["truth"])
        for name, frame in evaluated.items():
            _assert_frame(frames[name], frame, phase + "/" + name)
        if phase != "starter":
            recomputed = {"runs": frames["runs"], "truth": frames["truth"], "detector_results": replay, **evaluated}
            recomputed["comparison_exposure"] = comparison_exposure(recomputed)
            _assert_frame(frames["comparison_exposure"], recomputed["comparison_exposure"], phase + "/comparison_exposure")
            result = summarize(recomputed)
            _assert_frame(frames["study_summary"].drop(columns="rule_sha256"), result, phase + "/study_summary")
        records["phases"][phase] = {"rows": manifest["rows"], "status": "passed", "csv_sha256": {name: hashlib.sha256((folder / f"{name}.csv").read_bytes()).hexdigest() for name in frames}}
        print(f"{phase}: detector states, one-to-one matches, misses, exposure and summaries passed", flush=True)
    if split_seeds["calibration"] & split_seeds["test"]:
        raise ValueError("Exported calibration/test seeds overlap.")
    print("Verifying exact frozen calibration regeneration", flush=True)
    normal_config = deepcopy(calibration["config"])
    normal_config["scenarios"] = [s for s in normal_config["scenarios"] if s["scenario"] == calibration["study_config"]["calibration"]["scenario"]]
    if freeze_rule(calibration["study_config"], generate(normal_config)) != calibration["frozen_rule"]:
        raise ValueError("Exact regenerated calibration evidence differs from the frozen rule.")
    records.update({"status": "passed", "rule_sha256": calibration["frozen_rule"]["rule_sha256"]})
    return records


def main() -> None:
    parser = ArgumentParser(description="Replay and verify all Stage 1-5 synthetic exports and frozen calibration inputs.")
    parser.add_argument("--generated", type=Path, default=Path("data/generated"))
    parser.add_argument("--output", type=Path, default=Path("data/generated/study/verification.json"))
    args = parser.parse_args()
    evidence = verify_all(args.generated)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"PASS: complete Stage 1-5 CSV replay and frozen calibration verification -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
