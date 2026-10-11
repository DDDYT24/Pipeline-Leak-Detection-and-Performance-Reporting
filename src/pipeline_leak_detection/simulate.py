"""生成可复现的合成测量，并将泄漏真值单独导出。"""

from dataclasses import replace
from datetime import datetime, timedelta
import math
import numpy as np
import pandas as pd

from .hydraulics import Pipeline, pipe_flow, segment_state


def _window(start: int, end: int, interval: int, duration: int) -> None:
    """所有信号注入与真值使用对齐的半开区间。"""
    if (
        any(not isinstance(x, int) or isinstance(x, bool) for x in (start, end))
        or not 0 <= start < end <= duration or start % interval or end % interval
    ):
        raise ValueError("Signal boundaries must align with samples within the simulation.")


def _operating_flow(operation: dict | None, elapsed: int, nominal: float) -> tuple[float, float, str]:
    """指定启停流量斜坡与临时差值；不求解液压瞬态或库存。"""
    if operation is None:
        return nominal, 0.0, "steady"
    start, end = operation["startup_start_s"], operation["startup_end_s"]
    stop, stopped = operation["shutdown_start_s"], operation["shutdown_end_s"]
    if elapsed < start or elapsed >= stopped:
        return 0.0, 0.0, "stopped"
    if elapsed < end:
        flow = nominal * (elapsed - start) / (end - start)
        return flow, flow * operation["imbalance_fraction"], "transient"
    if elapsed >= stop:
        flow = nominal * (stopped - elapsed) / (stopped - stop)
        return flow, -flow * operation["imbalance_fraction"], "transient"
    return nominal, 0.0, "steady"


