"""生成公开格式的本地TMDL/PBIR定义；可用性必须在Desktop核对。"""

import json
from pathlib import Path
import uuid

import pandas as pd


SCHEMA_ROOT = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/"
MEASURES = {
    "Runs tested": ("DISTINCTCOUNT(Metrics[run_id])", "0"),
    "True events": ("SUM(Metrics[true_events])", "0"),
    "Detected 120s": ("SUM(Metrics[detected_120s])", "0"),
    "Missed 120s": ("SUM(Metrics[missed_120s])", "0"),
    "Missed anytime": ("SUM(Metrics[missed_anytime])", "0"),
    "False alarms": ("SUM(Metrics[false_alarm_events])", "0"),
    "Duplicate alarms": ("SUM(Metrics[duplicate_during_leak_events])", "0"),
    "Open alarms": ("SUM(Metrics[open_alarm_events])", "0"),
    "Scheduled hours": ("SUM(Metrics[scheduled_hours])", "0.000"),
    "Valid hours": ("SUM(Metrics[valid_hours])", "0.000"),
    "Evaluable hours": ("SUM(Metrics[evaluable_hours])", "0.000"),
    "No-leak evaluable hours": ("SUM(Metrics[eligible_normal_hours])", "0.000"),
    "Missing hours": ("SUM(Metrics[missing_hours])", "0.000"),
    "Timely detection rate": ("DIVIDE([Detected 120s], [True events])", "0.0%"),
    "False alarms per 24h": ("DIVIDE([False alarms] * 24, [No-leak evaluable hours])", "0.000"),
    "Data valid fraction": ("DIVIDE([Valid hours], [Scheduled hours])", "0.0%"),
    "Evaluation coverage": ("DIVIDE([Evaluable hours], [Scheduled hours])", "0.0%"),
    "Common no-leak hours": ("SUM(Metrics[common_eligible_normal_hours])", "0.000"),
    "Common false alarms": ("SUM(Metrics[false_alarm_events_common])", "0"),
    "Common false alarms per 24h": ("DIVIDE([Common false alarms] * 24, [Common no-leak hours])", "0.000"),
    "Delay sample count": ("COUNTROWS(FILTER(Events, Events[detected_anytime] = TRUE()))", "0"),
    "Delay P50 s": ("IF([Delay sample count] > 0, PERCENTILEX.INC(FILTER(Events, Events[detected_anytime] = TRUE()), Events[detection_delay_s], 0.5))", "0.0"),
    "Delay P95 s": ("IF([Delay sample count] > 0, PERCENTILEX.INC(FILTER(Events, Events[detected_anytime] = TRUE()), Events[detection_delay_s], 0.95))", "0.0"),
    "Review selection ready": ("HASONEVALUE(Runs[run_id]) && HASONEVALUE(Methods[method_version])", None),
    "Inlet flow m3h": ("IF([Review selection ready], AVERAGE(Samples[qin_m3h]))", "0.0"),
    "Outlet flow m3h": ("IF([Review selection ready], AVERAGE(Samples[qout_m3h]))", "0.0"),
    "Raw residual kgs": ("IF([Review selection ready], AVERAGE(Samples[residual_kgs]))", "0.000"),
    "Trailing residual kgs": ("IF([Review selection ready], AVERAGE(Samples[smoothed_residual_kgs]))", "0.000"),
    "Threshold kgs": ("IF([Review selection ready], MAX(Samples[threshold_kgs]))", "0.000"),
    "Inlet pressure MPa": ("IF([Review selection ready], AVERAGE(Samples[pin_mpa]))", "0.000"),
    "Outlet pressure MPa": ("IF([Review selection ready], AVERAGE(Samples[pout_mpa]))", "0.000"),
    "Monitoring available": ("IF([Review selection ready], MAX(Samples[monitoring_available]))", "0"),
    "Selection guidance": ('IF([Review selection ready], "One run / method", "Select a run / method")', None),
    "Demo steady detection rate": ('IF(SELECTEDVALUE(Runs[split]) = "test" && SELECTEDVALUE(Runs[noise_profile]) = "base_white", CALCULATE([Timely detection rate], KEEPFILTERS(Runs[scenario] IN {"leak_2pct", "leak_5pct"})))', "0.0%"),
    "Demo target": ('IF(NOT ISBLANK([Demo steady detection rate]), 0.9)', "0.0%"),
    "Demo target status": ('IF(ISBLANK([Demo steady detection rate]), "No eligible demo scope", IF([Demo steady detection rate] >= 0.9, "Meets project demo target", "Below project demo target"))', None),
}
for _metric in ("True events", "Detected 120s", "Missed 120s", "False alarms", "Evaluation coverage", "Delay P95 s"):
    MEASURES["KPI " + _metric] = (f"IF(HASONEVALUE(Methods[method_version]), [{_metric}])", MEASURES[_metric][1])


