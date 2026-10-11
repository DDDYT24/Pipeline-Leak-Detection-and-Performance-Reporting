# Stage 4–5: difficult synthetic scenarios and frozen-rule evaluation

All signals are **100% synthetic with assumed parameters**. This study evaluates independent noise realizations from the project's own generator. It is not field validation, an incident reconstruction, or validation against an independent hydraulic simulator.

## Reproduce

Use the project-local Windows environment from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pipeline_leak_detection --config configs/starter.json
.\.venv\Scripts\python.exe -m pipeline_leak_detection --study configs/study.json --phase calibration
.\.venv\Scripts\python.exe -m pipeline_leak_detection --study configs/study.json --phase test --frozen-rule data/generated/study/calibration/frozen_rule.json
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/verify_study_exports.py
```

The study directories are separate from the starter data used by the existing local Power BI file. Re-running a phase replaces its generated output. Test output cannot share the source calibration directory. No Power BI application check or PBIX generation is part of these commands.

## Stage 4 signal definitions

Each run lasts 7,200 s, with 10 s intervals. The inlet nominal flow is 800 m³/h and density is 850 kg/m³. Leak fractions use fixed nominal flow, including during startup. Events normally occupy `[1200, 6000)` s.

| Scenario | Prescribed signal and truth |
| --- | --- |
| `normal` | Steady flow, no leak, independent inlet/outlet meter noise |
| `spike` | Inlet reading increased by 24 m³/h for `[1200, 1210)` s; no leak |
| `small_leak` | 0.5% nominal leak, 4 m³/h |
| `leak_2pct` | 2% nominal leak, 16 m³/h |
| `leak_5pct` | 5% nominal leak, 40 m³/h |
| `startup_shutdown` | Specified operating ramps and temporary imbalance; no leak |
| `startup_leak` | Same ramps, with a constant 16 m³/h leak during `[420, 6000)` s |
| `meter_bias` | Inlet reading increased by 16 m³/h during `[1200, 6000)` s; status remains valid; no leak |
| `missing_measurement` | Outlet reading is blank, status `missing`, during `[1190, 1500)` s |
| `suspect_stuck` | Outlet reading held at its previous value and externally marked `suspect` during `[1190, 1500)` s |
| `dropped_samples` | Entire measurement rows omitted during `[1190, 1500)` s |
| `leak_data_loss` | 16 m³/h leak plus missing outlet readings during `[1190, 1500)` s |

The specified inlet flow is zero before 300 s, rises linearly from zero to nominal over `[300, 600)` s, stays nominal until 6600 s, falls linearly to zero over `[6600, 6900)` s, then stays stopped. During startup, the outlet signal is reduced by 3% of the current commanded flow; during shutdown it is increased by that amount. Thus the measurement residual includes a prescribed operating disturbance in addition to any leak and meter noise.

Pressure channels follow steady reference calculations for each commanded flow and prescribed sink, plus pressure noise. They do **not** resolve the flow disturbance, pressure waves, compressibility, temperature, or changing inventory. Moving averages do not compensate inventory. The reported operating state explains the test condition; it never suppresses alarms.

The flagged stuck-meter case tests the handling of an externally supplied instrument status. This implementation does not infer a fault merely because readings are constant. The persistent bias case deliberately remains available and demonstrates that smoothing cannot remove a sustained offset.

Measured values, run metadata, and event truth remain separate. `readings.csv` contains no scenario, split, noise-profile, leak-fraction, or truth labels. The study passes only the detector's allowed measurement columns to `detect()`. Truth is read after detection for event matching.

## Stage 5 calibration and test boundary

`configs/study.json` defines twelve templates and ten replicates per template. Calibration uses only the `base_white` profile: 120 runs with unique seeds `10000–10119`. Test uses all three profiles: 360 runs with unique seeds `100000–100359`. Overlapping seed ranges are rejected before generation.

Only the ten `normal` calibration runs in the predefined `[0, 7200)` s window determine the threshold. These 7,200 measurements must be complete and valid. Other calibration scenarios and all truth columns are excluded from threshold calculation. There is no parameter search or performance-based selection in this version.

```text
r_i = density_kgm3 × (qin_m3h − qout_m3h) / 3600      [kg/s]
s = sample standard deviation of normal r_i, ddof=1  [kg/s]
threshold_kgs = mean(r_i) + max(0.01 × nominal_mass_flow_kgs, 4 × s)
threshold_fraction_nominal = threshold_kgs / nominal_mass_flow_kgs
```

Both methods use this same frozen threshold. The trailing method's six samples and three confirmations are set before calibration. Its method version includes the study configuration hash prefix. The frozen file also records the complete rule SHA256, configuration SHA256, package version, selected seeds, calibration measurement SHA256, sample counts, and residual statistics.

Test requires an existing frozen file and validates it **before** generating test measurements. A changed configuration, package version, rule hash, or inconsistent rule/statistics causes failure; recalibration is a separate explicit command. The saved threshold file is copied unchanged into the test output and embedded in its manifest. Calibration results remain calibration results, even though they include all scenario types.

| Test noise profile | Meter standard deviation | Temporal structure |
| --- | --- | --- |
| `base_white` | 0.2% of current commanded flow | Independent white noise |
| `high_white` | 0.6% of current commanded flow | Independent white noise |
| `base_ar1` | 0.2% of current commanded flow | Stationary AR(1), coefficient 0.8 |

Inlet and outlet noise channels are independent of each other. For AR(1), standardized noise obeys `z_t = 0.8 z_(t−1) + sqrt(1−0.8²) ε_t`, with a standard-normal initial state and innovations. This preserves marginal variance while adding time correlation. Noise scales with commanded flow; it is zero at stopped flow. Each run uses a separate seed and history.

## Outputs and denominators

Each phase exports the existing measurements, runs, separate truth, detector results, alarms, event evaluation, and run metrics. Additional outputs are:

| File | Grain and purpose |
| --- | --- |
| `comparison_exposure.csv` | Run × method; jointly evaluable hours, jointly evaluable no-leak hours, and false-alarm starts on that shared timeline |
| `study_summary.csv` | Split × profile × scenario × method, plus profile and split totals |
| `frozen_rule.json` | Exact frozen calibration evidence and detection settings |
| `manifest.json` | Complete study and expanded phase configurations, seeds, dependencies, rule evidence, and row counts |
| `experiment_report.md` | Generated profile-level results and interpretation boundaries |

Summary counts retain truth events, detections anytime and within 120 s, misses under both definitions, false alarms, duplicate alarms, and open alarms. Scheduled, observed, missing, valid, evaluable, and no-leak evaluable hours are summed before calculating rates. False alarms per 24 h use the actual no-leak evaluable denominator, including operating disturbances and valid bias periods. Invalid periods do not contribute eligible exposure, but they still reduce coverage and do not remove truth events from the detection denominator.

Common-time comparison separately counts false-alarm starts that fall on samples where **both** methods are evaluable. Its denominator is the corresponding shared no-leak exposure. Full-period rates remain available alongside it; an alarm starting outside the shared period is excluded from the common numerator only.

P50/P95 delays are computed directly from all matched-event delays in the selected group using linear quantile interpolation. Their sample count and missed-event counts are reported alongside them. They are not averages of run-level percentiles. No truth events, no matched delays, or zero eligible exposure yield blank CSV values rather than zero risk. Event-level `eligible_window_fraction` remains available; the summary also reports its mean.

The matching policy remains Stage 3's one-to-one rule. An alarm already active before a leak cannot detect that new leak. A later new alarm within the event can match it, even if its trigger may also reflect noise or an operating disturbance. This is operational event matching, not proof that the detector identified the physical cause.

## How to read results

Inspect scenario-level rows first. Short spikes may be rejected by persistence; sustained meter bias may still trigger both rules. A small leak can remain below threshold. Data loss can delay a later detection beyond 120 s while reducing eligible detection-window coverage. Startup alarms can precede the true leak and therefore leave it missed under the unchanged matching rule.

High-noise profiles can produce apparently timely detections of small leaks through random threshold crossings, accompanied by false alarms and duplicate episodes. Event matching alone cannot prove small-leak sensitivity. Interpret these cases with the no-leak alarm burden and scenario definition.

Profile and split totals use a deliberately chosen synthetic mix. They are descriptive results from that mix and cannot establish field reliability, confidence bounds for operating assets, or industry compliance. Synthetic dates are replay metadata, not evidence of months of operation. Power BI/DAX reconciliation and weekly/monthly presentation remain Stages 6–7.
