"""仅用当前及历史测量计算逐点检测状态，不接触事件真值。"""

from collections import deque
from dataclasses import dataclass
from datetime import timedelta
import math
from numbers import Integral, Real

import pandas as pd


REQUIRED_COLUMNS = (
    "run_id", "sample_time_utc", "elapsed_s", "interval_s",
    "qin_m3h", "qout_m3h", "density_kgm3", "instrument_status",
)
RESULT_COLUMNS = (
    "run_id", "sample_time_utc", "elapsed_s", "interval_s", "method_version",
    "residual_kgs", "smoothed_residual_kgs", "threshold_kgs",
    "instrument_status", "quality_state", "invalid_reason", "gap_before",
    "window_samples_available", "confirmations", "alarm_state", "alarm_latched",
)


@dataclass(frozen=True)
class DetectionSettings:
    """集中保存可复现的阈值、窗口和连续确认参数。"""

    threshold_fraction_nominal: float
    window_samples: int
    confirmation_samples: int
    version: str

    def __post_init__(self) -> None:
        """在处理数据前验证规则，防止无效参数产生貌似正常的输出。"""
        if (
            not _finite_number(self.threshold_fraction_nominal)
            or not 0 < self.threshold_fraction_nominal < 1
        ):
            raise ValueError(
                "Detection threshold fraction must be finite and between zero and one."
            )
        for name in ("window_samples", "confirmation_samples"):
            value = getattr(self, name)
            if not isinstance(value, Integral) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("Detection version must be a nonempty string.")


def _finite_number(value: object) -> bool:
    """检查传感器数值是否可用于物理计算。"""
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _sample_quality(sample: object) -> str:
    """返回空字符串表示可用，否则给出不可评价的直接原因。"""
    if not isinstance(sample.instrument_status, str) or sample.instrument_status != "valid":
        return "instrument_status"
    if not _finite_number(sample.qin_m3h) or not _finite_number(sample.qout_m3h):
        return "flow_missing_or_nonfinite"
    if sample.qin_m3h < 0 or sample.qout_m3h < 0:
        return "flow_negative"
    if not _finite_number(sample.density_kgm3) or sample.density_kgm3 <= 0:
        return "density_invalid"
    return ""


def _sample_time(value: object) -> pd.Timestamp:
    """要求测量时间明确采用 UTC，避免回放顺序与时区含糊。"""
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Sample time must be a valid UTC timestamp.") from exc
    if pd.isna(timestamp) or timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("Sample time must have an explicit UTC offset.")
    return timestamp


def detect(
    readings: pd.DataFrame,
    settings: DetectionSettings,
    nominal_flow_m3h: float,
    nominal_density_kgm3: float,
) -> pd.DataFrame:
    """按回放顺序比较即时阈值和因果窗口，并导出每个采样的状态。"""
    missing = set(REQUIRED_COLUMNS).difference(readings.columns)
    if missing:
        raise ValueError(f"Missing measurement columns: {', '.join(sorted(missing))}.")
    if not _finite_number(nominal_flow_m3h) or nominal_flow_m3h <= 0:
        raise ValueError("Nominal flow must be positive and finite.")
    if not _finite_number(nominal_density_kgm3) or nominal_density_kgm3 <= 0:
        raise ValueError("Nominal density must be positive and finite.")

    threshold = nominal_density_kgm3 * nominal_flow_m3h * settings.threshold_fraction_nominal / 3600
    records: list[dict] = []
    # 只投影允许的测量列；即使调用方带入真值或场景标签，检测也不会读取它们。
    inputs = readings.loc[:, REQUIRED_COLUMNS]
    for run_id, group in inputs.groupby("run_id", sort=False, dropna=False):
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("Run ID must be a nonempty string.")

        window: deque[float] = deque(maxlen=settings.window_samples)
        streak = 0
        instant_latched = False
        trailing_latched = False
        previous_elapsed: int | None = None
        previous_interval: int | None = None
        previous_time: pd.Timestamp | None = None

        for sample in group.itertuples(index=False):
            elapsed, interval = sample.elapsed_s, sample.interval_s
            if not isinstance(elapsed, Integral) or isinstance(elapsed, bool) or elapsed < 0:
                raise ValueError(f"Run {run_id}: elapsed_s must be a nonnegative integer.")
            if not isinstance(interval, Integral) or isinstance(interval, bool) or interval < 1:
                raise ValueError(f"Run {run_id}: interval_s must be a positive integer.")
            timestamp = _sample_time(sample.sample_time_utc)
            gap_before = False
            if previous_elapsed is not None:
                if interval != previous_interval or elapsed <= previous_elapsed:
                    raise ValueError(
                        f"Run {run_id}: duplicate, out-of-order, or changed sampling interval."
                    )
                if timestamp - previous_time != timedelta(seconds=elapsed - previous_elapsed):
                    raise ValueError(f"Run {run_id}: timestamp and elapsed_s disagree.")
                expected = previous_elapsed + previous_interval
                if elapsed < expected:
                    raise ValueError(f"Run {run_id}: overlapping samples.")
                gap_before = elapsed > expected
            previous_elapsed, previous_interval, previous_time = elapsed, interval, timestamp

            if gap_before:
                # 缺测会断开因果窗口，但不能把此前报警自动当作已处理。
                window.clear()
                streak = 0

            invalid_reason = _sample_quality(sample)
            quality_state = "invalid" if invalid_reason else "valid"
            residual = float("nan")
            if not invalid_reason:
                residual = sample.density_kgm3 * (sample.qin_m3h - sample.qout_m3h) / 3600

            common = {
                "run_id": run_id,
                "sample_time_utc": sample.sample_time_utc,
                "elapsed_s": elapsed,
                "interval_s": interval,
                "residual_kgs": residual,
                "threshold_kgs": threshold,
                "instrument_status": sample.instrument_status,
                "quality_state": quality_state,
                "invalid_reason": invalid_reason,
                "gap_before": gap_before,
            }

            if invalid_reason:
                instant_state = "unavailable"
                trailing_state = "unavailable"
                window.clear()
                streak = 0
            else:
                instant_latched = residual > threshold
                instant_state = "alarm" if instant_latched else "normal"
                window.append(residual)
                if len(window) < settings.window_samples:
                    trailing_state = "warming"
                    streak = 0
                else:
                    smoothed = sum(window) / settings.window_samples
                    if smoothed > threshold:
                        streak += 1
                        if streak >= settings.confirmation_samples:
                            trailing_latched = True
                            trailing_state = "alarm"
                        else:
                            trailing_state = "pending"
                    else:
                        streak = 0
                        trailing_latched = False
                        trailing_state = "normal"

            records.append({
                **common,
                "method_version": f"instant-{settings.version}",
                "smoothed_residual_kgs": float("nan"),
                "window_samples_available": 1 if not invalid_reason else 0,
                "confirmations": int(instant_latched) if not invalid_reason else 0,
                "alarm_state": instant_state,
                "alarm_latched": instant_latched,
            })
            records.append({
                **common,
                "method_version": f"trailing-{settings.version}",
                "smoothed_residual_kgs": sum(window) / settings.window_samples
                if not invalid_reason and len(window) == settings.window_samples else float("nan"),
                "window_samples_available": len(window),
                "confirmations": streak,
                "alarm_state": trailing_state,
                "alarm_latched": trailing_latched,
            })

    return pd.DataFrame.from_records(records, columns=RESULT_COLUMNS)
