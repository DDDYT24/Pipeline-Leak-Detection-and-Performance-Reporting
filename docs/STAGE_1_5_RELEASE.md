# Stage 1-5 release: completed work and verification

This release publishes the offline Python engineering and evaluation workflow through Stage 5 as version `0.5.0`. All parameters are assumed and all generated signals are synthetic. Independent test seeds do not constitute field or independent-physics validation. Power BI report implementation and final report delivery are outside this release.

## What each stage contributes

| Stage | Problem addressed | Implemented work | Main output |
| --- | --- | --- | --- |
| 1 | Reproducible measurements with explicit physical assumptions | SI conversions, velocity, Reynolds number, Darcy friction factor, steady pressure loss and a prescribed midpoint sink | `readings.csv`, `runs.csv`, separate `truth.csv` and provenance manifest |
| 2 | Turning imperfect measurements into causal decisions | Current-sample threshold, six-sample trailing average, three confirmations, invalid/warming states and retained alarms through gaps | `detector_results.csv` |
| 3 | Evaluating events rather than counting alarm samples | Alarm episode boundaries, open/interrupted alarms, one-to-one truth matching, late/missed events, false alarms and exposure hours | `alarms.csv`, `event_evaluation.csv`, `run_metrics.csv` |
| 4 | Exposing conditions that simple demonstrations omit | Twelve templates: small/steady leaks, spikes, bias, prescribed operating ramps, startup leaks, missing/suspect instruments, dropped samples and leak-plus-data-loss | Reproducible successes, failures and availability evidence |
| 5 | Comparing fixed rules on unused noise realizations | Predefined normal-only calibration, hash-bound frozen rule, disjoint calibration/test seeds, three noise profiles, common-time comparison and pooled delay quantiles | `frozen_rule.json`, `comparison_exposure.csv`, `study_summary.csv`, `experiment_report.md` |

The detector receives only current/past measurement fields. Ground truth, scenario labels and noise-profile labels stay outside its inputs. Moving averages are not inventory compensation; prescribed operating ramps and steady pressure references are not a transient hydraulic solver.

## Reproduce

Run from the repository root in the project-local environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pipeline_leak_detection --config configs/starter.json
.\.venv\Scripts\python.exe -m pipeline_leak_detection --study configs/study.json --phase calibration
.\.venv\Scripts\python.exe -m pipeline_leak_detection --study configs/study.json --phase test --frozen-rule data/generated/study/calibration/frozen_rule.json
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/verify_study_exports.py
```

Generation writes separate starter, calibration and test folders under `data/generated/`. The verifier reads every exported measurement again, recomputes detector states, alarm episodes, truth matching, misses, exposure and summaries, and regenerates the exact normal calibration evidence. It writes `data/generated/study/verification.json` with row counts and CSV hashes. Generated data, local environments and report binaries are excluded from version control.

## Verification scope

The Stage 1-5 test suite covers 55 automated cases: hydraulics and conservation, seeded measurements, causal detector histories, invalid data, retained/open alarms, one-to-one event matching, zero denominators, difficult-scenario boundaries, calibration isolation, frozen-rule drift rejection, independent seeds, correlated noise, common-time exposure, direct delay quantiles and complete command-line exports.

| Export set | Runs | Measurement rows | Detector rows | Truth events | Truth-by-method rows |
| --- | ---: | ---: | ---: | ---: | ---: |
| Starter | 3 | 2160 | 4320 | 2 | 4 |
| Calibration | 120 | 86090 | 172180 | 50 | 100 |
| Test | 360 | 258270 | 516540 | 150 | 300 |

The complete replay covers 346520 measurement rows and 693040 detector rows. Calibration uses only 7200 valid measurements from ten predefined normal runs to set the threshold. Each method evaluates 150 test truth events across 720 scheduled test hours. Calibration and test seed ranges are disjoint.

### Actual execution on October 11, 2026

- Clean project-local environment: package `0.5.0`, Python 3.14.5, pinned runtime/test dependencies; dependency check passed.
- All **55 tests passed in 62.20 seconds**. The command-line integration case also verifies full export replay and rejection of a deliberately altered detector result.
- Starter, 120 calibration runs and 360 test runs were regenerated from this source snapshot. All **346520 measurements and 693040 detector rows** passed complete replay, event evaluation, common-time exposure and summary reconciliation.
- Exact regenerated normal-only calibration matched the entire frozen record, including its measurement hash. Study SHA256: `d44aae28401966cb94480dc1729792f6c8c9424344cea7c92937a5d5cf6d2392`; frozen rule SHA256: `39b5c085db08ad397e338190ce03356d15b7368e1e3f4e8ed8366bb4230c2764`.
- The frozen threshold is approximately `2.124419 kg/s` (`8.997540 m3/h`), fraction `0.011246925297133434` of nominal mass flow. The six-sample window and three confirmations were fixed before calibration.
- [Machine-readable verification](STAGE_1_5_VERIFICATION.json) records exact row counts and exported CSV hashes. No generated dataset or report binary is included in the release.


## Engineering findings and limitations

- In the prescribed base-white spike scenario, the instant rule produces 11 false episodes across ten runs; the trailing rule produces none. Both use the same frozen threshold, and their evaluable no-leak exposure remains visible.
- Persistent valid meter bias still produces 16/10 false episodes for instant/trailing. The moving average cannot remove a sustained offset.
- The trailing rule misses all ten base-white 0.5% leak events. A chance threshold crossing during a truth window does not establish reliable small-leak sensitivity.
- For base-white leaks during data loss, both methods miss all ten 120-second targets and detect later, with median delays of 300/370 seconds. Missing evidence is retained in coverage and event denominators.
- Rates are calculated from summed original counts and hours. Delay quantiles use matched events directly, with sample counts and misses beside them. Undefined denominators stay blank.
- The results belong to the prescribed synthetic scenario mix. No operating deployment, industrial commissioning or standards-compliance claim is made.

See [the detailed Stage 4-5 procedure](STAGE_4_5.md) for signal definitions, calibration formulas, output grains and interpretation.
