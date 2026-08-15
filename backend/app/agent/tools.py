"""The agent's tool layer - the ONLY surface the LLM can act through.

Every tool takes column names / data references, never raw cell addresses, so
the model structurally cannot mis-guess a spreadsheet range. All computation
happens in pandas inside the tools; the model only ever sees already-computed
results (each add_* returns the real numbers so insights stay grounded).

add_kpi/add_chart compute AND register in a single call - one agent step per
widget - because the earlier two-phase design (compute -> ref -> add) burned
2 steps per widget and made rich dashboards impossible within the step cap.
"""

import pandas as pd


AGG_FUNCS = {"sum": "sum", "count": "count", "avg": "mean"}
NUMERIC_ONLY_AGGS = {"sum", "avg", "growth"}


class NeedJoinConfirm(Exception):
    """Raised by apply_joins when the agent isn't confident - the router turns
    this into a need_join_confirm event for the user."""

    def __init__(self, proposal: list[dict]):
        self.proposal = proposal
        super().__init__("join confirmation needed")


def _get_layout(state: dict) -> dict:
    return state.setdefault("layout", {"kpis": [], "charts": [], "insights": []})


def _guard(df: pd.DataFrame, aggregation: str, value_column: str, group_by: str | None) -> dict | None:
    if value_column not in df.columns:
        return {"status": "missing_column", "missing": value_column}
    if aggregation in NUMERIC_ONLY_AGGS and not pd.api.types.is_numeric_dtype(df[value_column]):
        return {"status": "wrong_type", "column": value_column}
    if group_by is not None and group_by not in df.columns:
        return {"status": "missing_column", "missing": group_by}
    if aggregation == "growth" and group_by is None:
        return {"status": "error", "error": "growth cần group_by là cột thời gian (vd month)."}
    return None


def _grouped_series(df: pd.DataFrame, aggregation: str, value_column: str, group_by: str) -> list[dict]:
    if aggregation == "growth":
        # % change vs the previous period, in chronological order.
        totals = df.groupby(group_by)[value_column].sum().sort_index()
        pct = (totals.pct_change() * 100).dropna()
        return [{"label": str(idx), "value": round(float(v), 1)} for idx, v in pct.items()]

    grouped = getattr(df.groupby(group_by)[value_column], AGG_FUNCS[aggregation])()
    grouped = grouped.sort_values(ascending=False)
    if len(grouped) > 20:
        grouped = grouped.head(20)
    return [{"label": str(idx), "value": round(float(v), 2)} for idx, v in grouped.items()]


def tool_list_tables(state: dict, args: dict) -> dict:
    profiles = state.get("profiles") or []
    return {
        "tables": [
            {
                "source_id": p["source_id"],
                "columns": p["columns"],
                "dtypes": p["dtypes"],
                "row_count": p["row_count"],
                "sample_rows": p["sample_rows"][:3],
            }
            for p in profiles
        ]
    }


def tool_add_kpi(state: dict, args: dict) -> dict:
    df = state.get("cleaned_df")
    if df is None:
        return {"status": "error", "error": "Chưa có dữ liệu - hãy gọi apply_joins trước."}

    aggregation, value_column = args["aggregation"], args["value_column"]
    group_by = args.get("group_by")
    guard = _guard(df, aggregation, value_column, group_by)
    if guard:
        return guard

    if aggregation == "growth":
        series = _grouped_series(df, "growth", value_column, group_by)
        if not series:
            return {"status": "error", "error": "Không đủ kỳ dữ liệu để tính tăng trưởng."}
        value = series[-1]["value"]  # latest period vs the one before
        unit = "%"
    else:
        value = round(float(getattr(df[value_column], AGG_FUNCS[aggregation])()), 2)
        unit = ""

    _get_layout(state)["kpis"].append({
        "title": args["title"], "value": value, "unit": unit,
        "value_column": value_column, "aggregation": aggregation,
    })
    return {"status": "ok", "value": value, "unit": unit}


def tool_add_chart(state: dict, args: dict) -> dict:
    df = state.get("cleaned_df")
    if df is None:
        return {"status": "error", "error": "Chưa có dữ liệu - hãy gọi apply_joins trước."}

    chart_type = args["type"]
    aggregation, value_column, group_by = args["aggregation"], args["value_column"], args["group_by"]
    guard = _guard(df, aggregation, value_column, group_by)
    if guard:
        return guard

    data = _grouped_series(df, aggregation, value_column, group_by)
    if not data:
        return {"status": "error", "error": "Không có dữ liệu sau khi nhóm."}

    if chart_type == "bar" and len(data) > 10:
        data = data[:10]
    elif chart_type in ("line",) or aggregation == "growth":
        data = sorted(data, key=lambda d: d["label"])
    elif chart_type == "pie" and len(data) > 6:
        return {"status": "error", "error": f"Pie chart chỉ phù hợp ≤6 nhóm (đang có {len(data)}). Dùng bar."}

    _get_layout(state)["charts"].append({
        "type": chart_type, "title": args["title"], "data": data,
        "group_by": group_by, "value_column": value_column, "aggregation": aggregation,
    })
    return {"status": "ok", "preview": data[:5], "total_groups": len(data)}