def _identity(name: str) -> str:
    """稳定标识使同一源码重建的定义保持可比较。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "pipeline-synthetic-v1:" + name))


def column_type(name: str, series: pd.Series) -> tuple[str, str]:
    """依据完整列指定类型，禁止只看前200行推断比率或小时数。"""
    if name in {"report_date", "synthetic_test_date", "week_start", "month_start"}:
        return "dateTime", "type date"
    if pd.api.types.is_bool_dtype(series) or (len(series.dropna()) and series.dropna().map(lambda x: isinstance(x, bool)).all()):
        return "boolean", "type logical"
    if pd.api.types.is_integer_dtype(series):
        return "int64", "Int64.Type"
    if pd.api.types.is_numeric_dtype(series):
        return "double", "type number"
    return "string", "type text"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _table_definition(name: str, frame: pd.DataFrame) -> str:
    lines = [f"table {name}", f"\tlineageTag: {_identity(name)}", ""]
    if name == "ModelInfo":
        for measure, (formula, number_format) in MEASURES.items():
            lines += [f"\tmeasure '{measure}' = {formula}", f"\t\tlineageTag: {_identity('measure:'+measure)}"]
            if number_format:
                lines.append(f"\t\tformatString: {number_format}")
            lines.append("")
    conversions = []
    for column in frame:
        datatype, mtype = column_type(column, frame[column])
        lines += [f"\tcolumn {column}", f"\t\tdataType: {datatype}", f"\t\tlineageTag: {_identity(name+':'+column)}",
                  "\t\tsummarizeBy: none", f"\t\tsourceColumn: {column}"]
        if datatype == "dateTime":
            lines += ["\t\tformatString: yyyy-MM-dd", "\t\tannotation UnderlyingDateTimeDataType = Date"]
        if name == "Dates" and column == "week_label":
            lines.append("\t\tsortByColumn: week_order")
        if name == "Runs" and column == "scenario":
            lines.append("\t\tsortByColumn: scenario_order")
        lines.append("")
        conversions.append('{"' + column + '", ' + mtype + '}')
    lines += [f"\tpartition {name} = m", "\t\tmode: import", "\t\tsource =", "\t\t\tlet",
              f'\t\t\t    Source = Csv.Document(File.Contents(DataFolder & "/{name}.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),',
              '\t\t\t    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),',
              '\t\t\t    Blanks = Table.ReplaceValue(Headers, "", null, Replacer.ReplaceValue, Table.ColumnNames(Headers)),',
              '\t\t\t    Typed = Table.TransformColumnTypes(Blanks, {' + ', '.join(conversions) + '}, "en-US")',
              "\t\t\tin", "\t\t\t    Typed", "", "\tannotation PBI_ResultType = Table", ""]
    return "\n".join(lines)


def column(table: str, property_name: str, label: str | None = None) -> dict:
    return {"field": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": property_name}},
            "queryRef": f"{table}.{property_name}", "nativeQueryRef": label or property_name, "displayName": label or property_name, "active": True}


def measure(name: str) -> dict:
    return {"field": {"Measure": {"Expression": {"SourceRef": {"Entity": "ModelInfo"}}, "Property": name}},
            "queryRef": f"ModelInfo.{name}", "nativeQueryRef": name, "active": True}


def literal(value) -> dict:
    if isinstance(value, bool):
        serialized = str(value).lower()
    elif isinstance(value, (int, float)):
        serialized = str(value) + ("D" if isinstance(value, float) else "L")
    else:
        serialized = "'" + str(value).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": serialized}}}


def _selection_filter(table: str, field: str, value: str) -> dict:
    return {"Version": 2, "From": [{"Name": "s", "Entity": table, "Type": 0}],
            "Where": [{"Condition": {"In": {"Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": field}}],
                                              "Values": [[{"Literal": {"Value": "'" + value.replace("'", "''") + "'"}}]]}}}]}


def _visual(name: str, visual_type: str, title: str, x: int, y: int, width: int, height: int, roles: dict | None = None, objects: dict | None = None) -> dict:
    config = {"visualType": visual_type, "drillFilterOtherVisuals": True,
              "visualContainerObjects": {
                  "title": [{"properties": {"show": literal(True), "text": literal(title), "fontSize": literal(12), "fontColor": {"solid": {"color": literal("#17324D")}}}}],
                  "background": [{"properties": {"show": literal(True), "color": {"solid": {"color": literal("#FFFFFF")}}, "transparency": literal(0)}}],
                  "border": [{"properties": {"show": literal(True), "color": {"solid": {"color": literal("#DEE6EF")}}, "radius": literal(5)}}],
              }}
    if roles:
        config["query"] = {"queryState": {role: {"projections": projections} for role, projections in roles.items()}}
    if objects:
        config["objects"] = objects
    return {"$schema": SCHEMA_ROOT + "visualContainer/2.9.0/schema.json", "name": name,
            "position": {"x": x, "y": y, "z": 0, "width": width, "height": height, "tabOrder": y * 100 + x}, "visual": config}


def _textbox(name, text, x, y, width, height, size=14):
    result = _visual(name, "textbox", "", x, y, width, height, objects={
        "general": [{"properties": {"paragraphs": [{"textRuns": [{"value": text, "textStyle": {"fontSize": f"{size}pt", "fontFamily": "Segoe UI", "color": "#17324D"}}]}]}}]
    })
    result["visual"]["visualContainerObjects"]["title"] = [{"properties": {"show": literal(False)}}]
    result["visual"]["visualContainerObjects"]["border"] = [{"properties": {"show": literal(False)}}]
    return result


def _slicer(name, table, field, title, x, y, width, height=72, default=None):
    objects = {"data": [{"properties": {"mode": literal("Dropdown")}}],
               "header": [{"properties": {"show": literal(False)}}],
               "selection": [{"properties": {"singleSelect": literal(name in {"Method", "Run"})}}]}
    if default is not None:
        objects["general"] = [{"properties": {"filter": {"filter": _selection_filter(table, field, default)}}}]
    result = _visual(name, "slicer", title, x, y, width, height, {"Values": [column(table, field)]}, objects)
    result["visual"]["syncGroup"] = {"groupName": name, "fieldChanges": True, "filterChanges": True}
    return result


def _table(name, title, columns, x, y, width, height):
    aliases = {"Runs tested": "Runs", "True events": "Truth", "Detected 120s": "Within 120s", "Missed 120s": "Missed 120s",
               "False alarms": "False alarms", "No-leak evaluable hours": "No-leak h", "False alarms per 24h": "FA / 24h",
               "Evaluation coverage": "Coverage", "Delay sample count": "Delay n", "Delay P50 s": "P50 (s)", "Delay P95 s": "P95 (s)",
               "Scheduled hours": "Sched h", "Missing hours": "Missing h", "Data valid fraction": "Valid data",
               "Common no-leak hours": "Shared h", "Common false alarms": "FA starts",
               "Common false alarms per 24h": "FA / 24h", "Demo steady detection rate": "Steady 2% / 5% rate", "Demo target": "Demo target", "Demo target status": "Demo status"}
    columns = [dict(projection) for projection in columns]
    for projection in columns:
        if "Measure" in projection["field"]:
            metric = projection["field"]["Measure"]["Property"]
            projection["displayName"] = aliases.get(metric, metric)
    return _visual(name, "tableEx", title, x, y, width, height, {"Values": columns}, {
        "grid": [{"properties": {"textSize": literal(11)}}],
        "columnHeaders": [{"properties": {"fontSize": literal(11), "fontColor": {"solid": {"color": literal("#17324D")}}, "wordWrap": literal(True)}}],
        "values": [{"properties": {"fontSize": literal(11), "wordWrap": literal(True)}}],
        "total": [{"properties": {"totals": literal(False)}}],
    })


def _line(name, title, category, values, x, y, width, height, series=None):
    roles = {"Category": [category], "Y": [measure(value) for value in values]}
    if series:
        roles["Series"] = [series]
    result = _visual(name, "lineChart", title, x, y, width, height, roles, {
        "legend": [{"properties": {"show": literal(True), "position": literal("Top"), "fontSize": literal(10)}}],
        "categoryAxis": [{"properties": {"showAxisTitle": literal(True), "axisType": literal("Scalar"), "fontSize": literal(10)}}],
        "valueAxis": [{"properties": {"showAxisTitle": literal(True), "fontSize": literal(10)}}],
    })
    result["visual"]["query"]["sortDefinition"] = {"sort": [{"field": category["field"], "direction": "Ascending"}], "isDefaultSort": False}
    return result


def _page(name, display_name, visuals):
    return {"$schema": SCHEMA_ROOT + "page/2.1.0/schema.json", "name": name, "displayName": display_name, "displayOption": "FitToPage", "width": 1280, "height": 940,
            "objects": {"background": [{"properties": {"color": {"solid": {"color": literal("#F3F6FA")}}, "transparency": literal(0)}}]},
            "visualInteractions": [{"source": "Method", "target": visual, "type": "NoFilter"} for visual in visuals]}


def build_project(tables: dict[str, pd.DataFrame], output: Path) -> Path:
    """生成本地项目定义，不直接编造PBIX或已验证能力。"""
    semantic = output / "Pipeline.SemanticModel"
    report = output / "Pipeline.Report"
    definition = semantic / "definition"
    (definition / "tables").mkdir(parents=True, exist_ok=True)
    model_tables = {name: frame for name, frame in tables.items() if name != "PeriodSummary"}
    _write_json(semantic / "definition.pbism", {"version": "4.2", "settings": {}})
    (definition / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1606\n", encoding="utf-8")
    (definition / "model.tmdl").write_text("model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tsourceQueryCulture: en-US\n\tvalueFilterBehavior: independent\n\tdataAccessOptions\n\t\tlegacyRedirects\n\t\treturnErrorValuesAsNull\n\nannotation __PBI_TimeIntelligenceEnabled = 0\n\n" +
                                         "\n".join("ref table " + name for name in model_tables) + "\n\nref expression DataFolder\n", encoding="utf-8")
    data_folder = str((output / "data").resolve()).replace("\\", "/").replace('"', '""')
    (definition / "expressions.tmdl").write_text('expression DataFolder = "' + data_folder + '" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n', encoding="utf-8")
    relations = []
    for fact in ("Metrics", "Events", "Alarms", "Samples"):
        for dimension, key in (("Runs", "run_id"), ("Methods", "method_version")):
            relations += [f"relationship {_identity(fact+':'+dimension)}", f"\tfromColumn: {fact}.{key}", f"\ttoColumn: {dimension}.{key}", "\tcrossFilteringBehavior: oneDirection", ""]
    relations += [f"relationship {_identity('Runs:Dates')}", "\tfromColumn: Runs.report_date", "\ttoColumn: Dates.report_date", "\tcrossFilteringBehavior: oneDirection", ""]
    (definition / "relationships.tmdl").write_text("\n".join(relations), encoding="utf-8")
    for name, frame in model_tables.items():
        (definition / "tables" / f"{name}.tmdl").write_text(_table_definition(name, frame), encoding="utf-8")
    # 保留独立M/DAX参考文件，方便在应用中逐项核对。
    (output / "measures.dax").write_text("\n\n".join(name + " = " + formula for name, (formula, _) in MEASURES.items()), encoding="utf-8")
    _write_json(report / "definition.pbir", {"version": "4.0", "datasetReference": {"byPath": {"path": "../Pipeline.SemanticModel"}}})
    report_definition = report / "definition"
    _write_json(report_definition / "version.json", {"$schema": SCHEMA_ROOT + "versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    _write_json(report_definition / "report.json", {"$schema": SCHEMA_ROOT + "report/3.3.0/schema.json", "themeCollection": {},
                                                  "settings": {"exportDataMode": "AllowSummarized", "allowChangeFilterTypes": True}})
    page_order = ["Performance", "EventReview", "PeriodReview"]
    _write_json(report_definition / "pages" / "pages.json", {"$schema": SCHEMA_ROOT + "pagesMetadata/1.1.0/schema.json", "pageOrder": page_order, "activePageName": "Performance"})

    def common(title):
        info = tables["ModelInfo"].iloc[0]
        context = f"100% synthetic | assumed parameters | no field validation | V{info['package_version']} | rule {info['rule_sha256'][:12]}"
        return [_textbox("Header", title + "\n" + context, 24, 12, 1232, 66, 15),
                _slicer("Split", "Runs", "split", "Evaluation split", 24, 88, 160, default="test"),
                _slicer("Noise", "Runs", "noise_profile", "Noise profile", 198, 88, 210, default="base_white"),
                _slicer("Method", "Methods", "method", "Method", 422, 88, 150, default="trailing"),
                _slicer("Scenario", "Runs", "scenario", "Scenario", 586, 88, 350),
                _slicer("Week", "Dates", "week_label", "Synthetic study week", 950, 88, 306)]

    pages = {}
    performance = common("Pipeline detection | performance scorecard")
    card_names = ("True events", "Detected 120s", "Missed 120s", "False alarms", "Evaluation coverage", "Delay P95 s")
    for index, metric in enumerate(card_names):
        performance.append(_visual(f"KPI{index}", "card", metric, 24 + index * 208, 178, 192, 118, {"Data": [measure("KPI " + metric)]}, {"labels": [{"properties": {"fontSize": literal(28), "labelDisplayUnits": literal(1)}}], "categoryLabels": [{"properties": {"show": literal(False)}}]}))
    comparison_columns = [column("Methods", "method", "Method")] + [measure(name) for name in ("Runs tested", "True events", "Detected 120s", "Missed 120s", "False alarms", "No-leak evaluable hours", "False alarms per 24h", "Evaluation coverage", "Delay sample count", "Delay P50 s", "Delay P95 s")]
    performance += [_table("Comparison", "Both methods | counts, exposure and matched-delay sample size", comparison_columns, 24, 316, 1232, 174),
                    _table("ScenarioComparison", "Scenario evidence | misses stay in the denominator", [column("Runs", "scenario", "Scenario"), column("Methods", "method", "Method")] + [measure(name) for name in ("True events", "Detected 120s", "Missed 120s", "False alarms", "No-leak evaluable hours", "Delay P50 s")], 24, 506, 802, 264),
                    _table("Coverage", "Availability | original scheduled exposure", [column("Methods", "method", "Method")] + [measure(name) for name in ("Scheduled hours", "Missing hours", "Data valid fraction", "Evaluation coverage")], 842, 506, 414, 120),
                    _table("Shared", "Jointly evaluable no-leak time | separate numerator", [column("Methods", "method", "Method")] + [measure(name) for name in ("Common no-leak hours", "Common false alarms", "Common false alarms per 24h")], 842, 642, 414, 128),
                    _table("DemoTarget", "Project demo target | base-white steady 2% / 5% only | not an industry qualification", [column("Methods", "method", "Method"), measure("Demo steady detection rate"), measure("Demo target"), measure("Demo target status")], 24, 786, 1232, 120),
                    _textbox("Scope", "Choose one method for KPI cards. Both-method tables use original counts and exposure; delays describe matched events only. Undefined denominators remain blank.", 24, 910, 1232, 26, 9)]
    pages["Performance"] = (_page("Performance", "1 Performance Scorecard", ["Comparison", "ScenarioComparison", "Coverage", "Shared", "DemoTarget"]), performance)

    review = common("Pipeline detection | event review")
    default_run = "test:base_white:leak_2pct:001"
    review += [_slicer("Run", "Runs", "run_id", "Select one run (clear when changing incompatible filters)", 24, 176, 1012, default=default_run),
               _visual("Selection", "card", "Review scope", 1052, 176, 204, 72, {"Data": [measure("Selection guidance")]}, {"labels": [{"properties": {"fontSize": literal(11)}}], "categoryLabels": [{"properties": {"show": literal(False)}}]}),
               _line("Flow", "Measured inlet / outlet flow (m3/h) | shared axis", column("Samples", "elapsed_s", "Elapsed time (s)"), ["Inlet flow m3h", "Outlet flow m3h"], 24, 268, 606, 222),
               _line("Residual", "Residual and frozen threshold (kg/s)", column("Samples", "elapsed_s", "Elapsed time (s)"), ["Raw residual kgs", "Trailing residual kgs", "Threshold kgs"], 650, 268, 606, 222),
               _line("Pressure", "Steady reference pressures (MPa) | no transient solver", column("Samples", "elapsed_s", "Elapsed time (s)"), ["Inlet pressure MPa", "Outlet pressure MPa"], 24, 508, 606, 232),
               _table("Events", "Separate truth / matched alarm / delay", [column("Events", name) for name in ("truth_event_id", "onset_s", "end_s", "detected_120s", "detection_delay_s", "eligible_window_fraction")], 650, 508, 606, 108),
               _table("Alarms", "Alarm episodes | start / resolution / open / interruption", [column("Alarms", name) for name in ("start_s", "resolved_at_s", "status", "classification", "interrupted_s")], 650, 630, 606, 110),
               _line("Availability", "Monitoring available (1 / 0)", column("Samples", "elapsed_s", "Elapsed time (s)"), ["Monitoring available"], 24, 754, 306, 172),
               _table("Status", "State / truth | missing rows are display only", [column("Samples", name) for name in ("elapsed_s", "alarm_state", "quality_state", "invalid_reason", "operating_state", "truth_state", "display_row_origin")], 346, 754, 910, 172),
               _textbox("ReviewNote", "Select one run and one method. Missing measurements are blank; availability 0 denotes unavailable/warming, not normal. Truth is evaluation-only. Operating ramps and pressure references do not resolve inventory or pressure waves.", 24, 938, 1232, 46, 11)]
    for visual in review:
        if visual["name"] == "Availability":
            visual["visual"]["objects"]["legend"] = [{"properties": {"show": literal(False)}}]
    pages["EventReview"] = (_page("EventReview", "2 Event Review", []), review)
    pages["EventReview"][0]["height"] = 1000

    period = common("Pipeline detection | weekly and monthly review")
    period += [_table("Weekly", "Four synthetic seven-day weeks | aggregate original counts and exposure", [column("Dates", "week_label", "Study week"), column("Methods", "method", "Method")] + [measure(name) for name in ("Runs tested", "True events", "Detected 120s", "Missed 120s", "False alarms", "No-leak evaluable hours", "False alarms per 24h", "Evaluation coverage", "Delay sample count", "Delay P95 s")], 24, 178, 1232, 242),
               _table("Monthly", "Selected month | recompute from original events and hours", [column("Dates", "month_label", "Study month"), column("Methods", "method", "Method")] + [measure(name) for name in ("Runs tested", "True events", "Detected 120s", "Missed 120s", "False alarms", "No-leak evaluable hours", "False alarms per 24h", "Delay sample count", "Delay P50 s", "Delay P95 s")], 24, 438, 1232, 144),
               _table("Roadmap", "Next work | estimates are planning values", [column("Roadmap", name) for name in ("issue", "priority", "status", "owner", "estimated_hours", "validation_required", "next_action")], 24, 600, 1232, 248),
               _textbox("PeriodNote", "Reporting dates map replicates to September 1-28, 2026; original sample timestamps are preserved. Weeks are seven-day study blocks, not ISO calendar weeks or field operation. Monthly ratios and P95 are recalculated from all selected source records.", 24, 864, 1232, 58, 11)]
    pages["PeriodReview"] = (_page("PeriodReview", "3 Weekly Monthly Review", ["Weekly", "Monthly"]), period)
    for page_name, (page, visuals) in pages.items():
        folder = report_definition / "pages" / page_name
        _write_json(folder / "page.json", page)
        for visual in visuals:
            _write_json(folder / "visuals" / visual["name"] / "visual.json", visual)
    project = output / "Pipeline_V1.pbip"
    _write_json(project, {"version": "1.0", "artifacts": [{"report": {"path": "Pipeline.Report"}}], "settings": {"enableAutoRecovery": True}})
    return project
