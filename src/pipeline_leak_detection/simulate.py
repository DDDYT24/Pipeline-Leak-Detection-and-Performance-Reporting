"""Starter synthetic measurements. Ground truth is exported separately."""

from datetime import datetime, timedelta
import math
import numpy as np
import pandas as pd

from .hydraulics import Pipeline, pipe_flow, segment_state


def generate(config: dict) -> dict[str, pd.DataFrame]:
    pipeline = Pipeline(**config["pipeline"])
    sim = config["simulation"]
    interval, duration = sim["interval_s"], sim["duration_s"]
    if any(not isinstance(x, int) or isinstance(x, bool) or x <= 0 for x in (interval, duration)) or duration % interval:
        raise ValueError("Duration and interval must be positive integers with no partial interval.")
    onset, end = sim["leak_onset_s"], sim["leak_end_s"]
    if not 0 <= onset < end <= duration or onset % interval or end % interval:
        raise ValueError("Leak boundaries must align with samples within the simulation.")
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
        base, leaking = segment_state(pipeline, 0), segment_state(pipeline, leak_rate)
        rng = np.random.default_rng(scenario["seed"])
        run_start = start + timedelta(days=index)
        for elapsed in range(0, duration, interval):
            active = leak_rate > 0 and onset <= elapsed < end
            state = leaking if active else base
            flow_noise = rng.normal(0, pipeline.nominal_flow_m3h * sim["meter_noise_std_fraction"], 2)
            pressure_noise = rng.normal(0, sim["pressure_noise_std_pa"], 2)
            qin, qout = state["qin_m3h"] + flow_noise[0], state["qout_m3h"] + flow_noise[1]
            readings.append({
                "run_id": scenario["run_id"],
                "sample_time_utc": (run_start + timedelta(seconds=elapsed)).isoformat(),
                "elapsed_s": elapsed, "interval_s": interval,
                "qin_m3h": qin, "qout_m3h": qout,
                "pin_pa": state["pin_pa"] + pressure_noise[0],
                "pout_pa": state["pout_pa"] + pressure_noise[1],
                "density_kgm3": pipeline.density_kgm3,
                "mass_imbalance_kgs": pipeline.density_kgm3 * (qin - qout) / 3600,
                "instrument_status": "valid" if qin >= 0 and qout >= 0 else "suspect",
                "operating_state": "steady",
            })
        runs.append({"run_id": scenario["run_id"], "seed": scenario["seed"],
                     "split": "starter_demo", "synthetic_test_date": run_start.date().isoformat(),
                     "config_version": config["config_version"], "leak_fraction": fraction,
                     "scheduled_hours": duration / 3600, **reference})
        if leak_rate:
            truth.append({"truth_event_id": scenario["run_id"] + "-event-01", "run_id": scenario["run_id"],
                          "onset_s": onset, "end_s": end, "true_leak_m3h": leak_rate,
                          "true_release_m3": leak_rate * (end - onset) / 3600})
    return {"readings": pd.DataFrame(readings), "runs": pd.DataFrame(runs),
            "truth": pd.DataFrame(truth, columns=["truth_event_id", "run_id", "onset_s", "end_s", "true_leak_m3h", "true_release_m3"])}
