"""用独立真值评价报警事件，保留漏检和可评价时间分母。"""

import math
from numbers import Integral, Real

import pandas as pd

from .alarms import EVALUABLE_STATES, build_alarm_episodes, scheduled_seconds_by_run


TIMELY_LIMIT_S = 120
EVENT_COLUMNS = (
    "truth_event_id", "run_id", "method_version", "onset_s", "end_s",
    "true_leak_m3h", "true_release_m3", "matched_alarm_id", "first_alarm_s",
    "detection_delay_s", "detected_anytime", "detected_120s",
    "eligible_window_fraction", "true_release_before_alarm_m3",
)
METRIC_COLUMNS = (
    "run_id", "method_version", "synthetic_test_date", "split", "true_events",
    "detected_anytime", "detected_120s", "missed_anytime", "missed_120s",
    "alarm_events", "false_alarm_events", "duplicate_during_leak_events",
    "open_alarm_events", "sample_count", "scheduled_hours", "observed_hours",
    "missing_hours", "valid_hours", "evaluable_hours", "eligible_normal_hours",
    "data_valid_fraction", "evaluation_coverage", "detection_rate_120s",
    "false_alarms_per_24h",
)


def _overlap_seconds(
    left_start: float, left_end: float, right_start: float, right_end: float
) -> float:
    """计算两个半开时间区间交集的秒数。"""
    return max(0.0, min(left_end, right_end) - max(left_start, right_start))


def _validate_method_grids(detector_results: pd.DataFrame, runs: pd.DataFrame) -> None:
    """确保两种方法比较的是同一批测量时刻和仪表有效性。"""
    methods = set(detector_results["method_version"])
    if set(detector_results["run_id"]) != set(runs["run_id"]):
        raise ValueError("Every scheduled run must have detector samples.")
    columns = ["sample_time_utc", "elapsed_s", "interval_s", "quality_state"]
    for _, run_group in detector_results.groupby("run_id", sort=False):
        if set(run_group["method_version"]) != methods:
            raise ValueError("Every run must contain the same detector methods.")
        reference = None
        for _, method_group in run_group.groupby("method_version", sort=False):
            grid = method_group[columns].reset_index(drop=True)
            if reference is not None and not grid.equals(reference):
                raise ValueError(
                    "Detector methods must use the same sample grid and quality states."
                )
            reference = grid


def _validated_truth(truth: pd.DataFrame, schedules: dict[str, int]) -> dict[str, list[dict]]:
    """检查真值事件的范围、唯一性和体积单位，再按回放整理。"""
    required = {
        "truth_event_id", "run_id", "onset_s", "end_s",
        "true_leak_m3h", "true_release_m3",
    }
    missing = required.difference(truth.columns)
    if missing:
        raise ValueError(f"Missing truth columns: {', '.join(sorted(missing))}.")
    if truth["truth_event_id"].duplicated().any():
        raise ValueError("Truth event IDs must be unique.")

    by_run: dict[str, list[dict]] = {run_id: [] for run_id in schedules}
    for event in truth.itertuples(index=False):
        if not isinstance(event.truth_event_id, str) or not event.truth_event_id.strip():
            raise ValueError("Truth event ID must be a nonempty string.")
        if event.run_id not in schedules:
            raise ValueError("Truth event refers to an unknown run.")
        if any(
            not isinstance(value, Integral) or isinstance(value, bool)
            for value in (event.onset_s, event.end_s)
        ) or not 0 <= event.onset_s < event.end_s <= schedules[event.run_id]:
            raise ValueError("Truth event boundaries must lie within its run.")
        if (
            not isinstance(event.true_leak_m3h, Real)
            or not math.isfinite(event.true_leak_m3h)
            or event.true_leak_m3h <= 0
        ):
            raise ValueError("True leak rate must be positive and finite.")
        expected_volume = event.true_leak_m3h * (event.end_s - event.onset_s) / 3600
        if (
            not isinstance(event.true_release_m3, Real)
            or not math.isfinite(event.true_release_m3)
            or not math.isclose(event.true_release_m3, expected_volume, rel_tol=1e-8, abs_tol=1e-8)
        ):
            raise ValueError("True release volume disagrees with rate and duration.")
        by_run[event.run_id].append({
            "truth_event_id": event.truth_event_id,
            "run_id": event.run_id,
            "onset_s": event.onset_s,
            "end_s": event.end_s,
            "true_leak_m3h": event.true_leak_m3h,
            "true_release_m3": event.true_release_m3,
        })

    for events in by_run.values():
        events.sort(key=lambda event: event["onset_s"])
        if any(left["end_s"] > right["onset_s"] for left, right in zip(events, events[1:])):
            raise ValueError("Overlapping truth events are outside the V1 evaluator.")
    return by_run


def _normal_seconds(start: int, end: int, events: list[dict]) -> float:
    """从一个采样区间扣除所有真实泄漏时间，得到无泄漏暴露时长。"""
    leak_seconds = sum(
        _overlap_seconds(start, end, event["onset_s"], event["end_s"])
        for event in events
    )
    return end - start - leak_seconds


