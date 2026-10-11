"""验证困难场景、校准隔离、冻结边界与事件汇总分母。"""

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from pipeline_leak_detection.detector import DetectionSettings, REQUIRED_COLUMNS, detect
from pipeline_leak_detection.simulate import generate
from pipeline_leak_detection.study import (
    comparison_exposure, expand_study, fingerprint, freeze_rule,
    run_detection, summarize, validate_frozen_rule,
)


@pytest.fixture
def study():
    config = json.loads((Path(__file__).parents[1] / "configs/study.json").read_text(encoding="utf-8"))
    config["splits"]["calibration"]["replicates"] = 1
    config["splits"]["test"]["replicates"] = 1
    return config


@pytest.fixture
def deterministic(study):
    study["noise_profiles"] = [{"name": "base_white", "meter_noise_std_fraction": 0.0, "noise_ar1": 0.0}]
    config = expand_study(study, "test")
    frames = generate(config)
    run_detection(frames, config, DetectionSettings(0.01, 6, 3, "check"))
    return frames


def test_difficult_scenarios_expose_success_and_failure(deterministic):
    metrics = deterministic["run_metrics"].merge(deterministic["runs"][["run_id", "scenario"]], on="run_id")
    def row(scenario, method):
        return metrics.loc[metrics["scenario"].eq(scenario) & metrics["method_version"].eq(method)].iloc[0]
    assert row("spike", "instant-check")["false_alarm_events"] == 1
    assert row("spike", "trailing-check")["false_alarm_events"] == 0
    for method in ("instant-check", "trailing-check"):
        assert row("small_leak", method)["missed_anytime"] == 1
        assert row("meter_bias", method)["false_alarm_events"] == 1
        assert row("startup_shutdown", method)["false_alarm_events"] == 1
        assert row("leak_2pct", method)["detected_120s"] == 1
        assert row("leak_5pct", method)["detected_120s"] == 1
        assert row("leak_data_loss", method)["missed_120s"] == 1
        assert row("leak_data_loss", method)["detected_anytime"] == 1
        assert row("leak_data_loss", method)["evaluation_coverage"] < 1
    # 即时法的启停报警早于420秒泄漏，不能把旧报警当检出。
    assert row("startup_leak", "instant-check")["missed_anytime"] == 1


def test_fault_grain_truth_separation_and_mass_conservation(deterministic):
    readings, runs = deterministic["readings"], deterministic["runs"]
    assert not {"scenario", "split", "leak_fraction", "true_leak_m3h", "noise_profile"} & set(readings.columns)
    missing_id = runs.loc[runs["scenario"].eq("missing_measurement"), "run_id"].iloc[0]
    missing = readings.loc[readings["run_id"].eq(missing_id) & readings["elapsed_s"].between(1190, 1490)]
    assert len(missing) == 31 and missing["qout_m3h"].isna().all()
    assert missing["instrument_status"].eq("missing").all()
    stuck_id = runs.loc[runs["scenario"].eq("suspect_stuck"), "run_id"].iloc[0]
    stuck = readings.loc[readings["run_id"].eq(stuck_id) & readings["elapsed_s"].between(1190, 1490)]
    assert stuck["qout_m3h"].nunique() == 1 and stuck["instrument_status"].eq("suspect").all()
    dropout_id = runs.loc[runs["scenario"].eq("dropped_samples"), "run_id"].iloc[0]
    assert len(readings.loc[readings["run_id"].eq(dropout_id)]) == 689
    metrics = deterministic["run_metrics"].loc[deterministic["run_metrics"]["run_id"].eq(dropout_id)]
    assert metrics["missing_hours"].tolist() == pytest.approx([310 / 3600] * 2)
    startup_id = runs.loc[runs["scenario"].eq("startup_leak"), "run_id"].iloc[0]
    sample = readings.loc[readings["run_id"].eq(startup_id) & readings["elapsed_s"].eq(420)].iloc[0]
    # 指定库存扰动只作为信号：320×3% + 16，不能解释成25.6的真实漏率。
    assert sample["qin_m3h"] - sample["qout_m3h"] == pytest.approx(25.6)
    assert sample["operating_state"] == "transient"
    truth = deterministic["truth"].loc[deterministic["truth"]["run_id"].eq(startup_id)].iloc[0]
    assert truth["true_leak_m3h"] == 16
    assert truth["true_release_m3"] == pytest.approx(16 * (6000 - 420) / 3600)


