"""Deterministic trend/forecast/outlier/top-mover analysis — Phase 2 of the
analyst-copilot roadmap. Pure pandas/statsmodels, NO LLM call: these are FACTS
computed straight from the real dataframe, fed into the insight-writing LLM
(sub_agents.generate_insights) as grounded context — turning insights from
purely descriptive ("doanh thu là X") into predictive ("xu hướng tăng, dự
báo kỳ tới ~Y, vùng Z biến động mạnh nhất"). Every function degrades to
None/[] on insufficient/malformed data instead of raising — a dashboard build
must never fail because a forecast couldn't converge.
"""

import pandas as pd


def _period_series(df: pd.DataFrame, date_col: str, value_col: str) -> tuple[pd.Series | None, float]:
    """SUM value_col grouped into a sensible period (monthly for a long span,
    weekly for a medium one, daily for a short one), chronologically sorted.

    Also returns the last period's date coverage (0..1): how far through its
    period the newest raw date falls. A month whose data stops on the 5th has
    coverage ~0.17 — comparing it against full months produces the classic
    fake "doanh thu sụp đổ -84%" artifact, so callers must know."""
    s = df[[date_col, value_col]].dropna()
    if s.empty:
        return None, 1.0
    dt = pd.to_datetime(s[date_col], errors="coerce")
    s = s.assign(_dt=dt).dropna(subset=["_dt"])
    if s.empty:
        return None, 1.0

    span_days = (s["_dt"].max() - s["_dt"].min()).days
    freq = "M" if span_days > 120 else ("W" if span_days > 21 else "D")

    grouped = s.groupby(s["_dt"].dt.to_period(freq))[value_col].sum().sort_index()
    grouped.index = grouped.index.to_timestamp()

    last = s["_dt"].max()
    if freq == "M":
        coverage = last.day / last.days_in_month
    elif freq == "W":
        coverage = (last.weekday() + 1) / 7
    else:
        coverage = 1.0  # daily periods are inherently complete
    return grouped, coverage


def analyze_trend(
    df: pd.DataFrame, date_col: str, value_col: str,
    group_col: str | None = None, forecast_periods: int = 3,
) -> dict | None:
    """Returns grounded facts, or None if there isn't enough data to say
    anything meaningful (< 3 periods). Never raises.

    {
      "period_label": "tháng"|"tuần"|"ngày",
      "series": [{"period": "2025-05-01", "value": 123.0}, ...]   (last 24),
      "trend": "up"|"down"|"flat",
      "growth_pct": float | None,          # latest period vs previous
      "forecast": [{"period": "...", "value": ...}, ...],         # Holt-Winters, best-effort
      "outliers": [{"period": "...", "value": ..., "z": ...}, ...],
      "top_movers": [{"group": "...", "change_pct": ...}, ...],   # only if group_col given
    }
    """
    try:
        series, coverage = _period_series(df, date_col, value_col)
        if series is None or len(series) < 3:
            return None

        span_days = (series.index.max() - series.index.min()).days
        period_label = "tháng" if span_days > 120 else ("tuần" if span_days > 21 else "ngày")

        values = series.values.astype(float)

        # Pro-rate the last period if it's incomplete (coverage < 1) to avoid
        # the classic "revenue collapsed" artifact from comparing 5 days of a
        # new month against a full previous month. The pro-rated value feeds
        # EVERY downstream stat (growth, trend, outliers, forecast) — leaving
        # the raw partial value in any of them re-creates the same artifact
        # there (Holt-Winters trained on a fake crash forecasts a fake crash).
        adj_values = values.copy()
        if 0.1 < coverage < 0.9 and len(values) >= 2:
            adj_values[-1] = values[-1] / coverage

        # Guard against small denominators (e.g. previous value is very close to 0)
        # by checking if it's at least 1% of the average value and >= 1.0.
        growth_pct = None
        if len(adj_values) >= 2:
            prev_val = adj_values[-2]
            mean_val = adj_values.mean()
            val_threshold = max(1.0, mean_val * 0.01)
            if abs(prev_val) >= val_threshold:
                growth_pct = round(float((adj_values[-1] - prev_val) / abs(prev_val) * 100), 1)

        # Robust trend call: mean of second half vs first half (insensitive to
        # single-point noise, unlike comparing just the last two periods).
        half = len(adj_values) // 2
        first_mean, second_mean = adj_values[:half].mean(), adj_values[half:].mean()
        if first_mean == 0:
            trend = "up" if second_mean > 0 else "flat"
        else:
            change = (second_mean - first_mean) / abs(first_mean)
            trend = "up" if change > 0.05 else ("down" if change < -0.05 else "flat")

        outliers = []
        if len(adj_values) >= 4:
            mean, std = adj_values.mean(), adj_values.std()
            if std > 0:
                for ts, v in zip(series.index, adj_values):
                    z = (v - mean) / std
                    if abs(z) >= 2:
                        outliers.append({"period": str(ts.date()), "value": round(float(v), 2), "z": round(float(z), 2)})

        forecast = _forecast(series, adj_values, period_label, forecast_periods)
        top_movers = _top_movers(df, date_col, value_col, group_col)

        return {
            "period_label": period_label,
            "series": [{"period": str(ts.date()), "value": round(float(v), 2)} for ts, v in zip(series.index, values)][-24:],
            "trend": trend,
            "growth_pct": growth_pct,
            "last_period_coverage": round(float(coverage), 2),
            "forecast": forecast,
            "outliers": outliers[:5],
            "top_movers": top_movers,
        }
    except Exception:  # noqa: BLE001 - a failed analysis must not fail the dashboard
        return None


