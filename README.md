# Pipeline Leak Detection and Performance Reporting

A Python engineering project exploring liquid-pipeline mass balance, measurement uncertainty, and the evidence needed to evaluate leak-detection decisions.

## Motivation

A difference between inlet and outlet flow can indicate a leak, but it can also result from measurement noise, instrument bias, or changing inventory during pipeline operations. A useful monitoring system must make these distinctions visible and explain the limits of its decisions.

The project examines three practical engineering questions:

- Which short-lived measurement disturbances can smoothing reduce, and which persistent errors remain?
- How much detection delay does a more persistent alarm rule introduce?
- Why must missing or suspect instrument data be reported as unavailable rather than normal operation?

These questions motivate the project. The current implementation includes physical calculations, reproducible measurements, two causal detection rules, and event-level evaluation on a small synthetic starter set. Broader scenario testing and independent validation remain open.

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
- A current-sample threshold baseline and a causal six-sample average with three-sample confirmation.
- Explicit `unavailable` and `warming` states, with unresolved alarms retained through data gaps.
- Per-sample detector output with rule version, residual, threshold, state, and data-quality reason.
- Alarm episodes that retain open status and data interruptions until a valid rule evaluation clears them.
- One-to-one matching of new alarms to separate leak truth, including late and missed events.
- Run-level counts, exposure hours, monitoring coverage, and false-alarm frequency with explicit denominators.
- An export manifest containing assumptions, configuration, package versions, and row counts.
- Automated checks for hydraulic references, conservation, reproducibility, causal decisions, and invalid inputs.

The starter dataset is too small and simple to establish a general false-alarm reduction or operating performance. Independent scenario evaluation and a shareable Power BI performance report are not included in this snapshot.

## Data flow

```text
Pipeline and scenario configuration
    → Steady physical state
    → Noisy instrument measurements
    → Per-sample data-quality and detector decisions
    → Alarm episodes and event-level evaluation against separate truth
    → CSV exports and provenance manifest
```

Measurement data can be imported into Power BI Desktop. Python handles the engineering calculations; the CSV interface keeps analysis independent of report presentation.

## Repository guide

| Path | Purpose |
| --- | --- |
| `configs/starter.json` | Assumed pipeline, sampling, noise, leak scenarios, and detector settings |
| `src/pipeline_leak_detection/hydraulics.py` | Steady hydraulic calculations and prescribed midpoint leak state |
| `src/pipeline_leak_detection/simulate.py` | Reproducible measurement, run, and separate event-truth generation |
| `src/pipeline_leak_detection/detector.py` | Measurement-quality checks and two causal, sample-level detection rules |
| `src/pipeline_leak_detection/alarms.py` | Confirmed-alarm episodes, resolution, and interruption tracking |
| `src/pipeline_leak_detection/evaluation.py` | Truth matching, missed events, exposure denominators, and run metrics |
| `src/pipeline_leak_detection/cli.py` | Configuration loading, CSV export, and provenance manifest |
| `tests/test_starter.py`, `tests/test_detector.py`, `tests/test_evaluation.py` | Hydraulic, detector, event-evaluation, and export checks |
| `pyproject.toml`, `requirements-lock.txt` | Package metadata and repeatable dependency setup |
| `.vscode/` | Local interpreter and data-generation/test tasks |

`data/generated/` holds local CSV output. `reports/` holds local Power BI files. Their generated contents are excluded from Git.

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

## Detection rules and states

Both rules use the positive mass-flow residual `density × (inlet flow − outlet flow) / 3600`, in kg/s. The default threshold is strictly greater than 1% of nominal mass flow, or approximately 1.889 kg/s (8 m³/h) for the starter configuration. This is an illustrative fixed threshold, not a calibrated operating setting.

The `instant-v1` rule evaluates each valid sample. The `trailing-v1` rule averages the current and previous five valid samples and requires three consecutive above-threshold averages. Six samples taken ten seconds apart span 50 seconds from first to last sample; the rule does not model pipeline inventory or transient hydraulics.