def test_seed_splits_profiles_and_reproducibility(study):
    calibration = expand_study(study, "calibration")
    test = expand_study(study, "test")
    calibration_seeds = {s["seed"] for s in calibration["scenarios"]}
    test_seeds = [s["seed"] for s in test["scenarios"]]
    assert not calibration_seeds.intersection(test_seeds)
    assert len(test_seeds) == len(set(test_seeds)) == 36
    assert len(calibration_seeds) == 12
    first, second = generate(test), generate(deepcopy(test))
    for name in first:
        pd.testing.assert_frame_equal(first[name], second[name])


def test_ar1_noise_is_stationary_and_causal(study):
    config = expand_study(study, "test")
    config["scenarios"] = [s for s in config["scenarios"] if s["scenario"] == "normal"]
    frames = generate(config)
    for scenario in config["scenarios"]:
        readings = frames["readings"].loc[frames["readings"]["run_id"].eq(scenario["run_id"])]
        measured_correlation = readings["qin_m3h"].autocorr(lag=1)
        assert abs(measured_correlation - scenario["noise_ar1"]) < 0.12
    settings = DetectionSettings(0.01, 6, 3, "causal")
    readings = frames["readings"].loc[frames["readings"]["run_id"].eq(config["scenarios"][-1]["run_id"])]
    complete = detect(readings.loc[:, REQUIRED_COLUMNS], settings, 800, 850)
    prefix = detect(readings.iloc[:200].assign(scenario="leak", true_leak_m3h=999), settings, 800, 850)
    pd.testing.assert_frame_equal(prefix, complete.iloc[:400].reset_index(drop=True))


def test_only_normal_calibration_measurements_set_threshold(study):
    frames = generate(expand_study(study, "calibration"))
    frozen = freeze_rule(study, frames)
    normal_ids = set(frames["runs"].loc[frames["runs"]["scenario"].eq("normal"), "run_id"])
    modified = {name: value.copy() for name, value in frames.items()}
    modified["readings"].loc[~modified["readings"]["run_id"].isin(normal_ids), "qin_m3h"] = 999999
    modified["truth"]["true_leak_m3h"] = 99999
    assert freeze_rule(study, modified) == frozen
    assert frozen["statistics"]["sample_count"] == 720
    normal = frames["readings"].loc[frames["readings"]["run_id"].isin(normal_ids)]
    residual = 850 * (normal["qin_m3h"] - normal["qout_m3h"]) / 3600
    threshold = residual.mean() + max(850 * 800 * 0.01 / 3600, 4 * residual.std(ddof=1))
    assert frozen["detection"]["threshold_fraction_nominal"] == pytest.approx(threshold / (850 * 800 / 3600))
    settings = validate_frozen_rule(study, frozen)
    assert asdict(settings) == frozen["detection"]
    with pytest.raises(ValueError, match="Only calibration"):
        freeze_rule(study, generate(expand_study(study, "test")))


@pytest.mark.parametrize("change", ["settings", "config", "version", "statistics"])
def test_frozen_rule_rejects_drift_before_test(study, change):
    frozen = freeze_rule(study, generate(expand_study(study, "calibration")))
    if change == "settings":
        frozen["detection"]["threshold_fraction_nominal"] = 0.05
    elif change == "config":
        study["noise_profiles"][1]["meter_noise_std_fraction"] = 0.03
    elif change == "version":
        frozen["package_version"] = "other"
        frozen["rule_sha256"] = fingerprint({key: value for key, value in frozen.items() if key != "rule_sha256"})
    else:
        frozen["statistics"]["residual_std_kgs"] *= 2
        frozen["rule_sha256"] = fingerprint({key: value for key, value in frozen.items() if key != "rule_sha256"})
    with pytest.raises(ValueError, match="Frozen"):
        validate_frozen_rule(study, frozen)


