"""Deterministic chart-size caps shared by the dashboard (Code Interpreter)
and the chat Q&A path. A chart with hundreds of points is unreadable no matter
how it was produced, so both entry points fold their series through these
helpers as a final safety net — independent of whatever the model emitted.

Two data shapes are supported:
  - layout charts:  {"type", "data": [{"label", "value"}, ...]}
  - chat charts:    {"type", "labels": [...], "values": [...]}
"""

from typing import Any

MAX_LINE_POINTS = 40
MAX_BAR_POINTS = 12
MAX_PIE_POINTS = 6
MAX_SERIES_CATEGORIES = 12
MAX_SCATTER_POINTS = 300
MAX_HEATMAP_ROWS = 15
MAX_HEATMAP_COLS = 12


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _decimate(rows: list, n: int) -> list:
    """Keep n evenly-spaced REAL rows (first & last always included) — an honest
    downsample that preserves the trend shape without inventing numbers."""
    if len(rows) <= n:
        return rows
    step = (len(rows) - 1) / (n - 1)
    idxs = sorted({round(i * step) for i in range(n)})
    return [rows[i] for i in idxs]


def _cap_for(chart_type: str) -> tuple[str, int]:
    ctype = (chart_type or "bar").lower()
    if ctype in ("line", "area", "sparkline"):
        return "line", MAX_LINE_POINTS
    if ctype in ("pie", "donut"):
        return "pie", MAX_PIE_POINTS
    return "bar", MAX_BAR_POINTS


def _condense_pairs(pairs: list[tuple[str, float]], chart_type: str) -> list[tuple[str, float]]:
    """pairs = [(label, value), ...] → capped list of (label, value)."""
    ctype, cap = _cap_for(chart_type)
    if len(pairs) <= cap:
        return pairs
    if ctype == "line":
        # A trend stays chronological — decimate, don't reorder.
        return _decimate(pairs, cap)
    # bar / pie: top (cap-1) by |value| desc, remainder folded into "Khác".
    ordered = sorted(pairs, key=lambda p: abs(_num(p[1])), reverse=True)
    top = ordered[: cap - 1]
    other = round(sum(_num(v) for _, v in ordered[cap - 1:]), 2)
    return top + [("Khác", other)]


def _condense_multiseries(labels: list, series: list[dict], chart_type: str) -> tuple[list, list[dict]]:
    """Cap the shared category axis of a multi-series chart, folding the
    remainder into a single "Khác" category (summed per series) - same
    trend-preserving logic as _condense_pairs, just applied once across all
    series at once instead of per-series independently (they must stay aligned)."""
    n = len(labels)
    if n <= MAX_SERIES_CATEGORIES:
        return labels, series

    def vals_of(s: dict) -> list:
        v = s.get("values")
        return v if isinstance(v, list) else []

    if chart_type in ("multi-line", "stacked-area", "combo"):
        step = (n - 1) / (MAX_SERIES_CATEGORIES - 1)
        idxs = sorted({round(i * step) for i in range(MAX_SERIES_CATEGORIES)})
        new_labels = [labels[i] for i in idxs]
        new_series = [{"name": s.get("name"), "values": [vals_of(s)[i] if i < len(vals_of(s)) else 0 for i in idxs]} for s in series]
        return new_labels, new_series

    totals = [sum(_num(vals_of(s)[i]) if i < len(vals_of(s)) else 0 for s in series) for i in range(n)]
    order = sorted(range(n), key=lambda i: abs(totals[i]), reverse=True)
    top_idxs = order[: MAX_SERIES_CATEGORIES - 1]
    other_idxs = order[MAX_SERIES_CATEGORIES - 1 :]
    new_labels = [labels[i] for i in top_idxs] + ["Khác"]
    new_series = []
    for s in series:
        vals = vals_of(s)
        top_vals = [vals[i] if i < len(vals) else 0 for i in top_idxs]
        other_val = round(sum(_num(vals[i]) for i in other_idxs if i < len(vals)), 2)
        new_series.append({"name": s.get("name"), "values": top_vals + [other_val]})
    return new_labels, new_series


