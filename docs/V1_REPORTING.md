# V1 report and verification contract

The report presents **100% synthetic signals with assumed parameters**. Calibration and test use the same generator with disjoint seeds; these results are not field or independent-physics validation. Python computes engineering values and evaluation records. Power BI imports local CSVs and aggregates them for presentation.

## Rebuild the local V1

Use the project-local Windows environment:

```powershell
.\.venv\Scripts\python.exe -m pipeline_leak_detection --config configs/starter.json
.\.venv\Scripts\python.exe -m pipeline_leak_detection --study configs/study.json --phase calibration
.\.venv\Scripts\python.exe -m pipeline_leak_detection --study configs/study.json --phase test --frozen-rule data/generated/study/calibration/frozen_rule.json
.\.venv\Scripts\python.exe -m pipeline_leak_detection.reporting
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pipeline_leak_detection.verification
```

The reporting command writes `reports/v1/data/`, period reports, a provenance manifest, DAX references, and `reports/v1/Pipeline_V1.pbip`. Open that project in Power BI Desktop, refresh its local CSV imports, and save the verified report as `reports/v1/Pipeline_V1.pbix`. Rebuilding authoring definitions replaces generated definitions; save any intentional Desktop design changes separately first.

Text project files use the documented [PBIP/TMDL authoring workflow](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-overview) and [PBIR report format](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-report). PBIP-to-PBIX conversion is performed in Desktop. Generated definitions alone do not establish a working report.

## Local CSV interfaces

| Table | Grain and use |
| --- | --- |
| `Runs` | One physical replay; split, scenario, seed, noise profile, original synthetic date and separate presentation date |
| `Methods` | One frozen method version, with an instant/trailing display name |
| `Dates` | One presentation date; four seven-day study blocks in September 2026 |
| `Metrics` | Run × method; original event counts and exposure, joined one-to-one with shared-time exposure |
| `Events` | Truth event × method; one-to-one matching, misses, delay and eligible detection window |
| `Alarms` | One alarm episode; start, resolution/open status, interruption and matching classification |
| `Samples` | Scheduled sample × method; observed fields plus explicitly marked missing-interval display rows |
| `PeriodSummary` | Period × split × noise × scenario × method, with profile and split totals |
| `Scorecard` | Reference values and a narrowly scoped project demo target; no field qualification |
| `Roadmap` | Future work and planning estimates; not verified capabilities |
| `ModelInfo` | Package, rule hash, synthetic disclosure and reporting-calendar definition |

`Dates → Runs → Metrics/Events/Alarms/Samples` and `Methods → each fact` are one-to-many, single-direction relationships. Truth is not multiplied into measurement rows to count events. The review sample table adds truth context only after detection; it is never supplied to the detector or performance evaluator.

Power Query uses a `DataFolder` parameter and UTF-8 CSV decoding. Column types are generated from complete columns, with hours and rates explicitly numeric, truth outcomes logical, dates typed, and empty numeric values retained as null. The procedure avoids the fractional-hour and rate errors that can result from sampling only the first 200 rows for type inference.

## DAX definitions and filter behavior

The generated `measures.dax` is the exact measure reference. Counts and hours use sums of original facts. Detection rate is timely detections divided by all truth events; false alarms per 24 h are false episodes times 24 divided by eligible no-leak hours; coverage is evaluable hours divided by scheduled hours. Shared-time false alarms use their own numerator and shared no-leak denominator.

P50/P95 use `PERCENTILEX.INC` directly on matched event delays; matched-event sample counts and misses are displayed alongside them. A zero denominator or no matched events yields blank, rather than zero risk. Numeric rates are never averaged from run, week or month percentages.

KPI cards require one selected method. Comparison tables ignore the method slicer and show both methods under the other filters. The event charts require one selected run and one method, preventing accidental averaging across replays. Run selection applies to the event page; split, noise, method, scenario and study-week slicers synchronize across the three pages. Clear an incompatible run selection before changing to another split/noise/week.

## Three pages

1. **Performance Scorecard:** timely detection, misses, false alarms, availability, delay sample size, both-method comparisons and shared-time exposure. The 90% project demo target applies only to base-white test runs with steady 2%/5% leaks; other scopes remain review-only.
2. **Event Review:** shared-axis inlet/outlet flow, residual and frozen threshold in kg/s, steady reference pressure in MPa, explicit monitoring availability, separate truth, and alarm episode boundaries. Missing-display rows contain blank sensor values and are excluded from performance denominators.
3. **Weekly Monthly Review:** aggregate original events and exposure by presentation period, recompute matched-event quantiles, and retain a future-work roadmap with planning estimates.

Desktop's line renderer can connect the nearest available sensor points across a missing interval. Such a connecting segment is not an observed or imputed measurement. Read the monitoring-availability chart and event eligible-window coverage alongside it; the CSV sensor fields remain null and performance evaluation excludes unavailable time. The elapsed-time axes are continuous numeric axes covering the full replay.

## Presentation calendar and period reports

Original sample timestamps and `synthetic_test_date` are preserved. A separate `report_date` maps replicate and noise-profile indices to September 1–28, 2026. Study weeks are September 1–7, 8–14, 15–21 and 22–28; they are not ISO weeks. This mapping demonstrates reporting and does not imply four weeks of field operation.

`weekly_test_report.md` and `monthly_test_report.md` default to test data, show each noise profile and method, retain detection and false-alarm denominators, list three event records for review, and document next actions. Scenario-level period rows remain in `PeriodSummary.csv`.

## Verification boundaries

The automated verification command replays every exported measurement for starter, calibration and test; recomputes alarm episodes, matching, misses, exposure and summaries; regenerates exact normal calibration evidence; and reconciles every report CSV with original study inputs. It records CSV hashes in `verification/python_full_replay.json`.

Power BI application checks separately verify refresh, displayed values, exported DAX results, slicers, event charts, period totals and save/reopen of the exact final PBIX. File integrity or JSON schemas alone do not replace those checks. The local source, generated study, reports and verification evidence remain separate; no GitHub publication is required to use V1.

The local V1.0.0 verification on October 11, 2026 passed 64 tests and complete replay of 346,520 physical measurements and 693,040 detector rows. Desktop 2.158.1304.0 saved, closed and reopened the final PBIX. Native base-white test exports (eight week/method rows and two month/method rows) match Python counts exactly and other measures within displayed rounding. Reopened page screenshots and `verification/powerbi_ui_verification.json` record that application scope separately from the all-profile Python replay. The historical `reports/starter.pbix` is retained separately and is not the final V1 report.
