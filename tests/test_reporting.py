"""验证周月聚合、展示缺口、显式字段类型和报表事实关系。"""

from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from pipeline_leak_detection.cli import export
from pipeline_leak_detection.powerbi import MEASURES, build_project, column_type
from pipeline_leak_detection.reporting import (
    COUNTS, HOURS, aggregate_metrics, build_tables, load_study,
    period_report, reporting_calendar,
)
from pipeline_leak_detection.simulate import generate
from pipeline_leak_detection.study import expand_study, freeze_rule, run_detection, validate_frozen_rule, comparison_exposure, summarize


@pytest.fixture(scope="module")
def report_data():
    study = json.loads((Path(__file__).parents[1] / "configs/study.json").read_text(encoding="utf-8"))
    study["noise_profiles"] = study["noise_profiles"][:1]
    study["scenarios"] = [s for s in study["scenarios"] if s["scenario"] in {"normal", "small_leak", "leak_2pct", "dropped_samples", "leak_data_loss"}]
    for split in study["splits"].values():
        split["replicates"] = 2
    phases = {}
    frozen = None
    for phase in ("calibration", "test"):
        config = expand_study(study, phase)
        frames = generate(config)
        if phase == "calibration":
            frozen = freeze_rule(study, frames)
        settings = validate_frozen_rule(study, frozen)
        frames["runs"]["rule_sha256"] = frozen["rule_sha256"]
        run_detection(frames, config, settings)
        frames["comparison_exposure"] = comparison_exposure(frames)
        phases[phase] = (config, frames)
    frames = {name: pd.concat([phases[phase][1][name] for phase in phases], ignore_index=True) for name in phases["test"][1]}
    evidence = {"frozen_rule": frozen}
    tables = build_tables(frames, evidence)
    return study, phases, frames, evidence, tables


def test_unequal_denominators_and_direct_delay_quantiles():
    records = []
    for run_id, truth, detected, hours, false in (("a", 1, 1, 1, 2), ("b", 9, 0, 9, 0)):
        row = {name: 0 for name in COUNTS + HOURS}
        row.update(run_id=run_id, true_events=truth, detected_anytime=detected, detected_120s=detected,
                   missed_anytime=truth-detected, missed_120s=truth-detected, false_alarm_events=false,
                   scheduled_hours=hours, evaluable_hours=hours, valid_hours=hours,
                   eligible_normal_hours=hours)
        records.append(row)
    events = pd.DataFrame({"detected_anytime": [True, False, True], "detection_delay_s": [10.0, float("nan"), 110.0]})
    result = aggregate_metrics(pd.DataFrame(records), events)
    assert result["detection_rate_120s"] == .1
    assert result["false_alarms_per_24h"] == pytest.approx(4.8)
    assert result["delay_sample_count"] == 2
    assert result["delay_p50_s"] == 60
    assert result["delay_p95_s"] == 105


def test_zero_denominators_and_no_matches_stay_undefined():
    metrics = pd.DataFrame([{**{name: 0 for name in COUNTS + HOURS}, "run_id": "case"}])
    events = pd.DataFrame({"detected_anytime": [False], "detection_delay_s": [float("nan")]})
    result = aggregate_metrics(metrics, events)
    assert all(pd.isna(result[name]) for name in ("detection_rate_120s", "false_alarms_per_24h", "false_alarms_per_24h_common", "evaluation_coverage", "delay_p95_s"))
    assert result["delay_sample_count"] == 0


def test_calendar_preserves_sampling_metadata_and_uses_four_study_weeks(report_data):
    _, _, frames, _, _ = report_data
    original = frames["runs"].copy()
    runs, dates = reporting_calendar(original)
    pd.testing.assert_frame_equal(runs[original.columns], original)
    assert len(dates) == 28
    assert dates["week_order"].value_counts().tolist() == [7, 7, 7, 7]
    assert dates["month_label"].nunique() == 1
    assert dates["data_origin"].str.contains("100% synthetic", regex=False).all()
    assert set(runs["report_date"]).issubset(set(dates["report_date"]))