# Every way Vega-Lite can declare a view at the top level. A single-view spec
# uses `mark`; ANY chart comparing two measures uses one of the others.
#
# This list is why the "hai measure" case produced an empty popup: the check used
# to be `if "mark" not in spec: return None`, so a perfectly valid layered spec
# was declared "fundamentally invalid" and downgraded to an empty bar. The
# frontend renders through vega-embed, which handles every key below — the
# gatekeeper was rejecting charts the renderer could already draw.
_VEGA_VIEW_KEYS = ("mark", "layer", "repeat", "facet", "hconcat", "vconcat", "concat", "spec")


def _clean_vega_values(values: list) -> list:
    """Make one data array JSON-safe (NaN/Inf/NaT out, numpy scalars to native)."""
    import numpy as np
    import pandas as pd

    cleaned = []
    for v in values:
        if not isinstance(v, dict):
            cleaned.append(v)
            continue
        item = {}
        for k, val in v.items():
            if isinstance(val, pd.Timestamp):
                item[k] = str(val)
            elif isinstance(val, (np.floating, float)) and (np.isinf(val) or np.isnan(val)):
                item[k] = None
            elif isinstance(val, (np.integer, int)) and not isinstance(val, bool):
                item[k] = int(val)
            elif isinstance(val, (np.floating, float)):
                item[k] = float(val)
            elif val is pd.NaT or (not isinstance(val, (list, dict, tuple)) and pd.isna(val)):
                # pd.isna raises on containers, so it is asked last and only for
                # scalars — the old order called it first and could blow up here.
                item[k] = None
            else:
                item[k] = val
        cleaned.append(item)
    return cleaned


def _walk_vega_data(node) -> bool:
    """Clean every `data.values` array anywhere in the spec; report whether any
    data was found at all. Layered and faceted specs carry their data inside the
    sub-views, so cleaning only the top level would miss it entirely."""
    found = False
    if isinstance(node, dict):
        data = node.get("data")
        if isinstance(data, dict) and isinstance(data.get("values"), list):
            data["values"] = _clean_vega_values(data["values"])
            found = True
        elif isinstance(data, dict) and ("url" in data or "name" in data):
            found = True  # external/named source: not ours to clean, still data
        for key, child in node.items():
            if key != "data" and isinstance(child, (dict, list)):
                found = _walk_vega_data(child) or found
    elif isinstance(node, list):
        for child in node:
            found = _walk_vega_data(child) or found
    return found


def verify_and_sanitize_vega(spec: dict | None) -> dict | None:
    """Verify and sanitize a Vega-Lite specification.

    Returns a sanitized dict, or None if the spec cannot render — in which case
    the CALLER must drop the chart rather than substitute an empty one.
    """
    if not isinstance(spec, dict):
        return None

    if "$schema" not in spec:
        spec["$schema"] = "https://vega.github.io/schema/vega-lite/v5.json"

    # A spec with no view of any kind has nothing to draw.
    if not any(k in spec for k in _VEGA_VIEW_KEYS):
        return None

    # Data may sit at the top level or inside sub-views. No data anywhere means
    # an empty picture, so say so instead of shipping a blank chart.
    if not _walk_vega_data(spec):
        return None

    if "encoding" in spec and not isinstance(spec["encoding"], dict):
        del spec["encoding"]

    return spec


# A share/composition chart needs at least two parts to compare. One slice
# holding 100% is not a finding, it is an empty statement drawn as a picture.
_COMPOSITION_TYPES = {"pie", "donut", "funnel", "pyramid", "radial-bar"}

# Above this, a period-over-period delta is far more likely to be a scope
# mismatch (an all-time total compared against one month) than real growth.
# Dropping a genuine 400% jump costs one nice-to-have annotation; showing four
# nonsense arrows costs the reader's trust in every number on the board.
_MAX_PLAUSIBLE_DELTA = 3.0


