"""对已导出的完整回放重新检测、评价并核对报表，记录实际证据。"""

from argparse import ArgumentParser
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pandas as pd

from . import __version__
from .detector import DetectionSettings, REQUIRED_COLUMNS, detect
from .evaluation import evaluate
from .reporting import build_tables, load_study
from .simulate import generate
from .study import comparison_exposure, freeze_rule, summarize, validate_frozen_rule


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


def verify_all(generated: Path, report: Path) -> dict:
    """重新计算完整数据链，区分Python检查与后续Power BI应用证据。"""
    records = {"data_origin": "100% synthetic; assumed parameters; no field validation", "package_version": __version__,
               "scope": "Full CSV replay, event evaluation, frozen calibration and reporting reconciliation; Power BI application checks are recorded separately", "phases": {}}
    calibration = None
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
    print("Verifying exact frozen calibration regeneration", flush=True)
    normal_config = deepcopy(calibration["config"])
    normal_config["scenarios"] = [s for s in normal_config["scenarios"] if s["scenario"] == calibration["study_config"]["calibration"]["scenario"]]
    if freeze_rule(calibration["study_config"], generate(normal_config)) != calibration["frozen_rule"]:
        raise ValueError("Exact regenerated calibration evidence differs from the frozen rule.")
    print("Verifying report tables and weekly/monthly aggregation", flush=True)
    source, evidence = load_study(generated / "study")
    expected_tables = build_tables(source, evidence)
    manifest = json.loads((report / "manifest.json").read_text(encoding="utf-8"))
    if manifest["frozen_rule"] != evidence["frozen_rule"] or manifest["source_csv_sha256"] != evidence["source_csv_sha256"]:
        raise ValueError("Report provenance differs from the exact study exports.")
    for name, expected in expected_tables.items():
        path = report / "data" / f"{name}.csv"
        actual = pd.read_csv(path, low_memory=False)
        _assert_frame(actual, expected, "report/" + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["report_csv_sha256"][name]:
            raise ValueError("Report CSV hash mismatch: " + name)
    records.update({"status": "passed", "rule_sha256": evidence["frozen_rule"]["rule_sha256"],
                    "report_rows": manifest["rows"], "report_csv_sha256": manifest["report_csv_sha256"]})
    return records


def main() -> None:
    parser = ArgumentParser(description="Replay and verify the complete V1 synthetic exports and report inputs.")
    parser.add_argument("--generated", type=Path, default=Path("data/generated"))
    parser.add_argument("--report", type=Path, default=Path("reports/v1"))
    parser.add_argument("--output", type=Path, default=Path("reports/v1/verification/python_full_replay.json"))
    args = parser.parse_args()
    evidence = verify_all(args.generated, args.report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"PASS: complete V1 replay and report reconciliation -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
