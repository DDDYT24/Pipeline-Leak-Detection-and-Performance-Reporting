import copy
import json
from pathlib import Path

from fluids.friction import friction_factor
import pytest

from pipeline_leak_detection.hydraulics import Pipeline, pipe_flow, segment_state
from pipeline_leak_detection.simulate import generate


@pytest.fixture
def config():
    return json.loads((Path(__file__).parents[1] / "configs/starter.json").read_text())


def test_engineering_reference_and_independent_friction_check(config):
    pipeline = Pipeline(**config["pipeline"])
    result = pipe_flow(pipeline, 800)
    assert result["mass_flow_kgs"] == pytest.approx(850 * 800 / 3600)
    assert result["velocity_ms"] == pytest.approx(1.7683882566)
    assert result["friction_dp_pa"] == pytest.approx(588758.4045)
    exact_darcy = friction_factor(Re=result["reynolds"], eD=pipeline.roughness_m/pipeline.diameter_m)
    assert result["darcy_f"] == pytest.approx(exact_darcy, rel=0.02)


def test_mass_conservation_and_segment_pressure(config):
    pipeline = Pipeline(**config["pipeline"])
    state = segment_state(pipeline, 16)
    assert state["qin_m3h"] - state["qout_m3h"] == pytest.approx(16)
    assert state["pin_pa"] > state["midpoint_pressure_pa"] > state["pout_pa"]
    normal = segment_state(pipeline, 0)
    assert normal["pin_pa"] - normal["pout_pa"] == pytest.approx(pipe_flow(pipeline, 800)["friction_dp_pa"])


def test_zero_noise_measurements_match_separate_truth(config):
    config["simulation"]["meter_noise_std_fraction"] = 0
    config["simulation"]["pressure_noise_std_pa"] = 0
    frames = generate(config)
    assert len(frames["readings"]) == 2160
    assert len(frames["truth"]) == 2
    assert "true_leak_m3h" not in frames["readings"]
    normal = frames["readings"].query("run_id == 'normal-001'")
    assert (normal["mass_imbalance_kgs"] == 0).all()
    leaking = frames["readings"].query("run_id == 'leak-02-001' and 1200 <= elapsed_s < 6000")
    assert leaking["mass_imbalance_kgs"].to_numpy() == pytest.approx([850 * 16 / 3600] * 480)
    assert frames["truth"].iloc[0]["true_release_m3"] == pytest.approx(16 * 4800 / 3600)


def test_seed_reproducibility_and_timestamps(config):
    first, second = generate(config), generate(copy.deepcopy(config))
    assert first["readings"].equals(second["readings"])
    assert not first["readings"].duplicated(["run_id", "sample_time_utc"]).any()


@pytest.mark.parametrize("fraction", [-0.1, 1.0, float("nan")])
def test_invalid_leak_parameters_are_rejected(config, fraction):
    config["scenarios"][0]["leak_fraction"] = fraction
    with pytest.raises(ValueError):
        generate(config)


def test_invalid_geometry_and_partial_interval_are_rejected(config):
    config["pipeline"]["diameter_m"] = 0
    with pytest.raises(ValueError):
        generate(config)
    config["pipeline"]["diameter_m"] = 0.4
    config["simulation"]["duration_s"] = 7201
    with pytest.raises(ValueError):
        generate(config)


def test_stopped_and_laminar_flow(config):
    pipeline = Pipeline(**config["pipeline"])
    assert pipe_flow(pipeline, 0)["friction_dp_pa"] == 0
    low = pipe_flow(pipeline, 1)
    assert low["flow_regime"] == "laminar"
    assert low["darcy_f"] == pytest.approx(64 / low["reynolds"])