def sanitize_kpis(kpis: list) -> list[str]:
    """Drop comparisons that cannot be right, and duplicate KPIs. Returns the
    notes describing what was removed (in place edit of `kpis`)."""
    notes = []
    if not isinstance(kpis, list):
        return notes

    seen_values = {}
    for k in kpis:
        if not isinstance(k, dict):
            continue
        name = k.get("name") or k.get("title") or "?"

        cur, prev = k.get("value"), k.get("compare_value")
        if isinstance(cur, (int, float)) and isinstance(prev, (int, float)) and prev:
            try:
                delta = abs(float(cur) - float(prev)) / abs(float(prev))
            except (ZeroDivisionError, ValueError, TypeError):
                delta = 0.0
            if delta > _MAX_PLAUSIBLE_DELTA:
                k.pop("compare_value", None)
                k.pop("compare_label", None)
                notes.append(f'Bỏ so sánh kỳ của KPI "{name}" (chênh {delta * 100:.0f}% — nhiều khả năng so nhầm phạm vi).')

        # Two KPIs resolving to the same number are the same KPI wearing two
        # names; keeping both just doubles the noise.
        val = k.get("value")
        if isinstance(val, (int, float)):
            key = round(float(val), 2)
            if key in seen_values:
                k["_duplicate_of"] = seen_values[key]
            else:
                seen_values[key] = name
    return notes


def drop_incomplete_period(layout: dict, label: str | None) -> int:
    """Remove the trailing data point for an unfinished period, in place.

    A month with five days of data plotted beside nine full months draws a
    cliff. The numbers are right and the picture is a lie -- and a reader
    believes the picture, not the caption underneath explaining that the last
    bar is short because the export stopped on the 5th.

    Only the LAST point is considered, and only when its label matches the
    period the date column says is unfinished. The chart title is marked so the
    series is visibly shorter rather than quietly different, since dropping data
    without saying so is its own kind of lie.

    Returns how many charts were trimmed.
    """
    if not label:
        return 0
    charts = layout.get("charts")
    if not isinstance(charts, list):
        return 0

    trimmed = 0
    for chart in charts:
        if not isinstance(chart, dict):
            continue
        rows = chart.get("data")
        # Two points in, one point out is not a trend; leave it whole and let
        # the caption carry the caveat.
        if not isinstance(rows, list) or len(rows) < 3:
            continue
        last = rows[-1]
        if not isinstance(last, dict) or str(last.get("label") or "") != label:
            continue
        chart["data"] = rows[:-1]
        title = str(chart.get("title") or "").strip()
        if title and "chưa trọn" not in title:
            chart["title"] = f"{title} (bỏ {label} — kỳ chưa trọn)"
        trimmed += 1
    return trimmed


def trim_incomplete_period(state: dict, layout: dict) -> int:
    """Apply drop_incomplete_period using whatever the session knows, in place.

    Trimming only where a layout is BUILT is not enough: a layout is stored in
    session state and read back later by the slide builder and the report
    exporter, so a dashboard built before this existed -- or by any other path --
    keeps its cliff forever. Every consumer calls this instead, and it is cheap
    and idempotent: a series already trimmed no longer ends on the unfinished
    period, so the second call finds nothing to do.
    """
    from app.data.trends import incomplete_last_period, pick_trend_columns

    # A layout that already knows which period is unfinished can be trimmed
    # without any dataframe at all -- which is the normal case for every request
    # after the build.
    marked = layout.get("incomplete_period")
    if marked:
        return drop_incomplete_period(layout, str(marked))

    df = state.get("cleaned_df")
    if df is None:
        return 0
    date_col, _, _ = pick_trend_columns(state.get("cleaned_schema") or {})
    if not date_col:
        return 0
    found = incomplete_last_period(df, date_col)
    if not found:
        return 0
    # Record the verdict on the layout itself. cleaned_df lives only for the
    # duration of the build -- it is never persisted -- so a later reader has no
    # dates to re-derive this from. Without the marker the guard silently does
    # nothing on every request after the one that built the dashboard.
    layout["incomplete_period"] = found[0]
    return drop_incomplete_period(layout, found[0])