def _exposure(group: pd.DataFrame, events: list[dict], scheduled_s: float) -> dict[str, float]:
    """分别累计观察、有效、可评价及无泄漏可评价的时间。"""
    observed = valid = evaluable = eligible_normal = 0.0
    for sample in group.itertuples(index=False):
        observed += sample.interval_s
        if sample.quality_state == "valid":
            valid += sample.interval_s
        if sample.alarm_state in EVALUABLE_STATES:
            evaluable += sample.interval_s
            eligible_normal += _normal_seconds(
                sample.elapsed_s, sample.elapsed_s + sample.interval_s, events
            )
    if observed > scheduled_s:
        raise ValueError("Observed time exceeds scheduled run duration.")
    return {
        "scheduled_hours": scheduled_s / 3600,
        "observed_hours": observed / 3600,
        "missing_hours": (scheduled_s - observed) / 3600,
        "valid_hours": valid / 3600,
        "evaluable_hours": evaluable / 3600,
        "eligible_normal_hours": eligible_normal / 3600,
        "data_valid_fraction": valid / scheduled_s,
        "evaluation_coverage": evaluable / scheduled_s,
    }


def _eligible_window_fraction(group: pd.DataFrame, event: dict, limit_s: int) -> float:
    """衡量泄漏起点后的限时窗口有多少时间可供该方法评价。"""
    start = event["onset_s"]
    end = min(start + limit_s, event["end_s"])
    eligible = sum(
        _overlap_seconds(sample.elapsed_s, sample.elapsed_s + sample.interval_s, start, end)
        for sample in group.itertuples(index=False)
        if sample.alarm_state in EVALUABLE_STATES
    )
    return eligible / (end - start)


def evaluate(
    detector_results: pd.DataFrame,
    runs: pd.DataFrame,
    truth: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """生成报警事件、真值逐事件结果，以及带分母的逐回放指标。"""
    schedules = scheduled_seconds_by_run(runs)
    by_run = _validated_truth(truth, schedules)
    alarms = build_alarm_episodes(detector_results, runs)
    _validate_method_grids(detector_results, runs)
    alarm_rows = alarms.to_dict("records")
    groups = detector_results.groupby(["run_id", "method_version"], sort=False)

    event_rows: list[dict] = []
    metric_rows: list[dict] = []
    matches: dict[str, str] = {}
    for (run_id, method), group in groups:
        events = by_run[run_id]
        method_alarms = sorted(
            (
                alarm for alarm in alarm_rows
                if alarm["run_id"] == run_id and alarm["method_version"] == method
            ),
            key=lambda alarm: alarm["start_s"],
        )
        for event in events:
            # 新触发且落在真值窗口内的最早报警可匹配；旧报警不能代替新泄漏检出。
            match = next((
                alarm for alarm in method_alarms
                if alarm["alarm_id"] not in matches
                and event["onset_s"] <= alarm["start_s"] < event["end_s"]
            ), None)
            delay = match["start_s"] - event["onset_s"] if match else float("nan")
            timely = bool(match) and delay <= TIMELY_LIMIT_S
            if match:
                matches[match["alarm_id"]] = event["truth_event_id"]
            event_rows.append({
                **event,
                "method_version": method,
                "matched_alarm_id": match["alarm_id"] if match else "",
                "first_alarm_s": match["start_s"] if match else float("nan"),
                "detection_delay_s": delay,
                "detected_anytime": bool(match),
                "detected_120s": timely,
                "eligible_window_fraction": _eligible_window_fraction(group, event, TIMELY_LIMIT_S),
                "true_release_before_alarm_m3": (
                    event["true_leak_m3h"] * delay / 3600 if match else float("nan")
                ),
            })

        exposure = _exposure(group, events, schedules[run_id])
        # 分类只依据报警起点；泄漏期间多出的新报警单独列为重复报警。
        false_alarms = sum(
            alarm["alarm_id"] not in matches
            and not any(event["onset_s"] <= alarm["start_s"] < event["end_s"] for event in events)
            for alarm in method_alarms
        )
        duplicates = len(method_alarms) - false_alarms - sum(
            alarm["alarm_id"] in matches for alarm in method_alarms
        )
        results = [
            row for row in event_rows
            if row["run_id"] == run_id and row["method_version"] == method
        ]
        timely_count = sum(row["detected_120s"] for row in results)
        any_count = sum(row["detected_anytime"] for row in results)
        run_info = runs.loc[runs["run_id"] == run_id].iloc[0]
        metric_rows.append({
            "run_id": run_id,
            "method_version": method,
            "synthetic_test_date": run_info.get("synthetic_test_date", ""),
            "split": run_info.get("split", ""),
            "true_events": len(events),
            "detected_anytime": any_count,
            "detected_120s": timely_count,
            "missed_anytime": len(events) - any_count,
            "missed_120s": len(events) - timely_count,
            "alarm_events": len(method_alarms),
            "false_alarm_events": false_alarms,
            "duplicate_during_leak_events": duplicates,
            "open_alarm_events": sum(alarm["status"] == "open" for alarm in method_alarms),
            "sample_count": len(group),
            **exposure,
            "detection_rate_120s": timely_count / len(events) if events else float("nan"),
            "false_alarms_per_24h": (
                false_alarms * 24 / exposure["eligible_normal_hours"]
                if exposure["eligible_normal_hours"] else float("nan")
            ),
        })

    for alarm in alarm_rows:
        matched_event = matches.get(alarm["alarm_id"], "")
        if matched_event:
            alarm["classification"] = "matched"
            alarm["matched_truth_event_id"] = matched_event
        elif any(
            event["onset_s"] <= alarm["start_s"] < event["end_s"]
            for event in by_run[alarm["run_id"]]
        ):
            alarm["classification"] = "duplicate_during_leak"
        else:
            alarm["classification"] = "false_alarm"

    return {
        "alarms": pd.DataFrame.from_records(alarm_rows, columns=alarms.columns),
        "event_evaluation": pd.DataFrame.from_records(event_rows, columns=EVENT_COLUMNS),
        "run_metrics": pd.DataFrame.from_records(metric_rows, columns=METRIC_COLUMNS),
    }