Missing flow values, suspect instrument status, and nonfinite or negative flow readings are `unavailable`. The trailing window resets after an invalid reading or a time gap and reports `warming` until six new valid samples are present. A confirmed alarm remains latched across an unavailable or warming period until a valid rule evaluation can clear it. The output distinguishes the current evaluation state from that retained alarm status.

The detector reads only measurement fields; it uses `run_id` to keep replay histories separate. Event truth and scenario labels remain outside its input. A sample-level alarm is not an independently matched leak event.

## Event evaluation

Consecutive latched alarm samples form one episode. Invalid or warming samples and missing intervals interrupt evaluation without resolving an existing alarm. An episode is resolved only when a valid rule evaluation clears the latch; otherwise it remains open at the run boundary.

After detection, the evaluator compares alarm starts with separate synthetic truth. Each truth event can match only one newly started alarm in its half-open interval `[onset_s, end_s)`. An alarm already active before onset cannot detect the new event. Another alarm starting during the same leak is counted separately as a duplicate. An unmatched alarm beginning outside a leak interval counts as a false alarm under this predefined rule, including one first triggered after a leak ends. A match after 120 seconds remains an anytime detection but misses the 120-second target.

`run_metrics.csv` keeps counts alongside scheduled, observed, valid, evaluable, and evaluable no-leak hours. Evaluation coverage is evaluable hours divided by scheduled hours. False alarms per 24 hours are false alarm episodes divided by evaluable no-leak hours, multiplied by 24. Detection rate is undefined when there are no truth events; false-alarm frequency is undefined when there are no eligible no-leak hours. Missing denominators are exported as blank values, never as zero risk. `true_release_before_alarm_m3` uses the generator's known release rate and detection delay; it is not a leak-volume estimate produced by the detector.

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
| `detector_results.csv` | One run, sample, and detection rule | Residual, smoothed residual, fixed threshold, quality reason, decision state, and retained alarm status |
| `alarms.csv` | One confirmed alarm episode | Start, resolution or open status, interrupted duration, and truth-match classification |
| `event_evaluation.csv` | One truth leak and detection rule | Matched alarm, delay, 120-second result, eligible-window coverage, and synthetic release before alarm |
| `run_metrics.csv` | One run and detection rule | Event counts, misses, false alarms, coverage, exposure hours, and normalized frequency |
| `manifest.json` | One export | Data origin, assumptions, exact configuration, dependencies, and row counts |

The default configuration produces 2,160 measurement rows, 4,320 detector rows, four alarm episodes, four truth-by-method evaluation rows, and six run-by-method metric rows across three two-hour runs sampled every ten seconds. Timestamps are synthetic and use UTC. Interval starts are inclusive and end boundaries are exclusive. Run metadata and truth are kept separate from detector inputs; the demo is not a held-out evaluation dataset.

## Verification

Thirty-one automated checks cover:

- SI conversions and nominal hydraulic reference values.
- Comparison of the Haaland Darcy factor with the `fluids` friction calculation.
- Midpoint mass conservation and pressure ordering.
- Zero-noise consistency between measurements and separate event truth.
- Seed reproducibility and unique run timestamps.
- Invalid geometry, leak rates, and partial sampling intervals.
- Stopped and laminar flow handling.
- Expected alarm timing for a zero-noise step leak and rejection of a single transient spike.
- Instrument failure, time gaps, retained alarms, run isolation, causal prefix behavior, and structural input errors.
- Alarm episode boundaries, open alarms through invalid data, one-to-one truth matching, late and missed events, zero denominators, and inconsistent input rejection.
- End-to-end export of separate measurement, truth, detector, and manifest files.

For the seeded starter replay, the 2% leak has matched alarm delays of 0 and 40 seconds for the instant and trailing rules; the 5% leak has delays of 0 and 30 seconds. All four matched episodes resolve after their leak windows; the normal starter run has no alarm episodes. The trailing rule's synthetic true release before alarm is about 0.178 m³ for the 2% leak and 0.333 m³ for the 5% leak. A separate zero-noise 2% step test produces a 50-second difference under the configured rules. These example results do not demonstrate an overall false-alarm improvement or a field detection rate.

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
