"""验证逐点规则的时间因果性、数据失效和回放隔离。"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from pipeline_leak_detection.detector import DetectionSettings, detect
from pipeline_leak_detection.simulate import generate


@pytest.fixture
def starter_config():
    """使用项目公开配置，避免测试和实际运行采用两套参数。"""
    path = Path(__file__).parents[1] / "configs/starter.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _detect(readings: pd.DataFrame, config: dict) -> pd.DataFrame:
    """用实际配置执行检测，简化测试中重复的参数传递。"""
    return detect(
        readings,
        DetectionSettings(**config["detection"]),
        config["pipeline"]["nominal_flow_m3h"],
        config["pipeline"]["density_kgm3"],
    )


def _readings(
    differences_m3h: list[float],
    run_id: str = "case",
    elapsed: list[int] | None = None,
) -> pd.DataFrame:
    """构造可精确预期的测量序列，真值不进入检测输入。"""
    elapsed = elapsed if elapsed is not None else list(range(0, len(differences_m3h) * 10, 10))
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return pd.DataFrame({
        "run_id": [run_id] * len(elapsed),
        "sample_time_utc": [(start + timedelta(seconds=t)).isoformat() for t in elapsed],
        "elapsed_s": elapsed,
        "interval_s": [10] * len(elapsed),
        "qin_m3h": [800.0] * len(elapsed),
        "qout_m3h": [800.0 - difference for difference in differences_m3h],
        "density_kgm3": [850.0] * len(elapsed),
        "instrument_status": ["valid"] * len(elapsed),
    })


def test_step_leak_delay_and_truth_isolation(starter_config):
    """零噪声阶跃应显示直接触发与六点平均加确认的时间差。"""
    starter_config["simulation"]["meter_noise_std_fraction"] = 0
    starter_config["simulation"]["pressure_noise_std_pa"] = 0
    frames = generate(starter_config)
    readings = frames["readings"]
    result = _detect(readings, starter_config)

    leak = result.loc[result["run_id"] == "leak-02-001"]
    first = leak.loc[leak["alarm_state"] == "alarm"].groupby("method_version")["elapsed_s"].min()
    assert first["instant-v1"] == 1200
    assert first["trailing-v1"] == 1250
    assert not result.loc[result["run_id"] == "normal-001", "alarm_latched"].any()

    # 即使调用方误传真值列，检测实现也只能使用允许的测量列。
    with_labels = readings.assign(true_leak_m3h=999, leak_fraction=1.0)
    pd.testing.assert_frame_equal(result, _detect(with_labels, starter_config))


def test_single_spike_does_not_trigger_confirmed_trailing_alarm(starter_config):
    """短尖峰触发即时基线，但不满足持续性规则。"""
    readings = _readings([0.0] * 8 + [24.0] + [0.0] * 8)
    result = _detect(readings, starter_config)
    instant = result.loc[result["method_version"] == "instant-v1"]
    trailing = result.loc[result["method_version"] == "trailing-v1"]
    assert instant.loc[instant["alarm_state"] == "alarm", "elapsed_s"].tolist() == [80]
    assert not trailing["alarm_latched"].any()


def test_invalid_sample_resets_window_without_clearing_existing_alarm(starter_config):
    """失效点和恢复后的预热均不可评价；原有报警保持未解决。"""
    starter_config["detection"].update(window_samples=2, confirmation_samples=1)
    readings = _readings([16.0] * 6 + [0.0] * 2)
    readings.loc[3, "qout_m3h"] = float("nan")
    result = _detect(readings, starter_config)
    trailing = result.loc[result["method_version"] == "trailing-v1"].set_index("elapsed_s")
    assert trailing.loc[20, "alarm_state"] == "alarm"
    assert trailing.loc[30, "alarm_state"] == "unavailable"
    assert pd.isna(trailing.loc[30, "residual_kgs"])
    assert bool(trailing.loc[30, "alarm_latched"])
    assert trailing.loc[40, "alarm_state"] == "warming"
    assert bool(trailing.loc[40, "alarm_latched"])
    assert trailing.loc[50, "alarm_state"] == "alarm"
    assert trailing.loc[70, "alarm_state"] == "normal"
    assert not bool(trailing.loc[70, "alarm_latched"])


def test_missing_interval_and_new_run_reset_window(starter_config):
    """缺测与回放切换不能借用旧窗口，缺测前的报警仍需保留。"""
    starter_config["detection"].update(window_samples=2, confirmation_samples=1)
    first = _readings([16.0, 16.0, 16.0], elapsed=[0, 10, 40])
    second = _readings([0.0, 0.0], run_id="other")
    result = _detect(pd.concat([first, second], ignore_index=True), starter_config)
    trailing = result.loc[result["method_version"] == "trailing-v1"]
    gap = trailing.loc[(trailing["run_id"] == "case") & (trailing["elapsed_s"] == 40)].iloc[0]
    assert bool(gap["gap_before"])
    assert gap["alarm_state"] == "warming"
    assert bool(gap["alarm_latched"])
    other = trailing.loc[trailing["run_id"] == "other"]
    assert other["alarm_state"].tolist() == ["warming", "normal"]
    assert not other["alarm_latched"].any()


def test_suspect_meter_and_causal_prefix(starter_config):
    """可疑仪表不显示正常；追加未来数据不应改写已有判断。"""
    readings = _readings([0.0] * 8 + [16.0] * 8)
    readings.loc[9, "instrument_status"] = "suspect"
    result = _detect(readings, starter_config)
    suspect = result.loc[result["elapsed_s"] == 90]
    assert suspect["alarm_state"].tolist() == ["unavailable", "unavailable"]
    assert suspect["quality_state"].tolist() == ["invalid", "invalid"]
    assert suspect["invalid_reason"].tolist() == ["instrument_status", "instrument_status"]
    prefix = _detect(readings.iloc[:11], starter_config)
    pd.testing.assert_frame_equal(prefix, result.iloc[:len(prefix)].reset_index(drop=True))


def test_cli_exports_detector_results_without_changing_truth(starter_config, tmp_path):
    """端到端导出逐点结果，同时保留独立的事件真值与配置记录。"""
    root = Path(__file__).parents[1]
    completed = subprocess.run(
        [
            sys.executable, "-m", "pipeline_leak_detection",
            "--config", str(root / "configs/starter.json"),
            "--output", str(tmp_path),
        ],
        cwd=root, check=True, capture_output=True, text=True,
    )
    assert "detector_results: 4320 rows" in completed.stdout
    result = pd.read_csv(tmp_path / "detector_results.csv")
    truth = pd.read_csv(tmp_path / "truth.csv")
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert len(result) == 4320
    assert len(truth) == 2
    assert not set(truth.columns).intersection(set(result.columns) - {"run_id"})
    assert manifest["rows"]["detector_results"] == 4320
    assert manifest["config"]["detection"] == starter_config["detection"]


@pytest.mark.parametrize("broken", ["duplicate", "reverse", "wrong_time", "missing_column"])
def test_structural_input_errors_are_rejected(starter_config, broken):
    """拒绝无法可靠确定采样先后和单位粒度的输入。"""
    readings = _readings([0.0, 0.0, 0.0])
    if broken == "duplicate":
        readings.loc[2, "elapsed_s"] = 10
    elif broken == "reverse":
        readings.loc[2, "elapsed_s"] = 5
    elif broken == "wrong_time":
        readings.loc[2, "sample_time_utc"] = readings.loc[1, "sample_time_utc"]
    else:
        readings = readings.drop(columns="interval_s")
    with pytest.raises(ValueError):
        _detect(readings, starter_config)


@pytest.mark.parametrize("value", [0, -0.1, float("nan"), 1.0])
def test_invalid_threshold_is_rejected(value):
    """不允许零阈值、负阈值、非有限值或超过额定流量的比例。"""
    with pytest.raises(ValueError):
        DetectionSettings(value, 6, 3, "v1")
