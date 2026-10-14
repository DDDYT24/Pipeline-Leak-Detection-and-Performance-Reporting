"""生成离线报表接口、模拟周月汇总和可复核的报告。"""

from argparse import ArgumentParser
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import __version__
from .study import validate_frozen_rule


DISCLOSURE = "100% synthetic; assumed parameters; no field validation"
REPORT_START = date(2026, 9, 1)
BASE_TABLES = ("readings", "runs", "truth", "detector_results", "alarms", "event_evaluation", "run_metrics", "comparison_exposure")
COUNTS = ("true_events", "detected_anytime", "detected_120s", "missed_anytime", "missed_120s", "alarm_events", "false_alarm_events", "duplicate_during_leak_events", "open_alarm_events", "sample_count", "false_alarm_events_common")
HOURS = ("scheduled_hours", "observed_hours", "missing_hours", "valid_hours", "evaluable_hours", "eligible_normal_hours", "common_evaluable_hours", "common_eligible_normal_hours")


def load_study(directory: Path) -> tuple[dict[str, pd.DataFrame], dict]:
    """检查两个阶段的清单、唯一键与规则一致后，读取原始评价结果。"""
    collections = {name: [] for name in BASE_TABLES}
    sources = {}
    rules = []
    for split in ("calibration", "test"):
        folder = directory / split
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        rule = json.loads((folder / "frozen_rule.json").read_text(encoding="utf-8"))
        validate_frozen_rule(manifest["study_config"], rule)
        if manifest["phase"] != split or manifest["rule_sha256"] != rule["rule_sha256"]:
            raise ValueError("Study phase or frozen-rule manifest mismatch.")
        rules.append(rule)
        sources[split] = {}
        for name in BASE_TABLES:
            path = folder / f"{name}.csv"
            frame = pd.read_csv(path, low_memory=False)
            if len(frame) != manifest["rows"][name]:
                raise ValueError(f"Export row count mismatch: {split}/{name}.")
            collections[name].append(frame)
            sources[split][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if rules[0] != rules[1]:
        raise ValueError("Calibration and test must use the same frozen rule.")
    frames = {name: pd.concat(parts, ignore_index=True) for name, parts in collections.items()}
    runs = frames["runs"]
    if runs["run_id"].duplicated().any() or runs["seed"].duplicated().any():
        raise ValueError("Report runs and split seeds must be unique.")
    if set(runs["split"]) != {"calibration", "test"}:
        raise ValueError("Report input split labels are inconsistent.")
    for name, key in (("readings", ["run_id", "elapsed_s"]), ("truth", ["truth_event_id"]),
                      ("detector_results", ["run_id", "elapsed_s", "method_version"]),
                      ("alarms", ["alarm_id"]), ("event_evaluation", ["truth_event_id", "method_version"]),
                      ("run_metrics", ["run_id", "method_version"]), ("comparison_exposure", ["run_id", "method_version"])):
        if frames[name].duplicated(key).any() or not set(frames[name]["run_id"]).issubset(set(runs["run_id"])):
            raise ValueError(f"Report input has duplicate keys or unknown runs: {name}.")
    if not runs["rule_sha256"].eq(rules[0]["rule_sha256"]).all():
        raise ValueError("Run metadata is not bound to the frozen rule.")
    return frames, {"data_origin": DISCLOSURE, "frozen_rule": rules[0], "source_csv_sha256": sources}


def reporting_calendar(runs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """仅映射展示日期，均衡分配到四个七天研究周；保留原采样时间。"""
    result = runs.copy()
    profiles = {name: index for index, name in enumerate(("base_white", "high_white", "base_ar1"))}
    offsets = []
    for run in result.itertuples(index=False):
        replicate = int(run.run_id.rsplit(":", 1)[-1])
        offsets.append(((replicate - 1) * 3 + profiles.get(run.noise_profile, 0)) % 28)
    result["report_date"] = [(REPORT_START + timedelta(days=n)).isoformat() for n in offsets]
    review_order = {name: index for index, name in enumerate(("small_leak", "spike", "meter_bias", "startup_shutdown", "startup_leak", "leak_data_loss", "leak_2pct", "leak_5pct", "missing_measurement", "suspect_stuck", "dropped_samples", "normal"))}
    result["scenario_order"] = result["scenario"].map(review_order).fillna(99).astype(int)
    dates = pd.DataFrame([{
        "report_date": (REPORT_START + timedelta(days=n)).isoformat(),
        "week_start": (REPORT_START + timedelta(days=(n // 7) * 7)).isoformat(),
        "week_label": f"Study week {n // 7 + 1}", "week_order": n // 7 + 1,
        "month_start": REPORT_START.isoformat(), "month_label": "2026-09",
        "data_origin": DISCLOSURE,
    } for n in range(28)])
    return result, dates


def aggregate_metrics(metrics: pd.DataFrame, events: pd.DataFrame) -> dict:
    """用原始计数和小时数计算比率，匹配事件直接计算延迟分布。"""
    totals = metrics[list(COUNTS) + list(HOURS)].sum().to_dict()
    def ratio(numerator, denominator, scale=1):
        return totals[numerator] * scale / totals[denominator] if totals[denominator] else float("nan")
    delays = events.loc[events["detected_anytime"], "detection_delay_s"]
    return {"run_count": metrics["run_id"].nunique(), **totals,
            "detection_rate_120s": ratio("detected_120s", "true_events"),
            "false_alarms_per_24h": ratio("false_alarm_events", "eligible_normal_hours", 24),
            "false_alarms_per_24h_common": ratio("false_alarm_events_common", "common_eligible_normal_hours", 24),
            "data_valid_fraction": ratio("valid_hours", "scheduled_hours"),
            "evaluation_coverage": ratio("evaluable_hours", "scheduled_hours"),
            "delay_sample_count": len(delays),
            "delay_p50_s": delays.quantile(.5) if len(delays) else float("nan"),
            "delay_p95_s": delays.quantile(.95) if len(delays) else float("nan")}


def period_summaries(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """保留周/月、拆分、噪声和场景粒度，不用周百分比或P95求月均值。"""
    metadata = tables["Runs"][["run_id", "split", "noise_profile", "scenario", "report_date"]].merge(tables["Dates"], on="report_date", validate="many_to_one")
    metrics = tables["Metrics"].drop(columns=["split", "synthetic_test_date"], errors="ignore").merge(metadata, on="run_id", validate="many_to_one")
    records = []
    for period_type, start_column, label_column in (("weekly", "week_start", "week_label"), ("monthly", "month_start", "month_label")):
        for level, dimensions in (("scenario", ["noise_profile", "scenario"]), ("noise_profile", ["noise_profile"]), ("split", [])):
            group_columns = ["split", start_column, label_column, "method_version"] + dimensions
            for keys, group in metrics.groupby(group_columns, sort=True):
                labels = dict(zip(group_columns, keys))
                selected = tables["Events"].loc[tables["Events"]["run_id"].isin(group["run_id"]) & tables["Events"]["method_version"].eq(labels["method_version"])]
                records.append({"period_type": period_type, "period_start": labels.pop(start_column),
                                "period_label": labels.pop(label_column), "summary_level": level,
                                "noise_profile": "__all__", "scenario": "__all__", **labels,
                                **aggregate_metrics(group, selected), "data_origin": DISCLOSURE})
    return pd.DataFrame(records)


def review_samples(frames: dict[str, pd.DataFrame], runs: pd.DataFrame) -> pd.DataFrame:
    """展示层补齐缺测行；这些行不进入检测或性能分母。"""
    measurement_columns = ["run_id", "elapsed_s", "qin_m3h", "qout_m3h", "pin_pa", "pout_pa", "operating_state"]
    source = frames["detector_results"].merge(frames["readings"][measurement_columns], on=["run_id", "elapsed_s"], validate="many_to_one")
    records = []
    truth = frames["truth"].groupby("run_id")
    for run in runs.itertuples(index=False):
        samples = source.loc[source["run_id"].eq(run.run_id)]
        interval = int(samples["interval_s"].iloc[0])
        methods = samples["method_version"].unique()
        grid = pd.MultiIndex.from_product([methods, range(0, round(run.scheduled_hours * 3600), interval)], names=["method_version", "elapsed_s"])
        expanded = samples.set_index(["method_version", "elapsed_s"]).reindex(grid).reset_index()
        absent = expanded["run_id"].isna()
        expanded["run_id"] = run.run_id
        expanded["display_row_origin"] = np.where(absent, "missing_interval_display_only", "observed_measurement")
        expanded.loc[absent, "alarm_state"] = "unavailable"
        expanded.loc[absent, "quality_state"] = "invalid"
        expanded.loc[absent, "invalid_reason"] = "dropped_sample"
        expanded["truth_state"] = "no_leak"
        if run.run_id in truth.groups:
            for event in truth.get_group(run.run_id).itertuples(index=False):
                expanded.loc[expanded["elapsed_s"].ge(event.onset_s) & expanded["elapsed_s"].lt(event.end_s), "truth_state"] = "synthetic_leak"
        # Pa 保留在CSV中，MPa只是Python计算的展示列。
        expanded["pin_mpa"] = expanded["pin_pa"] / 1e6
        expanded["pout_mpa"] = expanded["pout_pa"] / 1e6
        expanded["monitoring_available"] = expanded["alarm_state"].isin(["normal", "pending", "alarm"]).astype(int)
        columns = ["run_id", "method_version", "elapsed_s", "qin_m3h", "qout_m3h", "pin_pa", "pout_pa", "pin_mpa", "pout_mpa", "residual_kgs", "smoothed_residual_kgs", "threshold_kgs", "alarm_state", "alarm_latched", "quality_state", "invalid_reason", "operating_state", "truth_state", "monitoring_available", "display_row_origin"]
        records.append(expanded[columns])
    return pd.concat(records, ignore_index=True)


def build_tables(frames: dict[str, pd.DataFrame], evidence: dict) -> dict[str, pd.DataFrame]:
    """用共享run/方法/日期维度形成一对多报表接口，不重复汇总真值。"""
    runs, dates = reporting_calendar(frames["runs"])
    methods = pd.DataFrame({"method_version": sorted(frames["run_metrics"]["method_version"].unique())})
    methods["method"] = methods["method_version"].str.split("-", n=1).str[0]
    tables = {
        "Runs": runs, "Methods": methods, "Dates": dates,
        "Metrics": frames["run_metrics"].merge(frames["comparison_exposure"], on=["run_id", "method_version"], validate="one_to_one"),
        "Events": frames["event_evaluation"].copy(), "Alarms": frames["alarms"].copy(),
        "ModelInfo": pd.DataFrame([{"data_origin": DISCLOSURE, "package_version": __version__,
                                    "rule_sha256": evidence["frozen_rule"]["rule_sha256"],
                                    "reporting_calendar": "Four synthetic seven-day study weeks, 2026-09-01 to 2026-09-28; not observed months of operation"}]),
        "Roadmap": pd.DataFrame([
            {"issue": "Small-leak sensitivity", "priority": "high", "status": "open", "owner": "Project owner", "estimated_hours": 6, "validation_required": "Independent noise tests plus bias sensitivity; no test-set retuning", "next_action": "Investigate meter uncertainty and additional independent evidence"},
            {"issue": "Persistent instrument bias", "priority": "high", "status": "open", "owner": "Project owner", "estimated_hours": 8, "validation_required": "No-leak bias and leak-plus-bias tests with availability preserved", "next_action": "Design an explicit bias diagnostic; smoothing alone cannot remove bias"},
            {"issue": "Inventory during operating changes", "priority": "high", "status": "open", "owner": "Project owner", "estimated_hours": 12, "validation_required": "Independent physical model or measured startup data", "next_action": "Specify inventory physics and identifiable inputs before implementation"},
            {"issue": "Independent physical validation", "priority": "medium", "status": "open", "owner": "Project owner", "estimated_hours": 16, "validation_required": "Provenance, units, scenario grain and held-out evaluation", "next_action": "Audit a separate simulator or licensed laboratory dataset"},
        ]),
    }
    tables["Samples"] = review_samples(frames, runs)
    tables["PeriodSummary"] = period_summaries(tables)
    cards = []
    for method in methods["method_version"]:
        selected_ids = runs.loc[runs["split"].eq("test") & runs["noise_profile"].eq("base_white"), "run_id"]
        metrics = tables["Metrics"].loc[tables["Metrics"]["run_id"].isin(selected_ids) & tables["Metrics"]["method_version"].eq(method)]
        events = tables["Events"].loc[tables["Events"]["run_id"].isin(selected_ids) & tables["Events"]["method_version"].eq(method)]
        values = aggregate_metrics(metrics, events)
        for metric, unit, denominator, action in (
            ("detection_rate_120s", "fraction", values["true_events"], "Inspect misses by leak size and operating condition"),
            ("false_alarms_per_24h", "episodes / 24 h", values["eligible_normal_hours"], "Inspect bias and operating disturbances with exposure and coverage"),
            ("delay_p95_s", "s", values["delay_sample_count"], "Inspect late matches together with missed events"),
            ("evaluation_coverage", "fraction", values["scheduled_hours"], "Inspect missing, suspect and warming intervals"),
        ):
            cards.append({"method_version": method, "metric": metric, "current_value": values[metric], "target_value": np.nan,
                          "unit": unit, "status": "review", "sample_count_or_exposure": denominator,
                          "split": "test", "noise_profile": "base_white", "scenario": "all prescribed scenarios", "review_period": "2026-09 study month", "next_action": action, "data_origin": DISCLOSURE})
        leak_ids = runs.loc[runs["run_id"].isin(selected_ids) & runs["scenario"].isin(["leak_2pct", "leak_5pct"]), "run_id"]
        steady_metrics = metrics.loc[metrics["run_id"].isin(leak_ids)]
        detection = steady_metrics["detected_120s"].sum() / steady_metrics["true_events"].sum() if steady_metrics["true_events"].sum() else np.nan
        cards.append({"method_version": method, "metric": "steady_2pct_5pct_demo_detection_120s", "current_value": detection,
                      "target_value": .9, "unit": "fraction", "status": "unavailable" if pd.isna(detection) else ("meets_demo_target" if detection >= .9 else "below_demo_target"),
                      "sample_count_or_exposure": steady_metrics["true_events"].sum(), "split": "test", "noise_profile": "base_white", "scenario": "steady 2% and 5% only", "review_period": "2026-09 study month", "next_action": "Project demo target only; validate new seeds and physical assumptions", "data_origin": DISCLOSURE})
    tables["Scorecard"] = pd.DataFrame(cards)
    return tables


def period_report(summary: pd.DataFrame, tables: dict, period_type: str) -> str:
    """周月报告默认测试集，显示分子、分母、漏检、延迟样本和行动。"""
    selected = summary.loc[summary["period_type"].eq(period_type) & summary["split"].eq("test") & summary["summary_level"].eq("noise_profile")]
    lines = [f"# {period_type.title()} synthetic test review", "", DISCLOSURE + ".", "",
             "Dates are a presentation-only study calendar mapped by replicate and noise profile. Sampling timestamps and frozen detection results are unchanged. Each study week contains seven days; this is not a record of field operation.", "",
             "Rates sum original event counts and eligible hours. Delay quantiles use matched events directly, with misses retained. Blank/undefined means no eligible result.", "",
             "| Period | Noise | Method | Runs | Truth / timely / missed | False alarms / no-leak h / FA per 24 h | Coverage | Delay n / P50 / P95 s |",
             "| --- | --- | --- | ---: | --- | --- | ---: | --- |"]
    for row in selected.itertuples(index=False):
        lines.append(f"| {row.period_label} | {row.noise_profile} | {row.method_version.split('-',1)[0]} | {row.run_count} | {row.true_events:.0f} / {row.detected_120s:.0f} / {row.missed_120s:.0f} | {row.false_alarm_events:.0f} / {row.eligible_normal_hours:.3f} / {row.false_alarms_per_24h:.3f} | {row.evaluation_coverage:.3%} | {row.delay_sample_count} / {row.delay_p50_s:.1f} / {row.delay_p95_s:.1f} |")
    test_ids = tables["Runs"].loc[tables["Runs"]["split"].eq("test") & tables["Runs"]["noise_profile"].eq("base_white"), "run_id"]
    candidates = tables["Events"].loc[tables["Events"]["run_id"].isin(test_ids) & ~tables["Events"]["detected_120s"]].sort_values(["truth_event_id", "method_version"]).head(3)
    lines += ["", "## Three event records for review", ""]
    for row in candidates.itertuples(index=False):
        delay = f"{row.detection_delay_s:g} s" if pd.notna(row.detection_delay_s) else "no matched alarm"
        lines.append(f"- `{row.truth_event_id}` / `{row.method_version}`: {delay}; eligible 120-second window {row.eligible_window_fraction:.1%}.")
    lines += ["", "## Next actions", "",
              "- Review persistent bias and prescribed operating changes; a smaller white-noise alarm burden does not solve these causes.",
              "- Investigate the missed small leaks alongside noise-driven matches and false-alarm exposure.",
              "- Review data-loss delays and monitor availability before proposing parameter changes.",
              "- Keep any new calibration and held-out test separate; obtain independent physics or laboratory evidence before a field-performance claim.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = ArgumentParser(description="Build local V1 CSV report inputs, period reports and Power BI project definitions.")
    parser.add_argument("--study-dir", type=Path, default=Path("data/generated/study"))
    parser.add_argument("--output", type=Path, default=Path("reports/v1"))
    args = parser.parse_args()
    frames, evidence = load_study(args.study_dir)
    tables = build_tables(frames, evidence)
    destination = args.output / "data"
    destination.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        frame.to_csv(destination / f"{name}.csv", index=False, float_format="%.9f")
        print(f"{name}: {len(frame)} rows", flush=True)
    evidence.update({"package_version": __version__, "reporting_calendar": "2026-09-01 to 2026-09-28; four seven-day study weeks; presentation metadata only", "rows": {name: len(frame) for name, frame in tables.items()},
                     "report_csv_sha256": {name: hashlib.sha256((destination / f"{name}.csv").read_bytes()).hexdigest() for name in tables}})
    (args.output / "manifest.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    for period_type in ("weekly", "monthly"):
        (args.output / f"{period_type}_test_report.md").write_text(period_report(tables["PeriodSummary"], tables, period_type), encoding="utf-8")
    from .powerbi import build_project
    project = build_project(tables, args.output)
    print(f"Power BI authoring project: {project}; open, refresh and save in Desktop to verify.", flush=True)


if __name__ == "__main__":
    main()
