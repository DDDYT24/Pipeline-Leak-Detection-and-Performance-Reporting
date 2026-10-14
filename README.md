# Pipeline Leak Detection and Performance Reporting

An offline Python and Power BI project for testing how pipeline leak alarms behave when flow measurements are noisy, biased or missing.

All flow and pressure signals are **100% synthetic**, and the pipeline parameters are assumed. The project has no connection to an operating pipeline or control system. Its results describe the specified model and test scenarios.

Version `1.0.0` includes the detection study and a three-page Power BI report. [Download the report](https://github.com/DDDYT24/Pipeline-Leak-Detection-and-Performance-Reporting/releases/tag/v1.0.0) or rebuild it from the source.

## What the project does

If more liquid enters a pipe than leaves it, the difference can suggest a leak. Measurement noise, a biased meter or changing stored liquid can also create a difference. This project tests two simple alarm rules and shows where their decisions become unreliable.

The Python workflow calculates steady hydraulic reference values, generates instrument readings, and evaluates alarms. Power BI reads the local CSV exports to display the results.

1. Generate normal operation, leaks, operating changes and instrument faults.
2. Replay the same measurements through an immediate threshold rule and a trailing-average rule.
3. Group continuing alarms into episodes and match each known leak to at most one new alarm.
4. Report false alarms, missed leaks, detection delay and the time during which monitoring was available.
5. Review individual events and weekly/monthly test summaries in Power BI.

The detector uses only current and past measurements. It receives no leak truth or scenario labels; those are used afterwards to evaluate its decisions.

## Explore the Power BI report

The release includes `Pipeline_V1.pbix`, saved and reopened in Power BI Desktop with its data already imported. Open it to explore these pages:

| Page | What you can inspect |
| --- | --- |
| Performance Scorecard | Compare the two methods, including detections, misses, false alarms, delay and monitoring coverage |
| Event Review | Select one replay and follow flow, pressure, residual, threshold, alarm timing and measurement quality |
| Weekly Monthly Review | Compare four synthetic study weeks and their monthly total, with follow-up work beside the results |

The default selection is the test set, base white noise and trailing method. Comparison tables continue to show both methods. Event charts require one replay and one method; clear an old replay selection when changing to incompatible filters.

Study dates are a separate presentation calendar. They demonstrate reporting and do not represent weeks of field operation. Missing readings stay blank. Power BI can connect the nearest available points across a gap, so read the monitoring-availability chart alongside the flow plot.

To refresh the downloaded report on another computer, first generate the CSVs below. In Power BI, update the `DataFolder` parameter to your local `reports/v1/data` directory, then refresh. The imported data can be viewed without refreshing.

## Run it locally

Use Python 3.14 on Windows to reproduce the pinned environment, verified with Python 3.14.5. Power BI Desktop is needed only to open and save the report. No cloud account, API service or database is required.

Download or clone the repository and open PowerShell in the project folder.

### Set up Python and try the starter example

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pipeline_leak_detection --config configs/starter.json
.\.venv\Scripts\python.exe -m pytest -q
```

The starter example has three two-hour replays: normal operation, a 2% leak and a 5% leak. It produces 2,160 measurement rows. This small example helps explain the rules; it does not establish general performance.

### Run the full study and build the report

```powershell
.\.venv\Scripts\python.exe -m pipeline_leak_detection --study configs/study.json --phase calibration
.\.venv\Scripts\python.exe -m pipeline_leak_detection --study configs/study.json --phase test --frozen-rule data/generated/study/calibration/frozen_rule.json
.\.venv\Scripts\python.exe -m pipeline_leak_detection.reporting
.\.venv\Scripts\python.exe -m pipeline_leak_detection.verification
```

Calibration creates 120 runs. Only 7,200 valid measurements from ten predefined normal runs set the threshold. The threshold, averaging window and confirmation settings are saved in a frozen rule file before testing.

The test set has 360 runs: 12 scenarios, ten repeats and three noise profiles. Calibration and test use different seeds, and test results never update the rule. Each method evaluates 150 known leak events across 720 scheduled test hours.

The reporting command creates `reports/v1/Pipeline_V1.pbip`. Open it in Power BI Desktop, refresh the CSV imports, and save it as `Pipeline_V1.pbix`. Rebuilding the project replaces its generated definitions; save any Desktop layout changes separately first.

## How to read the results

| Measure | Meaning |
| --- | --- |
| Detection within 120 seconds | A new alarm matched to a known leak within the chosen time target |
| Missed event | No matching new alarm, or no match within the selected time target |
| False alarm | An unmatched alarm episode starting outside a known leak interval |
| Detection delay | Time from leak onset to the first matched alarm; missed events remain listed separately |
| Monitoring coverage | Evaluable hours divided by scheduled hours, including the effect of missing data and window warm-up |
| False alarms per 24 hours | False episodes divided by evaluable no-leak hours, multiplied by 24 |

Blank values mean there is no applicable denominator or matched event. They do not mean zero risk. An unavailable or warming interval is not normal operation, and a data gap does not automatically clear an existing alarm.

For example, these results cover the same 120 base-white test runs and 50 known leaks for each method:

| Measure | Immediate threshold | Trailing average |
| --- | ---: | ---: |
| Detected within 120 seconds | 28 | 30 |
| Missed the 120-second target | 22 | 20 |
| False alarm episodes | 44 | 20 |
| Evaluable no-leak hours | 168.556 | 166.472 |
| False alarms per 24 hours | 6.265 | 2.883 |
| Monitoring coverage | 98.565% | 97.639% |
| Matched events used for delay | 47 | 40 |
| Median matched-event delay, seconds | 0 | 40 |
| Matched-event delay P95, seconds | 1803 | 370 |

The trailing rule rejected the prescribed single-sample spikes in this profile, but it missed all ten 0.5% leaks. Persistent meter bias still caused alarms. Both methods missed the 120-second target for all ten leaks during data loss and detected them later. These failures remain in the report.

P95 summarizes the longer matched-event delays. Read it with the matched-event count and misses: a method that misses events can still have a small delay statistic for the events it does match.

## Completed stages

| Stage | What was completed |
| --- | --- |
| 1 | Steady hydraulics, SI units and reproducible measurements with separate leak truth |
| 2 | Two causal alarm rules, invalid-data handling, window warm-up and retained alarms |
| 3 | Alarm episodes, one-to-one event matching, misses, delay and exposure denominators |
| 4 | Small leaks, spikes, meter bias, prescribed operating ramps and instrument/data failures |
| 5 | Normal-only calibration, frozen settings, separate test seeds and noise stress tests |
| 6 | Power Query imports, typed tables, DAX measures and three report pages |
| 7 | Weekly/monthly test reports, follow-up work, reproduction steps and verification evidence |

## Verification and limitations

The suite has 64 automated tests covering calculations, causal decisions, event matching, missing data, study isolation and report aggregation. Complete export verification replays 346,520 measurements and 693,040 detector rows, then checks the report tables against the original study results. [Verification details](docs/V1_RELEASE.md) separate Python checks from the Desktop save/reopen and DAX checks.

The model assumes a horizontal, single-phase pipe with fixed liquid properties. A leak is a prescribed sink at the midpoint. Pressure follows steady reference calculations. Operating ramps are prescribed signal tests; the model does not solve pressure waves or changing inventory. Moving averages do not provide inventory compensation.

The study uses new noise realizations from the same generator. It provides no field qualification, industrial commissioning or standards-compliance evidence. Future work includes meter-bias diagnosis, inventory modelling and independent physical validation.

## Files and further reading

| Path | Purpose |
| --- | --- |
| `configs/starter.json` | The small demonstration and its assumed pipeline parameters |
| `configs/study.json` | Difficult scenarios, noise profiles, calibration settings and split seeds |
| `src/pipeline_leak_detection/` | Hydraulic calculations, signal generation, detection, evaluation and report generation |
| `tests/` | Automated checks for the Python workflow and report definitions |
| `data/generated/` | Local measurement, truth, alarm and study exports |
| `reports/v1/` | Generated report CSVs, Power BI project, period reports and local verification records |

Generated datasets and Power BI binaries stay outside the source repository. The Desktop report is available as a release attachment.

- [Engineering definitions and detailed checks](docs/ENGINEERING_NOTES.md): formulas, alarm states, event matching, output columns and incident background.
- [Stage 4-5 study procedure](docs/STAGE_4_5.md): signal definitions, calibration formula, noise profiles and denominators.
- [V1 report guide](docs/V1_REPORTING.md): table relationships, DAX calculations, filters and refresh instructions.
- [V1 release verification](docs/V1_RELEASE.md): exact test scope, source-data hashes and Desktop evidence.
- [Stage 1-5 release record](docs/STAGE_1_5_RELEASE.md): the earlier `0.5.0` snapshot.