def test_weekly_monthly_reconciliation_uses_original_facts(report_data):
    _, _, _, _, tables = report_data
    summary = tables["PeriodSummary"]
    monthly = summary.loc[summary["period_type"].eq("monthly") & summary["summary_level"].eq("split")]
    for row in monthly.itertuples(index=False):
        ids = tables["Runs"].loc[tables["Runs"]["split"].eq(row.split), "run_id"]
        metrics = tables["Metrics"].loc[tables["Metrics"]["run_id"].isin(ids) & tables["Metrics"]["method_version"].eq(row.method_version)]
        events = tables["Events"].loc[tables["Events"]["run_id"].isin(ids) & tables["Events"]["method_version"].eq(row.method_version)]
        assert row.true_events == metrics["true_events"].sum()
        assert row.false_alarms_per_24h == pytest.approx(metrics["false_alarm_events"].sum()*24/metrics["eligible_normal_hours"].sum())
        matched = events.loc[events["detected_anytime"], "detection_delay_s"]
        assert row.delay_p95_s == pytest.approx(matched.quantile(.95))
        weekly = summary.loc[summary["period_type"].eq("weekly") & summary["summary_level"].eq("split") & summary["split"].eq(row.split) & summary["method_version"].eq(row.method_version)]
        assert weekly["true_events"].sum() == row.true_events
        assert weekly["missed_120s"].sum() == row.missed_120s
        assert weekly["eligible_normal_hours"].sum() == pytest.approx(row.eligible_normal_hours)
    for period_type in ("weekly", "monthly"):
        text = period_report(summary, tables, period_type)
        assert "100% synthetic" in text and "Delay n" in text
        assert "field operation" in text and "Next actions" in text


def test_missing_review_rows_do_not_change_metric_denominators(report_data):
    _, _, frames, _, tables = report_data
    samples = tables["Samples"]
    assert not samples.duplicated(["run_id", "method_version", "elapsed_s"]).any()
    synthetic = samples.loc[samples["display_row_origin"].eq("missing_interval_display_only")]
    assert len(synthetic) == 2 * 2 * 2 * 31  # two splits, two replicates, two methods
    assert synthetic["qin_m3h"].isna().all() and synthetic["qout_m3h"].isna().all()
    assert synthetic["monitoring_available"].eq(0).all()
    assert synthetic["alarm_state"].eq("unavailable").all()
    assert tables["Metrics"]["sample_count"].sum() == frames["run_metrics"]["sample_count"].sum()
    pd.testing.assert_frame_equal(tables["Events"], frames["event_evaluation"])


def test_full_column_type_controls_fractional_exposure_and_nulls():
    assert column_type("eligible_normal_hours", pd.Series([2.0]*200 + [.25])) == ("double", "type number")
    assert column_type("detection_rate_120s", pd.Series([float("nan")]*200 + [.3])) == ("double", "type number")
    assert column_type("detected_120s", pd.Series([True, False])) == ("boolean", "type logical")
    assert column_type("alarm_latched", pd.Series([True, None, False], dtype=object)) == ("boolean", "type logical")
    assert column_type("report_date", pd.Series(["2026-09-01"])) == ("dateTime", "type date")