def condense_layout(layout: dict) -> None:
    """Cap over-dense charts in a dashboard layout, in place.

    `layout` comes from executing AI-GENERATED code (Code Interpreter) — a
    buggy script can assign a raw DataFrame/Series where a list was expected.
    Never write `x.get(...) or fallback` here: if the value on the left is a
    DataFrame, `or` evaluates its truthiness and pandas raises "The truth
    value of a DataFrame is ambiguous" instead of falling back cleanly.
    isinstance() never calls __bool__, so it's the safe way to guard here.
    """
    charts = layout.get("charts")
    if not isinstance(charts, list):
        return

    # A composition chart with a single part says nothing; drop it rather than
    # render "B2C: 100%" as if it were an insight.
    kept = []
    for chart in charts:
        if isinstance(chart, dict) and (chart.get("type") or "").lower() in _COMPOSITION_TYPES:
            data = chart.get("data")
            if isinstance(data, list) and len(data) < 2:
                continue
        kept.append(chart)
    layout["charts"] = kept
    charts = kept

    # Vega specs that cannot render are REMOVED from the dashboard, for the same
    # reason the chat path drops them: an empty tile occupies a slot, invites a
    # click, and shows nothing. Done as a filter because a chart cannot delete
    # itself from the list it is being iterated over.
    survivors = []
    for chart in charts:
        if isinstance(chart, dict) and chart.get("type") == "vega":
            sanitized = verify_and_sanitize_vega(chart.get("vegaLiteSpec"))
            if sanitized is None:
                continue
            chart["vegaLiteSpec"] = sanitized
        survivors.append(chart)
    if len(survivors) != len(charts):
        layout["charts"] = survivors
        charts = survivors

    for chart in charts:
        if not isinstance(chart, dict) or chart.get("type") == "vega":
            continue

        series = chart.get("series")
        if isinstance(series, list) and series:
            labels = chart.get("labels") if isinstance(chart.get("labels"), list) else []
            new_labels, new_series = _condense_multiseries(labels, series, chart.get("type"))
            chart["labels"] = new_labels
            chart["series"] = new_series
            continue

        points = chart.get("points")
        if isinstance(points, list) and points:
            if len(points) > MAX_SCATTER_POINTS:
                chart["points"] = _decimate(points, MAX_SCATTER_POINTS)
            continue

        matrix = chart.get("matrix")
        if isinstance(matrix, list) and matrix:
            rows = matrix[:MAX_HEATMAP_ROWS]
            chart["matrix"] = [row[:MAX_HEATMAP_COLS] if isinstance(row, list) else row for row in rows]
            if isinstance(chart.get("labels"), list):
                chart["labels"] = chart["labels"][:MAX_HEATMAP_COLS]
            if isinstance(chart.get("rowLabels"), list):
                chart["rowLabels"] = chart["rowLabels"][:MAX_HEATMAP_ROWS]
            continue

        data = chart.get("data")
        if not isinstance(data, list) or not data:
            continue
        pairs = [(d.get("label"), d.get("value")) for d in data]
        capped = _condense_pairs(pairs, chart.get("type"))
        chart["data"] = [{"label": lbl, "value": val} for lbl, val in capped]


