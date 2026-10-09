"""把逐点确认状态合并为可审查的报警事件。"""

from datetime import timedelta
import math
from numbers import Integral, Real

import numpy as np
import pandas as pd


REQUIRED_DETECTOR_COLUMNS = (
    "run_id", "method_version", "sample_time_utc", "elapsed_s", "interval_s",
    "quality_state", "alarm_state", "alarm_latched", "gap_before",
)
ALARM_COLUMNS = (
    "alarm_id", "run_id", "method_version", "start_s", "start_time_utc",
    "resolved_at_s", "observed_until_s", "status", "interrupted_s",
    "classification", "matched_truth_event_id",
)
EVALUABLE_STATES = frozenset({"normal", "pending", "alarm"})
NON_EVALUABLE_STATES = frozenset({"unavailable", "warming"})


def _positive_seconds(value: object, name: str) -> float:
    """验证计划时长，避免缺失或负时长污染统计分母。"""
    if (
        not isinstance(value, Real)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be positive and finite.")
    return float(value)


def scheduled_seconds_by_run(runs: pd.DataFrame) -> dict[str, int]:
    """从独立回放表读取每组计划监测时长。"""
    required = {"run_id", "scheduled_hours"}
    missing = required.difference(runs.columns)
    if missing:
        raise ValueError(f"Missing run columns: {', '.join(sorted(missing))}.")
    if runs.empty:
        raise ValueError("At least one run is required for event evaluation.")
    if runs["run_id"].duplicated().any():
        raise ValueError("Run IDs must be unique.")

    schedules = {}
    for run in runs.itertuples(index=False):
        if not isinstance(run.run_id, str) or not run.run_id.strip():
            raise ValueError("Run ID must be a nonempty string.")
        seconds = _positive_seconds(run.scheduled_hours, "Scheduled hours") * 3600
        # 小时转秒后的浮点尾差不能把最后一个合法采样误判为越界。
        if not math.isclose(seconds, round(seconds), rel_tol=0, abs_tol=1e-6):
            raise ValueError("Scheduled duration must resolve to whole seconds.")
        schedules[run.run_id] = int(round(seconds))
    return schedules


def _finish(event: dict, end_s: float, status: str) -> dict:
    """区分有效解除时间与回放结束时仍未解除的报警。"""
    return {
        **event,
        "resolved_at_s": end_s if status == "resolved" else float("nan"),
        "observed_until_s": end_s,
        "status": status,
    }


def build_alarm_episodes(detector_results: pd.DataFrame, runs: pd.DataFrame) -> pd.DataFrame:
    """按方法和回放合并连续报警，保留中断与未解决状态。"""
    missing = set(REQUIRED_DETECTOR_COLUMNS).difference(detector_results.columns)
    if missing:
        raise ValueError(f"Missing detector columns: {', '.join(sorted(missing))}.")
    schedules = scheduled_seconds_by_run(runs)
    if detector_results.empty:
        raise ValueError("Detector results must contain at least one sample.")
    if detector_results[["run_id", "method_version", "elapsed_s"]].duplicated().any():
        raise ValueError("Detector samples must be unique by run, method, and elapsed time.")

    records: list[dict] = []
    grouped = detector_results.groupby(["run_id", "method_version"], sort=False, dropna=False)
    for (run_id, method), group in grouped:
        if run_id not in schedules or not isinstance(method, str) or not method.strip():
            raise ValueError("Detector run or method is missing from run metadata.")
        duration = schedules[run_id]
        active: dict | None = None
        previous_end: int | None = None
        previous_elapsed: int | None = None
        previous_time: pd.Timestamp | None = None
        episode_number = 0

        for sample in group.itertuples(index=False):
            elapsed, interval = sample.elapsed_s, sample.interval_s
            if not isinstance(elapsed, Integral) or isinstance(elapsed, bool) or elapsed < 0:
                raise ValueError("Detector elapsed_s must be a nonnegative integer.")
            if not isinstance(interval, Integral) or isinstance(interval, bool) or interval <= 0:
                raise ValueError("Detector interval_s must be a positive integer.")
            if elapsed + interval > duration:
                raise ValueError("Detector sample exceeds scheduled run duration.")
            try:
                timestamp = pd.Timestamp(sample.sample_time_utc)
            except (TypeError, ValueError) as exc:
                raise ValueError("Detector sample time must be a UTC timestamp.") from exc
            if (
                pd.isna(timestamp)
                or timestamp.tzinfo is None
                or timestamp.utcoffset() != timedelta(0)
            ):
                raise ValueError("Detector sample time must have an explicit UTC offset.")
            if not isinstance(sample.alarm_latched, (bool, np.bool_)):
                raise ValueError("alarm_latched must be Boolean.")
            if not isinstance(sample.gap_before, (bool, np.bool_)):
                raise ValueError("gap_before must be Boolean.")
            if sample.alarm_state not in EVALUABLE_STATES | NON_EVALUABLE_STATES:
                raise ValueError("Unknown alarm_state.")
            if sample.quality_state not in {"valid", "invalid"}:
                raise ValueError("Unknown quality_state.")
            if (sample.quality_state == "invalid") != (sample.alarm_state == "unavailable"):
                raise ValueError("Invalid measurement and unavailable state must agree.")
            if (sample.alarm_state == "alarm") and not sample.alarm_latched:
                raise ValueError("A confirmed alarm must remain latched.")
            if sample.alarm_state == "normal" and sample.alarm_latched:
                raise ValueError("A normal evaluation must clear the alarm latch.")

            gap = 0 if previous_end is None else elapsed - previous_end
            if gap < 0:
                raise ValueError("Detector samples overlap or are out of order.")
            if previous_end is not None and bool(sample.gap_before) != (gap > 0):
                raise ValueError("gap_before disagrees with the sampling timeline.")
            if previous_end is None and sample.gap_before:
                raise ValueError("The first sample cannot report a preceding gap.")
            if previous_time is not None and timestamp - previous_time != timedelta(
                seconds=elapsed - previous_elapsed
            ):
                raise ValueError("Detector timestamp and elapsed_s disagree.")
            if active is not None:
                active["interrupted_s"] += gap

            if sample.alarm_latched and active is None:
                if sample.alarm_state != "alarm":
                    raise ValueError("A new alarm must begin with a confirmed alarm state.")
                episode_number += 1
                active = {
                    "alarm_id": f"{run_id}:{method}:alarm-{episode_number:03d}",
                    "run_id": run_id,
                    "method_version": method,
                    "start_s": elapsed,
                    "start_time_utc": sample.sample_time_utc,
                    "interrupted_s": 0,
                    "classification": "unclassified",
                    "matched_truth_event_id": "",
                }
            elif active is not None and not sample.alarm_latched:
                if sample.alarm_state in NON_EVALUABLE_STATES:
                    raise ValueError("Unavailable data cannot resolve an existing alarm.")
                records.append(_finish(active, elapsed, "resolved"))
                active = None

            if active is not None and sample.alarm_state in NON_EVALUABLE_STATES:
                # 断线或重新积累窗口时保留报警，同时记录这段缺少判断证据的时间。
                active["interrupted_s"] += interval
            previous_end = elapsed + interval
            previous_elapsed, previous_time = elapsed, timestamp

        if active is not None:
            active["interrupted_s"] += duration - previous_end
            records.append(_finish(active, duration, "open"))

    return pd.DataFrame.from_records(records, columns=ALARM_COLUMNS)