def test_generated_project_bindings_and_geometry(report_data, tmp_path):
    _, _, _, _, tables = report_data
    path = build_project(tables, tmp_path)
    assert path.exists()
    projects = json.loads(path.read_text(encoding="utf-8"))
    assert projects["artifacts"][0]["report"]["path"] == "Pipeline.Report"
    definition = tmp_path / "Pipeline.Report/definition"
    pages = json.loads((definition / "pages/pages.json").read_text())
    assert len(pages["pageOrder"]) == 3
    for page_name in pages["pageOrder"]:
        folder = definition / "pages" / page_name
        page = json.loads((folder / "page.json").read_text())
        for file in (folder / "visuals").glob("*/visual.json"):
            visual = json.loads(file.read_text())
            position = visual["position"]
            assert 0 <= position["x"] < position["x"] + position["width"] <= page["width"]
            assert 0 <= position["y"] < position["y"] + position["height"] <= page["height"]
            if visual["visual"]["visualType"] == "lineChart":
                assert visual["visual"]["objects"]["categoryAxis"][0]["properties"]["axisType"]["expr"]["Literal"]["Value"] == "'Scalar'"
                if visual["name"] == "Availability":
                    assert position["height"] >= 160
            for role in visual["visual"].get("query", {}).get("queryState", {}).values():
                for projection in role["projections"]:
                    field = projection["field"]
                    if "Column" in field:
                        reference = field["Column"]
                        assert reference["Property"] in tables[reference["Expression"]["SourceRef"]["Entity"]]
                    else:
                        assert field["Measure"]["Property"] in MEASURES
        header = json.loads((folder / "visuals/Header/visual.json").read_text())
        assert "100% synthetic" in json.dumps(header)
    relationships = (tmp_path / "Pipeline.SemanticModel/definition/relationships.tmdl").read_text()
    assert relationships.count("crossFilteringBehavior: oneDirection") == 9
    metric_model = (tmp_path / "Pipeline.SemanticModel/definition/tables/Metrics.tmdl").read_text()
    assert 'Encoding=65001' in metric_model and '"en-US"' in metric_model


def test_export_loader_checks_rules_counts_and_duplicate_seeds(report_data, tmp_path):
    study, phases, _, evidence, _ = report_data
    for phase, (config, frames) in phases.items():
        folder = tmp_path / phase
        export(frames, folder, config, {"phase": phase, "study_config": study, "rule_sha256": evidence["frozen_rule"]["rule_sha256"]})
        (folder / "frozen_rule.json").write_text(json.dumps(evidence["frozen_rule"]), encoding="utf-8")
    loaded, actual_evidence = load_study(tmp_path)
    assert len(loaded["runs"]) == 20
    assert actual_evidence["frozen_rule"] == evidence["frozen_rule"]
    test_runs_path = tmp_path / "test/runs.csv"
    runs = pd.read_csv(test_runs_path)
    runs.loc[0, "seed"] = phases["calibration"][1]["runs"]["seed"].iloc[0]
    runs.to_csv(test_runs_path, index=False)
    with pytest.raises(ValueError, match="split seeds"):
        load_study(tmp_path)


def test_complete_export_replay_and_report_reconciliation(report_data, tmp_path):
    import subprocess
    import sys
    from pipeline_leak_detection.verification import verify_all

    study, phases, _, evidence, _ = report_data
    generated = tmp_path / "generated"
    starter_config = json.loads((Path(__file__).parents[1] / "configs/starter.json").read_text(encoding="utf-8"))
    starter = generate(starter_config)
    from pipeline_leak_detection.detector import DetectionSettings
    run_detection(starter, starter_config, DetectionSettings(**starter_config["detection"]))
    export(starter, generated, starter_config, {})
    for phase, (config, original) in phases.items():
        frames = deepcopy(original)
        frames["study_summary"] = summarize(frames)
        frames["study_summary"]["rule_sha256"] = evidence["frozen_rule"]["rule_sha256"]
        folder = generated / "study" / phase
        export(frames, folder, config, {"phase": phase, "study_config": study, "rule_sha256": evidence["frozen_rule"]["rule_sha256"], "frozen_rule": evidence["frozen_rule"]})
        (folder / "frozen_rule.json").write_text(json.dumps(evidence["frozen_rule"]), encoding="utf-8")
    report = tmp_path / "report"
    subprocess.run([sys.executable, "-m", "pipeline_leak_detection.reporting", "--study-dir", str(generated / "study"), "--output", str(report)], check=True, capture_output=True)
    result = verify_all(generated, report)
    assert result["status"] == "passed"
    assert result["report_rows"]["Runs"] == 20
    assert result["phases"]["starter"]["rows"]["readings"] == 2160
    assert result["rule_sha256"] == evidence["frozen_rule"]["rule_sha256"]
