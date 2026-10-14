# V1.0.0 release and verification

Version `1.0.0` completes the offline workflow through Stage 7. All data are synthetic and use assumed parameters. The source includes the detector study, report generation and complete CSV verification. The release attachment `Pipeline_V1.pbix` contains the report saved in Power BI Desktop with its data imported.

## Stage 6: Power BI reporting

- Import local CSVs with explicit number, date, text and logical types. Blank numeric values stay null.
- Use nine one-direction relationships between the run, method and date dimensions and the measurement/event facts.
- Calculate rates from original counts and hours. Compute median/P95 delay from matched events directly, with misses and sample counts beside them.
- Provide performance comparison, a single-run event review, and weekly/monthly review pages. Event charts use continuous elapsed-time axes and show monitoring availability.
- Keep measurement inputs separate from truth throughout detection; add truth context only for later evaluation and review.

## Stage 7: Test reports and reproducibility

- Generate weekly and monthly reports from original event records and exposure hours.
- Preserve original sample timestamps and add a separate presentation calendar with four seven-day study weeks.
- Retain difficult cases, unavailable time and follow-up work. Planning estimates describe proposed work, not completed capabilities.
- Provide reproduction commands, recorded hashes and application checks so source calculations and displayed results can be compared.

## Automated checks on October 13, 2026

The project-local Python 3.14.5 environment passed its dependency check and all **64 tests in 92.87 seconds**. Coverage includes hydraulics, measurement causality, alarm episodes, one-to-one matching, seed separation, frozen-rule checks, missing data, reporting types, relationships, direct period aggregation and export reconciliation. The command-line integration test rejects altered detector exports.

The starter exports were regenerated. Complete verification replayed all **346520 physical measurement rows and 693040 detector rows** across starter, calibration and test, then reconciled every report CSV. Exact normal-only calibration regeneration also passed.

| Data set | Runs | Measurement rows | Detector rows | Known leak events |
| --- | ---: | ---: | ---: | ---: |
| Starter | 3 | 2160 | 4320 | 2 |
| Calibration | 120 | 86090 | 172180 | 50 |
| Test | 360 | 258270 | 516540 | 150 |

The report contains 480 runs, 960 run/method metric rows, 400 event/method rows, 8699 alarm episodes, 691200 scheduled sample/method display rows and 540 period summaries. Added missing-interval rows are for display only; their sensor values remain blank and they never enter detector or evaluation inputs.

The V1 frozen rule SHA256 is `3d937a0cb0ec47ab385bfdf5691befd1b445bbeec642352c78db943c2809f57a`. The earlier Stage 1-5 package has its own version-bound rule record; the test settings and numerical results are unchanged.

[Machine-readable verification](V1_VERIFICATION.json) records row counts and CSV hashes. Run the complete checks with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pipeline_leak_detection.verification
```

## Recorded Power BI application checks

On October 11, 2026, Power BI Desktop `2.158.1304.0` refreshed the local CSVs, saved the final PBIX, closed it and reopened the same file. All three pages displayed the synthetic disclosure, version `1.0.0` and rule hash prefix `3d937a0cb0ec`.

- Default scorecard: test/base-white/trailing, 50 known leaks, 30 detections within 120 seconds, 20 target misses, 20 false alarms, 97.6% coverage and delay P95 of 370 seconds.
- Both-method comparison: 120 runs each; instant/trailing false alarms 44/20 and no-leak evaluable hours 168.556/166.472.
- The 2% event replay shows truth from 1200 to 6000 seconds, trailing alarm start at 1250 seconds, resolution at 6020 seconds and a 50-second matched delay.
- The data-loss replay shows a 370-second delay, a missed 120-second target and a visible monitoring interruption.
- Native exports contain eight weekly rows and two monthly rows for the base-white test scope. Integer counts match Python exactly; other values agree within displayed rounding. Python full replay covers all profiles and records.

The published PBIX has **47633453 bytes** and SHA256 `14ded749b5912a279a5eb763b702125a8db85091eef83b1b93057eba7a9c64fa`. Its current checksum matches those recorded Desktop checks. The October 13 checks repeat the Python verification and artifact identity checks; they do not claim a new Desktop session on that date.

To check a downloaded report in PowerShell:

```powershell
Get-FileHash .\Pipeline_V1.pbix -Algorithm SHA256
```

The report has imported data and opens without CSV refresh. To refresh after rebuilding locally, change `DataFolder` to your `reports/v1/data` directory. Detailed filter and refresh instructions are in [the report guide](V1_REPORTING.md).

## Interpretation limits

The hydraulic model is steady and simplified. Operating changes are prescribed signal tests, and the moving average does not compensate changing pipeline inventory. No field performance, industrial commissioning or standards compliance is established.

Power BI may connect available sensor points across null gaps. Those segments are not measurements or imputed values; read monitoring availability alongside them. Report dates demonstrate study aggregation, not field monitoring history.
