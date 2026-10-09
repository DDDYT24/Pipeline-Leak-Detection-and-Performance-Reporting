"""SI-unit, horizontal, single-phase pipe calculations; Darcy friction factors."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Pipeline:
    length_m: float
    diameter_m: float
    density_kgm3: float
    dynamic_viscosity_pas: float
    roughness_m: float
    nominal_flow_m3h: float
    outlet_pressure_pa: float

    def __post_init__(self):
        positive = (self.length_m, self.diameter_m, self.density_kgm3,
                    self.dynamic_viscosity_pas, self.nominal_flow_m3h)
        if any(not math.isfinite(x) or x <= 0 for x in positive):
            raise ValueError("Length, diameter, density, viscosity and nominal flow must be positive and finite.")
        if not math.isfinite(self.roughness_m) or not 0 <= self.roughness_m < self.diameter_m:
            raise ValueError("Roughness must be finite and between zero and pipe diameter.")
        if not math.isfinite(self.outlet_pressure_pa) or self.outlet_pressure_pa <= 0:
            raise ValueError("Outlet pressure must be positive and finite.")


def pipe_flow(pipeline: Pipeline, flow_m3h: float, length_m: float | None = None) -> dict:
    """Compute steady friction loss. No elevation, local losses, pumps or transients."""
    if not math.isfinite(flow_m3h) or flow_m3h < 0:
        raise ValueError("Flow must be finite and nonnegative; reverse flow is not supported.")
    length = pipeline.length_m if length_m is None else length_m
    if not math.isfinite(length) or length <= 0:
        raise ValueError("Segment length must be positive and finite.")
    q = flow_m3h / 3600.0
    area = math.pi * pipeline.diameter_m**2 / 4
    velocity = q / area
    re = pipeline.density_kgm3 * velocity * pipeline.diameter_m / pipeline.dynamic_viscosity_pas
    if re == 0:
        factor, regime = 0.0, "stopped"
    elif re < 2300:
        factor, regime = 64.0 / re, "laminar"
    elif re < 4000:
        raise ValueError("Transitional Reynolds numbers are outside the starter model.")
    else:
        factor = (-1.8 * math.log10((pipeline.roughness_m / pipeline.diameter_m / 3.7)**1.11 + 6.9 / re))**-2
        regime = "turbulent"
    dp = factor * length / pipeline.diameter_m * pipeline.density_kgm3 * velocity**2 / 2
    return {"velocity_ms": velocity, "reynolds": re, "darcy_f": factor,
            "friction_dp_pa": dp, "mass_flow_kgs": pipeline.density_kgm3 * q,
            "flow_regime": regime}


def segment_state(pipeline: Pipeline, leak_m3h: float) -> dict:
    """Prescribed midpoint mass sink with fixed inlet flow and outlet pressure."""
    if not math.isfinite(leak_m3h) or not 0 <= leak_m3h < pipeline.nominal_flow_m3h:
        raise ValueError("Prescribed leak must be nonnegative and below inlet flow.")
    upstream = pipe_flow(pipeline, pipeline.nominal_flow_m3h, pipeline.length_m / 2)
    q_out = pipeline.nominal_flow_m3h - leak_m3h
    downstream = pipe_flow(pipeline, q_out, pipeline.length_m / 2)
    node_pressure = pipeline.outlet_pressure_pa + downstream["friction_dp_pa"]
    return {"qin_m3h": pipeline.nominal_flow_m3h, "qout_m3h": q_out,
            "pin_pa": node_pressure + upstream["friction_dp_pa"],
            "pout_pa": pipeline.outlet_pressure_pa,
            "midpoint_pressure_pa": node_pressure}