@pytest.mark.parametrize("change", ["overlap", "duplicate_name", "leaky_calibration", "faulty_calibration", "empty_replicates"])
def test_invalid_study_design_is_rejected(study, change):
    if change == "overlap":
        study["splits"]["test"]["seed_start"] = 10000
    elif change == "duplicate_name":
        study["scenarios"][1]["scenario"] = "normal"
    elif change == "leaky_calibration":
        study["scenarios"][0]["leak_fraction"] = 0.02
    elif change == "faulty_calibration":
        study["scenarios"][0]["faults"] = study["scenarios"][1]["faults"]
    else:
        study["splits"]["test"]["replicates"] = 0
    with pytest.raises(ValueError):
        expand_study(study, "test")


@pytest.mark.parametrize("change", ["unknown_fault", "overlap", "unaligned", "bad_ar1", "nonfinite_bias"])
def test_invalid_signal_injections_are_rejected(study, change):
    config = expand_study(study, "test")
    scenario = config["scenarios"][1]
    if change == "unknown_fault":
        scenario["faults"][0]["kind"] = "mystery"
    elif change == "overlap":
        scenario["faults"].append(deepcopy(scenario["faults"][0]))
    elif change == "unaligned":
        scenario["faults"][0]["end_s"] = 1211
    elif change == "bad_ar1":
        scenario["noise_ar1"] = 1
    else:
        scenario["faults"][0]["offset_m3h"] = float("inf")
    with pytest.raises(ValueError):
        generate(config)


def test_summary_sums_exposures_and_keeps_misses(deterministic):
    frames = deterministic
    frames["comparison_exposure"] = comparison_exposure(frames)
    summary = summarize(frames)
    for method, group in frames["run_metrics"].groupby("method_version"):
        row = summary.loc[summary["summary_level"].eq("split") & summary["method_version"].eq(method)].iloc[0]
        assert row["run_count"] == 12
        assert row["true_events"] == 5
        assert row["false_alarms_per_24h"] == pytest.approx(group["false_alarm_events"].sum() * 24 / group["eligible_normal_hours"].sum())
        assert row["evaluation_coverage"] == pytest.approx(group["evaluable_hours"].sum() / group["scheduled_hours"].sum())
        events = frames["event_evaluation"].loc[frames["event_evaluation"]["method_version"].eq(method)]
        delays = events.loc[events["detected_anytime"], "detection_delay_s"]
        assert row["delay_sample_count"] == len(delays)
        assert row["delay_p95_s"] == pytest.approx(np.quantile(delays, 0.95))
        assert row["missed_anytime"] > 0
    spike = summary.loc[summary["scenario"].eq("spike")]
    assert spike["detection_rate_120s"].isna().all()
    small = summary.loc[summary["scenario"].eq("small_leak")]
    assert small["delay_p50_s"].isna().all()
    # 各方法共同评价分母完全相同，且不会大于各自全部可评价分母。
    common = frames["comparison_exposure"]
    assert common.groupby("run_id")["common_eligible_normal_hours"].nunique().eq(1).all()
    joined = frames["run_metrics"].merge(common, on=["run_id", "method_version"])
    assert (joined["common_evaluable_hours"] <= joined["evaluable_hours"] + 1e-10).all()
    assert (joined["false_alarm_events_common"] <= joined["false_alarm_events"]).all()


def test_summary_zero_exposure_is_undefined(study):
    config = expand_study(study, "test")
    config["scenarios"] = config["scenarios"][:1]
    frames = generate(config)
    frames["readings"]["qout_m3h"] = float("nan")
    run_detection(frames, config, DetectionSettings(0.01, 6, 3, "unavailable"))
    frames["comparison_exposure"] = comparison_exposure(frames)
    summary = summarize(frames)
    assert summary["evaluation_coverage"].eq(0).all()
    assert summary["false_alarms_per_24h"].isna().all()
    assert summary["false_alarms_per_24h_common"].isna().all()
    assert summary["detection_rate_120s"].isna().all()
    assert summary["delay_sample_count"].eq(0).all()


def test_calibration_rejects_missing_or_invalid_normal_window(study):
    frames = generate(expand_study(study, "calibration"))
    broken = {name: value.copy() for name, value in frames.items()}
    broken["readings"].loc[0, "qout_m3h"] = float("nan")
    with pytest.raises(ValueError, match="complete valid"):
        freeze_rule(study, broken)
    frames["readings"] = frames["readings"].iloc[1:]
    with pytest.raises(ValueError, match="complete valid"):
        freeze_rule(study, frames)