def _forecast(series: pd.Series, values, period_label: str, periods: int) -> list[dict]:
    """Holt-Winters exponential smoothing — best-effort, empty list on any
    convergence failure or insufficient history."""
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing

        if len(values) < 4:
            return []
        seasonal_periods = (
            12 if (period_label == "tháng" and len(values) >= 24)
            else 7 if (period_label == "ngày" and len(values) >= 14)
            else None
        )
        model = ExponentialSmoothing(
            values, trend="add",
            seasonal="add" if seasonal_periods else None,
            seasonal_periods=seasonal_periods,
        ).fit(optimized=True)
        preds = model.forecast(periods)

        step = series.index[-1] - series.index[-2] if len(series.index) >= 2 else pd.Timedelta(days=30)
        out = []
        for i, p in enumerate(preds, start=1):
            out.append({"period": str((series.index[-1] + step * i).date()), "value": round(float(p), 2)})
        return out
    except Exception:  # noqa: BLE001
        return []


def _top_movers(df: pd.DataFrame, date_col: str, value_col: str, group_col: str | None) -> list[dict]:
    """Which groups grew/shrank the most, comparing the first half of the
    timespan to the second half. Empty if no group column is available."""
    if not group_col or group_col not in df.columns:
        return []
    try:
        g = df[[date_col, group_col, value_col]].dropna()
        dt = pd.to_datetime(g[date_col], errors="coerce")
        g = g.assign(_dt=dt).dropna(subset=["_dt"])
        if g.empty:
            return []
        mid = g["_dt"].min() + (g["_dt"].max() - g["_dt"].min()) / 2
        before = g[g["_dt"] < mid].groupby(group_col)[value_col].sum()
        after = g[g["_dt"] >= mid].groupby(group_col)[value_col].sum()
        combined = pd.concat([before, after], axis=1, keys=["before", "after"]).fillna(0)

        # Guard against small baseline values in group analysis (e.g. < 1% of mean before, or < 1.0)
        mean_before = before.mean() if not before.empty else 0
        min_threshold = max(1.0, mean_before * 0.01)

        # A group whose first-half total is under 10% of its second-half total
        # effectively didn't operate in the first half (e.g. a branch opened
        # mid-period). Its growth-% is arithmetic noise (the "+14258.8%" class
        # of artifact), so report it as NEW instead of ranking it by %.
        new_groups = []

        def _calc_change(r):
            b = r["before"]
            # NEW-group check must run first: a branch that opened mid-period
            # has before ≈ 0, which the small-denominator guard below would
            # silently swallow — but "new branch" is exactly the fact worth
            # reporting (just not as a bogus growth-%).
            if r["after"] > 0 and abs(b) < 0.1 * r["after"]:
                new_groups.append({"group": str(r.name), "new": True, "after": round(float(r["after"]), 2)})
                return None
            if abs(b) < min_threshold:
                return None
            return round(float((r["after"] - b) / abs(b) * 100), 1)

        combined["change_pct"] = combined.apply(_calc_change, axis=1)
        combined = combined.dropna(subset=["change_pct"]).sort_values("change_pct", ascending=False)
        movers = [
            {"group": str(name), "change_pct": float(row["change_pct"])}
            for name, row in combined.head(3).iterrows()
        ]
        return movers + new_groups[:2]
    except Exception:  # noqa: BLE001
        return []


