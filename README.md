# Pipeline Leak Detection and Performance Reporting

A Python engineering project exploring liquid-pipeline mass balance, measurement uncertainty, and the evidence needed to evaluate leak-detection decisions.

## Motivation

A difference between inlet and outlet flow can indicate a leak, but it can also result from measurement noise, instrument bias, or changing inventory during pipeline operations. A useful monitoring system must make these distinctions visible and explain the limits of its decisions.

The project examines three practical engineering questions:

- Which short-lived measurement disturbances can smoothing reduce, and which persistent errors remain?
- How much detection delay does a more persistent alarm rule introduce?
- Why must missing or suspect instrument data be reported as unavailable rather than normal operation?

These questions motivate the project. The current implementation establishes the physical calculations and reproducible measurement data needed to investigate them; it does not yet provide measured detector-performance answers.

## Incident background

On July 25, 2010, Enbridge Line 6B ruptured near Marshall, Michigan, during the final stages of a planned shutdown. The NTSB investigation documented that the rupture was not discovered or addressed for more than 17 hours, and two subsequent startups contributed 81% of the total release. The case highlights the importance of interpreting abnormal conditions and response delays during operational changes. [NTSB investigation](https://www-s.ntsb.gov/investigations/Pages/DCA10MP007.aspx)

PHMSA describes volume balance, mass balance, rate-of-change methods, and Computational Pipeline Monitoring (CPM) as approaches whose effectiveness depends on the pipeline, product, instrumentation, and operating conditions. [PHMSA leak-detection overview](https://primis.phmsa.dot.gov/stakeholder-comms/factsheets/fsleakdetectionsystems/)

The incident provides background for the engineering questions. Generated measurements are not a reconstruction of the incident or records from any operating pipeline.

## Implemented scope

- Configuration-driven, horizontal single-phase liquid-pipe calculations.
- Flow velocity, Reynolds number, Darcy friction factor, friction pressure loss, and mass flow.
- A prescribed midpoint leak with mass conservation across two pipe segments.
- Reproducible normal, 2% leak, and 5% leak scenarios with independent measurement noise.
- Separate CSV exports for measurements, run metadata, and event ground truth.
- An export manifest containing assumptions, configuration, package versions, and row counts.
- Automated checks for engineering reference values, conservation, reproducibility, and invalid inputs.

The repository currently contains the data-generation and hydraulics foundation. Detection algorithms, event-level performance metrics, and a Power BI report are not included in this snapshot.

## Data flow

```text
Pipeline and scenario configuration
    → Steady physical state
    → Noisy instrument measurements
    → CSV exports and provenance manifest
```

Measurement data can be imported into Power BI Desktop. Python handles the engineering calculations; the CSV interface keeps analysis independent of report presentation.

## Model and units

The model assumes one horizontal, constant-diameter pipe carrying a single-phase liquid with fixed density and viscosity. There are no elevation changes, branches, local fitting losses, intermediate pumps, or transient inventory changes.

Flow supplied in m³/h is converted to m³/s:

```text
A = πD² / 4
v = Q / A
Re = ρvD / μ
ΔP = f_D (L/D) ρv²/2
ṁ = ρQ
```

Friction factors are Darcy factors. Laminar flow uses `64/Re`; turbulent flow uses the Haaland approximation. Transitional flow is rejected rather than assigned an unsupported calculation. Stopped flow has zero friction loss.

The prescribed leak withdraws flow at the midpoint. Inlet flow is fixed, outlet pressure is fixed, and each half of the pipe uses its own flow to compute friction loss:

```text
Q_in = Q_out + Q_leak
```

This is a steady-state prescribed-sink model, not a leak-orifice or transient-wave solver. A sudden change in the synthetic leak schedule switches between steady states without resolving the physical transition.

## Reproduce the data

Use Python 3.14 on Windows to reproduce the pinned environment, verified with Python 3.14.5.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pipeline_leak_detection --config configs/starter.json
.\.venv\Scripts\python.exe -m pytest -q
```

The export directory defaults to `data/generated`. Re-running the generator replaces its generated CSV and manifest files. Generated outputs and the local environment are excluded from version control.

| File | Grain | Contents |
| --- | --- | --- |
| `readings.csv` | One run and one sampling interval | Noisy flow and pressure, mass imbalance, and instrument status |
| `runs.csv` | One run | Seed, configuration version, synthetic date, and nominal hydraulic values |
| `truth.csv` | One synthetic leak event | Onset, end, actual prescribed leak rate, and release volume |
| `manifest.json` | One export | Data origin, assumptions, exact configuration, dependencies, and row counts |

The default configuration produces 2,160 measurement rows across three two-hour runs sampled every ten seconds. Timestamps are synthetic and use UTC. Interval starts are inclusive and end boundaries are exclusive. Run metadata and truth are kept separate from measurements; the demo is not a held-out evaluation dataset.

## Verification

Nine automated checks cover:

- SI conversions and nominal hydraulic reference values.
- Comparison of the Haaland Darcy factor with the `fluids` friction calculation.
- Midpoint mass conservation and pressure ordering.
- Zero-noise consistency between measurements and separate event truth.
- Seed reproducibility and unique run timestamps.
- Invalid geometry, leak rates, and partial sampling intervals.
- Stopped and laminar flow handling.

The nominal example uses assumed values: length 10 km, diameter 0.4 m, density 850 kg/m³, viscosity 0.005 Pa·s, roughness 0.000045 m, and flow 800 m³/h. It yields approximately 1.768 m/s velocity, Reynolds number 120,250, and 0.589 MPa friction loss.

These checks establish implementation consistency for the stated assumptions. They do not establish leak-detection performance on operating assets.

## Interpretation limits

All generated data are synthetic and use assumed parameters. The project has no connection to an operating pipeline or control system.

In general, inlet mass flow minus outlet mass flow includes both a possible leak and the rate of change of stored mass. The current steady-state model sets inventory change to zero. Its residual cannot be treated as a definitive leak estimate during startup or shutdown.

The measurement channels share the same simplified physical generator. Their agreement does not provide independent field validation. No deployment, industrial commissioning, or standards-compliance claim is made.

## References

- [NTSB: Enbridge Line 6B rupture and release](https://www-s.ntsb.gov/investigations/Pages/DCA10MP007.aspx).
- [PHMSA: Leak Detection Systems](https://primis.phmsa.dot.gov/stakeholder-comms/factsheets/fsleakdetectionsystems/).
- [fluids: Python fluid dynamics calculations](https://github.com/CalebBell/fluids), MIT-licensed dependency used for calculation cross-checks.
- [fluids friction-factor documentation](https://fluids.readthedocs.io/fluids.friction.html).
