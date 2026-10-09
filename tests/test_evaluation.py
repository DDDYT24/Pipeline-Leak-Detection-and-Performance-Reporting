"""验证事件去重、真值匹配、失效暴露时间与报表分母。"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from pipeline_leak_detection.detector import DetectionSettings, detect
from pipeline_leak_detection.evaluation import evaluate
from pipeline_leak_detection.simulate import generate


def _runs(duration_s: int = 100) -> pd.DataFrame:
    """建立一段可精确核算时长的合成回放元数据。"""
    return pd.DataFrame([{
        "run_id": "case", "scheduled_hours": duration_s / 3600,
        "synthetic_test_date": "2026-09-01", "split": "test",
    }])


def _truth(*intervals: tuple[int, int]) -> pd.DataFrame:
    """生成与指定区间一致的独立真值事件。"""
    return pd.DataFrame([{
        "truth_event_id": f"event-{index}", "run_id": "case",
        "onset_s": start, "end_s": end, "true_leak_m3h": 36.0,
        "true_release_m3": (end - start) / 100,
    } for index, (start, end) in enumerate(intervals, 1)], columns=[
        "truth_event_id", "run_id", "onset_s", "end_s",
        "true_leak_m3h", "true_release_m3",
    ])


def _samples(states: list[tuple[int, str, bool]]) -> pd.DataFrame:
    """构造逐点输出；报警真假由状态序列明确给定。"""
    start_time = datetime(2026, 9, 1, tzinfo=timezone.utc)
    records = []
    previous_end = None
    for elapsed, alarm_state, latched in states:
        records.append({
            "run_id": "case", "method_version": "instant-v1",
            "sample_time_utc": (start_time + timedelta(seconds=elapsed)).isoformat(),
            "elapsed_s": elapsed, "interval_s": 10,
            "quality_state": "invalid" if alarm_state == "unavailable" else "valid",
            "alarm_state": alarm_state, "alarm_latched": latched,
            "gap_before": previous_end is not None and elapsed > previous_end,
        })
        previous_end = elapsed + 10
    return pd.DataFrame(records)


def test_starter_events_and_volume_units():
    """真实 CLI 路径使用的三个回放应得到四个事件和逐事件秒/立方米结果。"""
    root = Path(__file__).parents[1]
    config = json.loads((root / "configs/starter.json").read_text(encoding="utf-8"))
    frames = generate(config)
    settings = DetectionSettings(**config["detection"])
    results = detect(
        frames["readings"], settings,
        config["pipeline"]["nominal_flow_m3h"],
        config["pipeline"]["density_kgm3"],
    )
    output = evaluate(results, frames["runs"], frames["truth"])
    assert len(output["alarms"]) == 4
    assert len(output["event_evaluation"]) == 4
    assert len(output["run_metrics"]) == 6
    assert set(output["alarms"]["classification"]) == {"matched"}
    assert set(output["alarms"]["status"]) == {"resolved"}

    event = output["event_evaluation"].query(
        "run_id == 'leak-02-001' and method_version == 'trailing-v1'"
    ).iloc[0]
    assert event["detection_delay_s"] == 40
    assert event["true_release_before_alarm_m3"] == pytest.approx(16 * 40 / 3600)
    normal = output["run_metrics"].query(
        "run_id == 'normal-001' and method_version == 'trailing-v1'"
    ).iloc[0]
    assert pd.isna(normal["detection_rate_120s"])
    assert normal["eligible_normal_hours"] == pytest.approx(7150 / 3600)


def test_preexisting_alarm_cannot_detect_later_truth_events():
    """泄漏前已经存在的一个报警不能替两个后来的事件报功。"""
    samples = _samples([
        (0, "normal", False), (10, "alarm", True), (20, "alarm", True),
        (30, "alarm", True), (40, "alarm", True), (50, "alarm", True),
        (60, "alarm", True), (70, "alarm", True), (80, "normal", False),
        (90, "normal", False),
    ])
    output = evaluate(samples, _runs(), _truth((20, 40), (60, 80)))
    assert len(output["alarms"]) == 1
    assert output["alarms"].iloc[0]["classification"] == "false_alarm"
    assert not output["event_evaluation"]["detected_anytime"].any()
    metrics = output["run_metrics"].iloc[0]
    assert metrics["missed_120s"] == 2
    assert metrics["false_alarm_events"] == 1
    assert metrics["eligible_normal_hours"] == pytest.approx(60 / 3600)


def test_two_alarms_during_one_leak_match_only_once():
    """同一真值只匹配最早的新报警，后续新触发列为重复。"""
    samples = _samples([
        (0, "normal", False), (10, "normal", False),
        (20, "alarm", True), (30, "normal", False),
        (40, "alarm", True), (50, "normal", False),
        (60, "normal", False), (70, "normal", False),
        (80, "normal", False), (90, "normal", False),
    ])
    output = evaluate(samples, _runs(), _truth((20, 60)))
    assert output["alarms"]["classification"].tolist() == ["matched", "duplicate_during_leak"]
    metrics = output["run_metrics"].iloc[0]
    assert metrics["detected_120s"] == 1
    assert metrics["false_alarm_events"] == 0
    assert metrics["duplicate_during_leak_events"] == 1


def test_late_alarm_remains_a_120_second_miss():
    """迟到的报警保留实际延迟，同时仍计入 120 秒限时漏检。"""
    samples = _samples([
        (elapsed, "alarm" if 130 <= elapsed < 210 else "normal", 130 <= elapsed < 210)
        for elapsed in range(0, 230, 10)
    ])
    output = evaluate(samples, _runs(230), _truth((0, 200)))
    event = output["event_evaluation"].iloc[0]
    assert bool(event["detected_anytime"])
    assert not bool(event["detected_120s"])
    assert event["detection_delay_s"] == 130
    assert event["true_release_before_alarm_m3"] == pytest.approx(1.3)
    assert output["run_metrics"].iloc[0]["missed_120s"] == 1


def test_unavailable_gap_preserves_open_alarm_and_reduces_coverage():
    """断线、预热、缺测和末尾未解除各自占用时间，不能自动清除旧报警。"""
    samples = _samples([
        (0, "alarm", True), (10, "unavailable", True),
        (20, "warming", True), (40, "alarm", True),
    ])
    output = evaluate(samples, _runs(60), _truth())
    alarm = output["alarms"].iloc[0]
    assert alarm["status"] == "open"
    assert pd.isna(alarm["resolved_at_s"])
    assert alarm["observed_until_s"] == 60
    assert alarm["interrupted_s"] == 40
    metrics = output["run_metrics"].iloc[0]
    assert metrics["alarm_events"] == 1
    assert metrics["open_alarm_events"] == 1
    assert metrics["missing_hours"] == pytest.approx(20 / 3600)
    assert metrics["valid_hours"] == pytest.approx(30 / 3600)
    assert metrics["evaluable_hours"] == pytest.approx(20 / 3600)
    assert metrics["evaluation_coverage"] == pytest.approx(1 / 3)


def test_no_evaluable_time_keeps_miss_and_undefined_false_alarm_rate():
    """整段仪表无效时保留漏检，零分母显示缺值而不是零风险。"""
    samples = _samples([(0, "unavailable", False), (10, "unavailable", False)])
    output = evaluate(samples, _runs(20), _truth((0, 20)))
    assert output["alarms"].empty
    event = output["event_evaluation"].iloc[0]
    assert not bool(event["detected_120s"])
    assert event["eligible_window_fraction"] == 0
    metrics = output["run_metrics"].iloc[0]
    assert metrics["missed_120s"] == 1
    assert metrics["evaluation_coverage"] == 0
    assert pd.isna(metrics["false_alarms_per_24h"])


def test_inconsistent_truth_and_invalid_alarm_transition_are_rejected():
    """错误真值体积与无效数据自动清除报警都应显式失败。"""
    truth = _truth((20, 40))
    truth.loc[0, "true_release_m3"] = 100.0
    with pytest.raises(ValueError, match="release volume"):
        evaluate(_samples([(0, "normal", False)]), _runs(), truth)

    samples = _samples([(0, "alarm", True), (10, "unavailable", False)])
    with pytest.raises(ValueError, match="cannot resolve"):
        evaluate(samples, _runs(), _truth())


def test_methods_with_different_sample_grids_are_rejected():
    """两种方法缺了不同测量行时，禁止直接比较覆盖率和报警数。"""
    instant = _samples([(0, "normal", False), (10, "normal", False)])
    trailing = instant.iloc[:1].copy()
    trailing["method_version"] = "trailing-v1"
    with pytest.raises(ValueError, match="same sample grid"):
        evaluate(pd.concat([instant, trailing], ignore_index=True), _runs(20), _truth())
