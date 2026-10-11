"""先校准并冻结，再用独立种子评价；只汇总事件分子与时间分母。"""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import math

import pandas as pd

from . import __version__
from .alarms import EVALUABLE_STATES
from .detector import DetectionSettings, REQUIRED_COLUMNS, detect
from .evaluation import evaluate
from .simulate import generate, _window


def fingerprint(value: dict) -> str:
    """标准 JSON 哈希绑定规则与配置，不依赖键顺序。"""
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def expand_study(study: dict, split: str) -> dict:
    """先检查全部种子空间，再展开指定拆分；测试不复用校准噪声。"""
    if split not in {"calibration", "test"}:
        raise ValueError("Study split must be calibration or test.")
    scenario_names = [s["scenario"] for s in study["scenarios"]]
    profiles = study["noise_profiles"]
    profile_names = [p["name"] for p in profiles]
    for values in (scenario_names, profile_names):
        if not values or any(not isinstance(x, str) or not x.strip() or x == "__all__" for x in values) or len(set(values)) != len(values):
            raise ValueError("Study scenario and noise names must be nonempty and unique.")
    calibration = study["calibration"]
    if calibration["noise_profile"] not in profile_names or calibration["scenario"] not in scenario_names:
        raise ValueError("Calibration selection must refer to configured names.")
    normal = next(s for s in study["scenarios"] if s["scenario"] == calibration["scenario"])
    if normal["leak_fraction"] != 0 or normal.get("faults") or normal.get("operation"):
        raise ValueError("Calibration must use a predefined steady, fault-free, no-leak scenario.")
    sim = study["simulation"]
    _window(calibration["start_s"], calibration["end_s"], sim["interval_s"], sim["duration_s"])
    for name in ("threshold_floor_fraction_nominal", "std_multiplier"):
        value = calibration[name]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError("Calibration floor and multiplier must be positive and finite.")

    seed_sets = {}
    for name in ("calibration", "test"):
        settings = study["splits"][name]
        for key in ("seed_start", "replicates"):
            value = settings[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < (1 if key == "replicates" else 0):
                raise ValueError("Study seeds and replicate counts must be valid integers.")
        profile_count = 1 if name == "calibration" else len(profiles)
        count = len(scenario_names) * settings["replicates"] * profile_count
        seed_sets[name] = set(range(settings["seed_start"], settings["seed_start"] + count))
    if seed_sets["calibration"] & seed_sets["test"]:
        raise ValueError("Calibration and test seed ranges must not overlap.")

    selected_profiles = profiles if split == "test" else [
        p for p in profiles if p["name"] == calibration["noise_profile"]
    ]
    scenarios = []
    for profile in selected_profiles:
        for template in study["scenarios"]:
            for replicate in range(study["splits"][split]["replicates"]):
                scenario = deepcopy(template)
                scenario.update({
                    "run_id": f"{split}:{profile['name']}:{template['scenario']}:{replicate + 1:03d}",
                    "seed": study["splits"][split]["seed_start"] + len(scenarios),
                    "split": split, "noise_profile": profile["name"],
                    "meter_noise_std_fraction": profile["meter_noise_std_fraction"],
                    "noise_ar1": profile["noise_ar1"],
                })
                scenarios.append(scenario)
    return {"config_version": study["study_version"], "pipeline": study["pipeline"],
            "simulation": sim, "scenarios": scenarios}


def _rule_settings(study: dict, statistics: dict) -> DetectionSettings:
    """只根据正常校准残差均值与样本标准差定阈值，不搜索测试表现。"""
    calibration, pipeline = study["calibration"], study["pipeline"]
    nominal_mass = pipeline["density_kgm3"] * pipeline["nominal_flow_m3h"] / 3600
    threshold = statistics["residual_mean_kgs"] + max(
        nominal_mass * calibration["threshold_floor_fraction_nominal"],
        calibration["std_multiplier"] * statistics["residual_std_kgs"],
    )
    return DetectionSettings(threshold / nominal_mass, calibration["window_samples"],
                             calibration["confirmation_samples"],
                             f"{study['study_version']}-{fingerprint(study)[:12]}")


def freeze_rule(study: dict, frames: dict[str, pd.DataFrame]) -> dict:
    """冻结文件记录实际校准样本、统计值、版本与哈希。"""
    calibration = study["calibration"]
    runs = frames["runs"]
    if not runs["split"].eq("calibration").all():
        raise ValueError("Only calibration runs can freeze a rule.")
    selected_ids = runs.loc[
        runs["scenario"].eq(calibration["scenario"]) & runs["noise_profile"].eq(calibration["noise_profile"]), "run_id"
    ]
    readings = frames["readings"]
    selected = readings.loc[
        readings["run_id"].isin(selected_ids)
        & readings["elapsed_s"].ge(calibration["start_s"])
        & readings["elapsed_s"].lt(calibration["end_s"]), list(REQUIRED_COLUMNS)
    ].reset_index(drop=True)
    # 使用检测器本身检查仪表质量；正常校准窗若损坏就拒绝冻结。
    checked = detect(selected, DetectionSettings(0.01, 1, 1, "quality-check"),
                     study["pipeline"]["nominal_flow_m3h"], study["pipeline"]["density_kgm3"])
    instant = checked.loc[checked["method_version"] == "instant-quality-check"]
    expected = len(selected_ids) * (calibration["end_s"] - calibration["start_s"]) // study["simulation"]["interval_s"]
    if len(instant) != expected or len(instant) < 2 or not instant["quality_state"].eq("valid").all():
        raise ValueError("Calibration window must contain complete valid measurements.")
    statistics = {"sample_count": len(instant), "run_count": len(selected_ids),
                  "residual_mean_kgs": float(instant["residual_kgs"].mean()),
                  "residual_std_kgs": float(instant["residual_kgs"].std(ddof=1))}
    settings = _rule_settings(study, statistics)
    record = {"schema_version": 1, "package_version": __version__,
              "data_origin": "100% synthetic; assumed parameters; not field validation",
              "study_sha256": fingerprint(study),
              "calibration_selection": deepcopy(calibration),
              "calibration_seeds": sorted(runs.loc[runs["run_id"].isin(selected_ids), "seed"].tolist()),
              "calibration_measurements_sha256": hashlib.sha256(selected.to_csv(index=False, float_format="%.17g").encode()).hexdigest(),
              "statistics": statistics, "detection": asdict(settings),
              "threshold_formula": "mean + max(floor_fraction * nominal_mass_flow, std_multiplier * sample_std_ddof1)"}
    record["rule_sha256"] = fingerprint(record)
    return record


def validate_frozen_rule(study: dict, frozen: dict) -> DetectionSettings:
    """在生成任何测试数据前拒绝配置、规则或程序版本变更。"""
    record = {key: value for key, value in frozen.items() if key != "rule_sha256"}
    if frozen.get("rule_sha256") != fingerprint(record):
        raise ValueError("Frozen rule hash mismatch.")
    if frozen.get("study_sha256") != fingerprint(study) or frozen.get("package_version") != __version__:
        raise ValueError("Frozen rule requires the exact study and package version.")
    if frozen["detection"] != asdict(_rule_settings(study, frozen["statistics"])):
        raise ValueError("Frozen detection settings disagree with calibration statistics.")
    return DetectionSettings(**frozen["detection"])


def run_detection(frames: dict[str, pd.DataFrame], config: dict, settings: DetectionSettings) -> None:
    """检测只传允许的测量列，随后评价才读真值。"""
    pipeline = config["pipeline"]
    frames["detector_results"] = detect(frames["readings"].loc[:, REQUIRED_COLUMNS], settings,
                                         pipeline["nominal_flow_m3h"], pipeline["density_kgm3"])
    frames.update(evaluate(frames["detector_results"], frames["runs"], frames["truth"]))


def comparison_exposure(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """在两方法共同可评价的时段上另算误报频率，仍保留全时段结果。"""
    records = []
    for run_id, samples in frames["detector_results"].groupby("run_id", sort=False):
        eligible = samples.assign(evaluable=samples["alarm_state"].isin(EVALUABLE_STATES))
        common = eligible.groupby("elapsed_s")["evaluable"].all()
        grid = samples.drop_duplicates("elapsed_s").set_index("elapsed_s")
        common_grid = grid.loc[common[common].index]
        events = frames["truth"].loc[frames["truth"]["run_id"] == run_id]
        normal_s = 0.0
        for sample in common_grid.reset_index().itertuples(index=False):
            leak_s = sum(max(0, min(sample.elapsed_s + sample.interval_s, event.end_s) - max(sample.elapsed_s, event.onset_s))
                         for event in events.itertuples(index=False))
            normal_s += sample.interval_s - leak_s
        common_starts = set(common_grid.index)
        alarms = frames["alarms"].loc[frames["alarms"]["run_id"] == run_id]
        for method in samples["method_version"].unique():
            false_count = int((alarms["method_version"].eq(method) & alarms["classification"].eq("false_alarm")
                               & alarms["start_s"].isin(common_starts)).sum())
            records.append({"run_id": run_id, "method_version": method,
                            "common_evaluable_hours": common_grid["interval_s"].sum() / 3600,
                            "common_eligible_normal_hours": normal_s / 3600,
                            "false_alarm_events_common": false_count})
    return pd.DataFrame(records)


def summarize(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """先累计分子和分母，延迟分位数直接取匹配事件，不平均各回放比率。"""
    metadata = frames["runs"][["run_id", "scenario", "noise_profile"]]
    metrics = frames["run_metrics"].merge(metadata, on="run_id", validate="many_to_one").merge(
        frames["comparison_exposure"], on=["run_id", "method_version"], validate="one_to_one")
    count_columns = ["true_events", "detected_anytime", "detected_120s", "missed_anytime", "missed_120s",
                     "alarm_events", "false_alarm_events", "duplicate_during_leak_events", "open_alarm_events", "sample_count", "false_alarm_events_common"]
    hour_columns = ["scheduled_hours", "observed_hours", "missing_hours", "valid_hours", "evaluable_hours",
                    "eligible_normal_hours", "common_evaluable_hours", "common_eligible_normal_hours"]
    records = []
    for level, group_columns in (("scenario", ["split", "noise_profile", "scenario", "method_version"]),
                                  ("noise_profile", ["split", "noise_profile", "method_version"]),
                                  ("split", ["split", "method_version"])):
        for keys, group in metrics.groupby(group_columns, sort=False):
            labels = dict(zip(group_columns, keys))
            totals = group[count_columns + hour_columns].sum().to_dict()
            events = frames["event_evaluation"]
            selected = events.loc[events["run_id"].isin(group["run_id"]) & events["method_version"].eq(labels["method_version"])]
            delays = selected.loc[selected["detected_anytime"], "detection_delay_s"]
            def ratio(numerator: str, denominator: str, scale: float = 1.0) -> float:
                return totals[numerator] * scale / totals[denominator] if totals[denominator] else float("nan")
            records.append({"summary_level": level, "noise_profile": "__all__", "scenario": "__all__", **labels,
                            "run_count": group["run_id"].nunique(), **totals,
                            "detection_rate_120s": ratio("detected_120s", "true_events"),
                            "false_alarms_per_24h": ratio("false_alarm_events", "eligible_normal_hours", 24),
                            "false_alarms_per_24h_common": ratio("false_alarm_events_common", "common_eligible_normal_hours", 24),
                            "data_valid_fraction": ratio("valid_hours", "scheduled_hours"),
                            "evaluation_coverage": ratio("evaluable_hours", "scheduled_hours"),
                            "delay_sample_count": len(delays),
                            "delay_p50_s": delays.quantile(0.5) if len(delays) else float("nan"),
                            "delay_p95_s": delays.quantile(0.95) if len(delays) else float("nan"),
                            "mean_eligible_window_fraction": selected["eligible_window_fraction"].mean()})
    return pd.DataFrame(records)


def report_text(frames: dict[str, pd.DataFrame], frozen: dict) -> str:
    """生成可重算的实验说明；所有表旁保留样本数、分母与覆盖率。"""
    summary = frames["study_summary"]
    phase = frames["runs"]["split"].iloc[0]
    phase_scope = "Calibration results below are not held-out performance." if phase == "calibration" else "Results below use seed-held-out test runs with the previously frozen calibration rule."
    lines = [f"# Synthetic detector study — {phase}", "", "100% synthetic signals with assumed parameters; no field or independent-physics validation.", "",
             phase_scope, "",
             f"Package: {__version__}. Rule SHA256: `{frozen['rule_sha256']}`.",
             f"Study SHA256: `{frozen['study_sha256']}`.",
             f"Frozen threshold fraction: {frozen['detection']['threshold_fraction_nominal']:.9f}; {frozen['detection']['window_samples']}-sample causal averaging and {frozen['detection']['confirmation_samples']} confirmations.",
             f"Calibration: {frozen['statistics']['sample_count']} measurements in {frozen['statistics']['run_count']} predefined normal runs; sample standard deviation uses ddof=1.", "",
             "Seeds are disjoint. Test profiles use the same frozen rule. Only normal calibration measurements set the threshold; test truth never changes settings.", "",
             "Prescribed startup/shutdown ramps add a temporary flow imbalance. Pressure is a sequence of steady reference calculations, not a transient solution. Suspect stuck channels are externally flagged; automatic stuck-meter diagnosis is outside this study.", "",
             "Delays describe matched events only. Misses remain in the full truth denominator. Rates sum counts and exposure before division. Blank values indicate undefined metrics. Common-time false alarms use only starts during jointly evaluable samples.", "",
             "| Noise | Method | Runs | Truth | Detected ≤120 s | Missed anytime /120 s | False alarms | No-leak evaluable h | FA/24 h | Coverage | Delay n / P50 / P95 s |",
             "| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |"]
    for row in summary.loc[summary["summary_level"] == "noise_profile"].itertuples():
        lines.append(f"| {row.noise_profile} | {row.method_version} | {row.run_count} | {row.true_events:.0f} | {row.detected_120s:.0f} | {row.missed_anytime:.0f} / {row.missed_120s:.0f} | {row.false_alarm_events:.0f} | {row.eligible_normal_hours:.3f} | {row.false_alarms_per_24h:.3f} | {row.evaluation_coverage:.3%} | {row.delay_sample_count} / {row.delay_p50_s:.1f} / {row.delay_p95_s:.1f} |")
    selected_profile = frozen["calibration_selection"]["noise_profile"]
    lines += ["", f"## Scenario detail — {selected_profile}", "",
              "These rows retain the evaluation phase named above. Method names below abbreviate the same frozen versions shown above.", "",
              "| Scenario | Method | Truth | Detected ≤120 s | Missed anytime /120 s | False alarms | No-leak evaluable h | Coverage | Delay n / P50 s |",
              "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- |"]
    for row in summary.loc[summary["summary_level"].eq("scenario") & summary["noise_profile"].eq(selected_profile)].itertuples():
        method = row.method_version.split("-", 1)[0]
        median = f"{row.delay_p50_s:.1f}" if pd.notna(row.delay_p50_s) else "undefined"
        lines.append(f"| {row.scenario} | {method} | {row.true_events:.0f} | {row.detected_120s:.0f} | {row.missed_anytime:.0f} / {row.missed_120s:.0f} | {row.false_alarm_events:.0f} | {row.eligible_normal_hours:.3f} | {row.evaluation_coverage:.3%} | {row.delay_sample_count} / {median} |")
    lines += ["", "Scenario rows in study_summary.csv separate small leaks, spikes, persistent bias, operating ramps and data loss. Inspect event_evaluation.csv for pre-existing alarms and eligible detection windows; inspect comparison_exposure.csv for common-time denominators.", "",
              "The predefined synthetic scenario mix determines pooled rates. Seed separation tests new noise realizations under this generator; it does not establish transfer to operating pipelines.", ""]
    return "\n".join(lines)