def test_cli_validates_frozen_rule_before_generation(study, tmp_path, monkeypatch):
    from pipeline_leak_detection import cli
    frozen = freeze_rule(study, generate(expand_study(study, "calibration")))
    frozen["detection"]["confirmation_samples"] = 1
    config_path, rule_path = tmp_path / "study.json", tmp_path / "rule.json"
    config_path.write_text(json.dumps(study), encoding="utf-8")
    rule_path.write_text(json.dumps(frozen), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["pipeline-leak", "--study", str(config_path), "--phase", "test", "--frozen-rule", str(rule_path)])
    def forbidden_generation(config):
        pytest.fail("Test data generation happened before frozen-rule validation")
    monkeypatch.setattr(cli, "generate", forbidden_generation)
    with pytest.raises(ValueError, match="hash mismatch"):
        cli.main()


def test_cli_calibration_then_test_and_frozen_provenance(study, tmp_path):
    study["noise_profiles"] = study["noise_profiles"][:1]
    path = tmp_path / "study.json"
    path.write_text(json.dumps(study), encoding="utf-8")
    root = Path(__file__).parents[1]
    command = [sys.executable, "-m", "pipeline_leak_detection", "--study", str(path)]
    failed = subprocess.run(command + ["--phase", "test", "--output", str(tmp_path / "rejected")], cwd=root, capture_output=True, text=True)
    assert failed.returncode != 0 and not (tmp_path / "rejected").exists()
    generated = tmp_path / "generated"
    calibration_dir, test_dir = generated / "study/calibration", generated / "study/test"
    subprocess.run(command + ["--phase", "calibration", "--output", str(calibration_dir)], cwd=root, check=True, capture_output=True)
    rule_path = calibration_dir / "frozen_rule.json"
    frozen = json.loads(rule_path.read_text())
    subprocess.run(command + ["--phase", "test", "--frozen-rule", str(rule_path), "--output", str(test_dir)], cwd=root, check=True, capture_output=True)
    manifest = json.loads((test_dir / "manifest.json").read_text())
    assert manifest["frozen_rule"] == frozen
    assert "100% synthetic" in manifest["data_origin"]
    assert (test_dir / "frozen_rule.json").read_bytes() == rule_path.read_bytes()
    for name, count in manifest["rows"].items():
        assert len(pd.read_csv(test_dir / f"{name}.csv")) == count
    calibration_runs = pd.read_csv(calibration_dir / "runs.csv")
    test_runs = pd.read_csv(test_dir / "runs.csv")
    assert not set(calibration_runs["seed"]) & set(test_runs["seed"])
    assert test_runs["rule_sha256"].eq(frozen["rule_sha256"]).all()
    assert "100% synthetic" in (test_dir / "experiment_report.md").read_text(encoding="utf-8")
    assert "seed-held-out test runs" in (test_dir / "experiment_report.md").read_text(encoding="utf-8")
    assert "not held-out performance" in (calibration_dir / "experiment_report.md").read_text(encoding="utf-8")
    subprocess.run([sys.executable, "-m", "pipeline_leak_detection", "--config", str(root / "configs/starter.json"), "--output", str(generated)], cwd=root, check=True, capture_output=True)
    verification_path = tmp_path / "verification.json"
    subprocess.run([sys.executable, str(root / "scripts/verify_study_exports.py"), "--generated", str(generated), "--output", str(verification_path)], cwd=root, check=True, capture_output=True)
    verification = json.loads(verification_path.read_text())
    assert verification["status"] == "passed"
    assert verification["rule_sha256"] == frozen["rule_sha256"]
    assert verification["phases"]["starter"]["rows"]["readings"] == 2160
    # 真实导出被篡改时，命令行核对必须失败，不能生成通过记录。
    detector_path = generated / "detector_results.csv"
    detector = pd.read_csv(detector_path)
    detector.loc[0, "residual_kgs"] += 1
    detector.to_csv(detector_path, index=False)
    rejected_path = tmp_path / "rejected_verification.json"
    failed = subprocess.run([sys.executable, str(root / "scripts/verify_study_exports.py"), "--generated", str(generated), "--output", str(rejected_path)], cwd=root, capture_output=True)
    assert failed.returncode != 0 and not rejected_path.exists()