def generate(config: dict) -> dict[str, pd.DataFrame]:
    """按配置生成逐点读数、回放信息和独立真值三张表。"""
    pipeline = Pipeline(**config["pipeline"])
    sim = config["simulation"]
    interval, duration = sim["interval_s"], sim["duration_s"]
    if any(not isinstance(x, int) or isinstance(x, bool) or x <= 0 for x in (interval, duration)) or duration % interval:
        raise ValueError("Duration and interval must be positive integers with no partial interval.")
    onset, end = sim["leak_onset_s"], sim["leak_end_s"]
    _window(onset, end, interval, duration)
    for name in ("meter_noise_std_fraction", "pressure_noise_std_pa"):
        if not math.isfinite(sim[name]) or sim[name] < 0:
            raise ValueError("Noise standard deviations must be finite and nonnegative.")
    start = datetime.fromisoformat(sim["synthetic_start_utc"])
    if start.utcoffset() != timedelta(0):
        raise ValueError("Synthetic start must have an explicit UTC offset.")
    ids = [s["run_id"] for s in config["scenarios"]]
    if not ids or any(not isinstance(x, str) or not x.strip() for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("Run IDs must be nonempty and unique.")
    readings, runs, truth = [], [], []
    reference = pipe_flow(pipeline, pipeline.nominal_flow_m3h)
    for index, scenario in enumerate(config["scenarios"]):
        fraction = scenario["leak_fraction"]
        if not math.isfinite(fraction) or not 0 <= fraction < 1:
            raise ValueError("Leak fraction must be finite and between zero and one.")
        leak_rate = fraction * pipeline.nominal_flow_m3h
        scenario_onset = scenario.get("leak_onset_s", onset)
        scenario_end = scenario.get("leak_end_s", end)
        _window(scenario_onset, scenario_end, interval, duration)
        seed = scenario["seed"]
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ValueError("Seed must be a nonnegative integer.")
        noise_fraction = scenario.get("meter_noise_std_fraction", sim["meter_noise_std_fraction"])
        correlation = scenario.get("noise_ar1", 0.0)
        if not math.isfinite(noise_fraction) or noise_fraction < 0:
            raise ValueError("Meter noise must be finite and nonnegative.")
        if not math.isfinite(correlation) or not 0 <= correlation < 1:
            raise ValueError("AR(1) correlation must be finite and in [0, 1).")
        operation = scenario.get("operation")
        if operation is not None:
            _window(operation["startup_start_s"], operation["startup_end_s"], interval, duration)
            _window(operation["shutdown_start_s"], operation["shutdown_end_s"], interval, duration)
            if operation["startup_end_s"] > operation["shutdown_start_s"]:
                raise ValueError("Startup and shutdown must not overlap.")
            if not math.isfinite(operation["imbalance_fraction"]) or not 0 <= operation["imbalance_fraction"] < 1:
                raise ValueError("Operating imbalance must be finite and in [0, 1).")
        faults = scenario.get("faults", [])
        for fault in faults:
            _window(fault["start_s"], fault["end_s"], interval, duration)
            if fault["kind"] not in {"spike", "bias", "missing", "suspect_stuck", "dropout"}:
                raise ValueError("Unknown measurement fault kind.")
            if fault.get("channel", "qin_m3h") not in {"qin_m3h", "qout_m3h"}:
                raise ValueError("Fault channel must be a flow measurement.")
            if fault["kind"] in {"spike", "bias"} and not math.isfinite(fault["offset_m3h"]):
                raise ValueError("Meter offset must be finite.")
        if any(
            max(left["start_s"], right["start_s"]) < min(left["end_s"], right["end_s"])
            for i, left in enumerate(faults) for right in faults[i + 1:]
        ):
            raise ValueError("Overlapping fault injections are outside V1.")
        rng = np.random.default_rng(scenario["seed"])
        run_start = start + timedelta(days=index)
        previous_noise = None
        previous_flows = None
        stuck_values = {}
        states = {}
        for elapsed in range(0, duration, interval):
            active = leak_rate > 0 and scenario_onset <= elapsed < scenario_end
            commanded, disturbance, operating_state = _operating_flow(operation, elapsed, pipeline.nominal_flow_m3h)
            prescribed_leak = leak_rate if active else 0.0
            if commanded == 0:
                if prescribed_leak:
                    raise ValueError("A prescribed sink requires positive commanded inlet flow.")
                state = {"qin_m3h": 0.0, "qout_m3h": 0.0,
                         "pin_pa": pipeline.outlet_pressure_pa, "pout_pa": pipeline.outlet_pressure_pa}
            else:
                key = (commanded, prescribed_leak)
                if key not in states:
                    states[key] = segment_state(replace(pipeline, nominal_flow_m3h=commanded), prescribed_leak)
                state = states[key]
            # AR(1) 只读取上一噪声；各流量通道独立，初始状态为平稳分布。
            standard_noise = rng.normal(0, 1, 2)
            if previous_noise is not None:
                standard_noise = correlation * previous_noise + math.sqrt(1 - correlation**2) * standard_noise
            previous_noise = standard_noise
            flow_noise = standard_noise * commanded * noise_fraction
            pressure_noise = rng.normal(0, sim["pressure_noise_std_pa"], 2)
            flows = {"qin_m3h": state["qin_m3h"] + flow_noise[0],
                     "qout_m3h": state["qout_m3h"] - disturbance + flow_noise[1]}
            status = "valid" if all(value >= 0 for value in flows.values()) else "suspect"
            dropped = False
            for fault_index, fault in enumerate(faults):
                if not fault["start_s"] <= elapsed < fault["end_s"]:
                    continue
                kind, channel = fault["kind"], fault.get("channel", "qin_m3h")
                if kind in {"spike", "bias"}:
                    flows[channel] += fault["offset_m3h"]
                elif kind == "missing":
                    flows[channel], status = float("nan"), "missing"
                elif kind == "suspect_stuck":
                    if fault_index not in stuck_values:
                        stuck_values[fault_index] = previous_flows[channel] if previous_flows else flows[channel]
                    flows[channel], status = stuck_values[fault_index], "suspect"
                else:
                    dropped = True
            previous_flows = flows.copy()
            if dropped:
                continue
            qin, qout = flows["qin_m3h"], flows["qout_m3h"]
            if qin < 0 or qout < 0:
                status = "suspect"
            readings.append({
                "run_id": scenario["run_id"],
                "sample_time_utc": (run_start + timedelta(seconds=elapsed)).isoformat(),
                "elapsed_s": elapsed, "interval_s": interval,
                "qin_m3h": qin, "qout_m3h": qout,
                "pin_pa": state["pin_pa"] + pressure_noise[0],
                "pout_pa": state["pout_pa"] + pressure_noise[1],
                "density_kgm3": pipeline.density_kgm3,
                "mass_imbalance_kgs": pipeline.density_kgm3 * (qin - qout) / 3600,
                "instrument_status": status,
                "operating_state": operating_state,
            })
        runs.append({"run_id": scenario["run_id"], "seed": scenario["seed"],
                     "split": scenario.get("split", "starter_demo"),
                     "scenario": scenario.get("scenario", "starter"),
                     "noise_profile": scenario.get("noise_profile", "starter"),
                     "meter_noise_std_fraction": noise_fraction, "noise_ar1": correlation,
                     "synthetic_test_date": run_start.date().isoformat(),
                     "config_version": config["config_version"], "leak_fraction": fraction,
                     "scheduled_hours": duration / 3600, **reference})
        if leak_rate:
            truth.append({"truth_event_id": scenario["run_id"] + "-event-01", "run_id": scenario["run_id"],
                          "onset_s": scenario_onset, "end_s": scenario_end, "true_leak_m3h": leak_rate,
                          "true_release_m3": leak_rate * (scenario_end - scenario_onset) / 3600})
    return {"readings": pd.DataFrame(readings), "runs": pd.DataFrame(runs),
            "truth": pd.DataFrame(truth, columns=["truth_event_id", "run_id", "onset_s", "end_s", "true_leak_m3h", "true_release_m3"])}
