"""Interactive dashboard filtering — the Grafana model, adapted.

Grafana gives every panel a query plus a global time range and template
variables; changing the range re-runs every query so the whole board stays in
one consistent state. Here the AI-written pandas script IS the query — for the
entire dashboard rather than per panel — so the same behaviour falls out of
re-running that one stored script against a filtered `df`.

Two properties matter and both come for free from that choice:
  - No LLM runs on a filter change. Only pandas re-executes, so the numbers
    cannot drift between refreshes and a filter costs ~1s, not a model call.
  - Panels can never disagree with each other, because they are all produced
    by a single execution rather than N independently filtered queries.

The filter is applied by REBINDING `df` itself before the script runs, never by
introducing a second variable. A script that reaches for the original frame
half-way through (AI-written code does this) would otherwise silently ignore
the filter for part of the dashboard.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# A dimension with more distinct values than this is unusable as a dropdown -
# nobody scrolls 800 store names, and it would not narrow anything meaningfully.
MAX_FILTER_OPTIONS = 40

TIME_RANGES = {
    "all": "Toàn bộ thời gian",
    "last_month": "Tháng gần nhất",
    "last_3_months": "3 tháng gần nhất",
    "last_6_months": "6 tháng gần nhất",
    "last_12_months": "12 tháng gần nhất",
    "ytd": "Từ đầu năm",
}


def _date_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]


def available_filters(df: pd.DataFrame | None) -> dict:
    """What this dataset can actually be sliced by, derived from the data
    itself rather than guessed: the date column drives the time range, and
    low-cardinality text/category columns become dropdowns."""
    if df is None or df.empty:
        return {"time_column": None, "time_ranges": [], "dimensions": []}

    date_cols = _date_columns(df)
    time_col = date_cols[0] if date_cols else None

    dims = []
    for col in df.columns:
        if col in date_cols:
            continue
        # Derived period helpers (month/quarter/year) added during the join step
        # are covered by the time range, so offering them again is noise.
        if col in ("month", "quarter", "year"):
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        try:
            values = df[col].dropna().unique()
        except Exception:  # noqa: BLE001
            continue
        if not 1 < len(values) <= MAX_FILTER_OPTIONS:
            continue
        dims.append({
            "column": col,
            "options": sorted(str(v) for v in values)[:MAX_FILTER_OPTIONS],
        })

    return {
        "time_column": time_col,
        "time_ranges": [{"key": k, "label": v} for k, v in TIME_RANGES.items()] if time_col else [],
        "dimensions": dims[:6],
    }


def _apply_time_range(df: pd.DataFrame, time_col: str, key: str) -> pd.DataFrame:
    """Ranges are relative to the LATEST DATE IN THE DATA, not to today. An
    uploaded export usually ends weeks in the past; anchoring on the wall clock
    would return an empty dashboard and look like a bug."""
    if key in (None, "", "all") or time_col not in df.columns:
        return df
    series = df[time_col]
    if series.notna().sum() == 0:
        return df
    latest = series.max()

    if key == "ytd":
        start = pd.Timestamp(year=latest.year, month=1, day=1)
    else:
        months = {"last_month": 1, "last_3_months": 3, "last_6_months": 6, "last_12_months": 12}.get(key)
        if months is None:
            return df
        start = latest - pd.DateOffset(months=months)
    return df[series >= start]


def apply_filters(df: pd.DataFrame, time_column: str | None, time_range: str | None,
                  dimensions: dict[str, list[str]] | None) -> tuple[pd.DataFrame, int]:
    """Returns (filtered_df, rows_kept). Any filter that would empty the frame
    is skipped rather than applied: an all-zero dashboard tells the user
    nothing, while the unfiltered one at least still answers the question."""
    out = df
    if time_column and time_range:
        candidate = _apply_time_range(out, time_column, time_range)
        if not candidate.empty:
            out = candidate

    for col, values in (dimensions or {}).items():
        if not values or col not in out.columns:
            continue
        candidate = out[out[col].astype("string").isin([str(v) for v in values])]
        if not candidate.empty:
            out = candidate

    return out, len(out)