def condense_chat_chart(chart: dict | None) -> dict | None:
    """Cap a chat-answer chart (parallel labels/values arrays), in place.

    Also verifies and sanitizes Vega spec if type is vega.
    """
    if not chart:
        return chart
    if chart.get("type") == "vega":
        sanitized = verify_and_sanitize_vega(chart.get("vegaLiteSpec"))
        if sanitized is None:
            # Drop the chart, do NOT substitute an empty bar.
            #
            # The old fallback set type="bar" with empty labels/values. That
            # object is still truthy, so the frontend showed a "📈 Xem biểu đồ"
            # button and opened a popup — where MiniChart, which checks the DATA
            # rather than the object, rendered nothing. The user got an empty box
            # with a "Ghi vào Dashboard" button in it. Returning None makes the
            # button disappear, which is the honest outcome: there is no chart.
            return None
        chart["vegaLiteSpec"] = sanitized
        return chart
        
    labels = chart.get("labels")
    values = chart.get("values")
    if not isinstance(labels, list) or not isinstance(values, list) or not labels:
        return chart

    # Coerce here, at the one door a model-authored chart comes through.
    # Gemini's schema takes a single type per field, so `values` is declared as
    # string; every consumer downstream (MiniChart, the docx/pptx/xlsx chart
    # renderer, dashboard KPIs) expects numbers. Converting at the boundary
    # keeps that schema constraint from leaking into all of them.
    values = [_num(v) if not isinstance(v, (int, float)) or isinstance(v, bool) else v
              for v in values]

    pairs = list(zip(labels, values))
    capped = _condense_pairs(pairs, chart.get("type"))
    chart["labels"] = [lbl for lbl, _ in capped]
    chart["values"] = [val for _, val in capped]
    return chart


# ── Chart shape ────────────────────────────────────────────────────────────
# Two shapes for the same thing exist in this codebase: the auto-dashboard
# emits {title, type, data: [{label, value}]}, while every renderer -- MiniChart
# in the browser, chart_renderer for .docx/.pptx/.xlsx -- reads
# {labels, values}. The browser has always bridged them in layoutChartToSpec.
# The backend had no bridge at all, so a chart full of data reached the slide
# planner and the report exporter looking empty, and both produced blank frames
# without complaining. One definition, here, used by everything server-side.

# What each chart type needs before MiniChart will draw it. Mirrored from the
# renderer on purpose: offering the model a chart the browser then refuses is
# how a slide ends up as an empty frame with a caption underneath explaining a
# trend nobody can see.
_CHART_REQUIREMENTS = {
    "scatter": "points",
    "bubble": "points",
    "heatmap": "matrix",
    "stacked-bar": "series",
    "grouped-bar": "series",
    "multi-line": "series",
    "stacked-area": "series",
    "combo": "series",
    "radar": "series",
    "vega": "vegaLiteSpec",
}


def normalize_chart(chart: Any) -> dict:
    """Put a dashboard chart into the shape the renderer reads.

    The auto-dashboard emits {title, type, data: [{label, value}]}; MiniChart
    reads {labels, values}. The browser converts between them in
    layoutChartToSpec, but the deck is planned server-side and never passed
    through it -- so every chart arrived looking empty, the prompt listed them
    with no data, and the slides framed blank boxes under confident headings.
    Mirrors layoutChartToSpec deliberately; the two must agree.
    """
    if not isinstance(chart, dict):
        return {}

    out = dict(chart)
    if chart.get("type") == "vega" and chart.get("vegaLiteSpec"):
        out.setdefault("labels", [])
        out.setdefault("values", [])
        return out

    rows = chart.get("data") or []
    if not chart.get("labels") and rows:
        out["labels"] = [r.get("label") for r in rows if isinstance(r, dict)]
    if not chart.get("values") and rows:
        out["values"] = [r.get("value") for r in rows if isinstance(r, dict)]
    out.setdefault("type", "bar")
    return out


def is_renderable(chart: Any) -> bool:
    """True when this chart carries the data its own type requires."""
    if not isinstance(chart, dict):
        return False
    required = _CHART_REQUIREMENTS.get(str(chart.get("type") or "").strip())
    if required:
        payload = chart.get(required)
        return bool(payload)
    # Everything else -- bar, line, pie, donut, gauge, bullet, progress -- draws
    # from `values`.
    return bool(chart.get("values"))