def period_coverage_note(df: pd.DataFrame, date_col: str | None) -> str:
    """Warning text about an INCOMPLETE final period, computed before any code
    is generated.

    analyze_trend() already pro-rates a partial last period, but that runs
    AFTER the model has written the dashboard script — so the prose came out
    corrected while the KPI deltas the script computed kept the raw artifact,
    and one dashboard showed "+12.6%" next to "▼83.9%" for the same measure.
    The fix has to reach the model BEFORE it writes any comparison.
    Returns "" when the last period is complete or there is no date column."""
    if not date_col or date_col not in df.columns:
        return ""
    try:
        dt = pd.to_datetime(df[date_col], errors="coerce").dropna()
        if dt.empty:
            return ""
        last = dt.max()
        span_days = (last - dt.min()).days
        if span_days > 120:
            coverage = last.day / last.days_in_month
            unit, label = "tháng", last.strftime("%Y-%m")
        elif span_days > 21:
            coverage = (last.weekday() + 1) / 7
            unit, label = "tuần", last.strftime("%Y-W%W")
        else:
            return ""
        if coverage >= 0.9:
            return ""
        return (
            f"\n⚠️ CẢNH BÁO KỲ CUỐI CHƯA TRỌN VẸN: dữ liệu kết thúc ngày {last.date()}, "
            f"tức {unit} cuối cùng ({label}) mới có khoảng {coverage * 100:.0f}% số ngày.\n"
            f"KHI TÍNH `compare_value` HOẶC BẤT KỲ SO SÁNH KỲ NÀO: TUYỆT ĐỐI không so {unit} cuối này "
            f"với một {unit} đầy đủ — sẽ ra mức giảm giả tạo tới -80% dù thực tế không hề giảm.\n"
            f"Hãy làm MỘT trong hai cách: (a) bỏ qua {unit} chưa trọn vẹn, lấy {unit} hoàn chỉnh gần nhất "
            f"làm giá trị hiện tại và {unit} liền trước làm `compare_value`; hoặc (b) so cùng số ngày đầu "
            f"{unit} (ví dụ 5 ngày đầu {unit} này với 5 ngày đầu {unit} trước).\n"
        )
    except Exception:  # noqa: BLE001 - a warning that fails must not block the build
        return ""


def pick_trend_columns(schema_info: dict) -> tuple[str | None, str | None, str | None]:
    """Best-effort auto-pick (date_col, value_col, group_col) from a
    cleaned_schema's role-tagged column_profiles. (None, None, None) if there
    is no date+measure pair to analyze."""
    profiles = schema_info.get("column_profiles", [])
    date_col = next((c["name"] for c in profiles if c.get("role") == "date"), None)
    if not date_col:
        return None, None, None

    measures = [c for c in profiles if c.get("role") == "measure"]
    if not measures:
        return None, None, None
    # Prefer the measure with the largest sum magnitude — usually the revenue-
    # like column an analyst cares about, not a small side count.
    value_col = max(measures, key=lambda c: abs(c.get("sum") or 0))["name"]
    group_col = next((c["name"] for c in profiles if c.get("role") == "category"), None)
    return date_col, value_col, group_col


def format_trend_for_prompt(signals: dict | None) -> str:
    """Compact text block for injection into the insight/report prompts. The
    LLM only ever sees these already-computed facts, never the raw dataframe,
    so it structurally cannot invent a forecast/outlier number."""
    if not signals:
        return ""
    lines = [f"Phân tích xu hướng theo {signals['period_label']} (tính trực tiếp từ dữ liệu bằng pandas/statsmodels, KHÔNG phải AI ước lượng):"]
    lines.append(f"- Xu hướng tổng thể: {signals['trend']}")
    coverage = signals.get("last_period_coverage")
    if coverage is not None and coverage < 0.9:
        lines.append(
            f"- LƯU Ý: kỳ cuối cùng mới có ~{coverage * 100:.0f}% số ngày dữ liệu (kỳ chưa kết thúc). "
            "Các số tăng trưởng/dự báo dưới đây ĐÃ quy đổi ước tính cho cả kỳ; giá trị thô của kỳ cuối "
            "trong chuỗi hiển thị thấp hơn thực tế và KHÔNG được diễn giải là sụt giảm."
        )
    if signals.get("growth_pct") is not None:
        lines.append(f"- Tăng trưởng kỳ gần nhất so với kỳ trước: {signals['growth_pct']}%")
    if signals.get("forecast"):
        fc = ", ".join(f"{f['period']}≈{f['value']}" for f in signals["forecast"])
        lines.append(f"- Dự báo (Holt-Winters) các kỳ tới: {fc}")
    if signals.get("outliers"):
        ol = ", ".join(f"{o['period']}={o['value']} (z={o['z']})" for o in signals["outliers"])
        lines.append(f"- Điểm bất thường: {ol}")
    movers = [m for m in signals.get("top_movers", []) if not m.get("new")]
    new_groups = [m for m in signals.get("top_movers", []) if m.get("new")]
    if movers:
        tm = ", ".join(f"{m['group']} {m['change_pct']:+.1f}%" for m in movers)
        lines.append(f"- Biến động mạnh nhất theo nhóm (nửa sau so với nửa đầu giai đoạn): {tm}")
    if new_groups:
        ng = ", ".join(f"{m['group']} (doanh số nửa sau ≈ {m['after']})" for m in new_groups)
        lines.append(
            f"- Nhóm MỚI hoạt động từ giữa kỳ (không đủ dữ liệu kỳ đầu để so sánh %, KHÔNG được nói là \"tăng trưởng đột biến\"): {ng}"
        )
    return "\n".join(lines)
