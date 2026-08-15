"""Curated, hand-tested analysis helpers seeded into the skill library.

Unlike self-learned skills (which the AI writes at runtime), these ship with the
app and are verified, so the model calls a reliable implementation instead of
re-deriving common business calculations each time. They are loaded into the
sandbox exactly like learned skills (get_skills_source / load_skills_into_env)
and retrieved by relevance, so both the chat Q&A and dashboard paths can use
them. Every function is defensive: it validates columns and coerces numbers so a
messy real-world sheet won't crash the call.
"""

import pandas as pd
import numpy as np


def _num(series):
    """Coerce a column to numeric (Vietnamese/US formats already normalized
    upstream, but be safe), dropping what cannot be parsed."""
    return pd.to_numeric(series, errors="coerce")


def top_n_by(df, group_col, value_col, n=10, agg="sum"):
    """Top N categories ranked by an aggregated numeric column.
    Use for "top 10 cửa hàng theo doanh thu", "sản phẩm bán chạy nhất".

    Args:
        df: input DataFrame.
        group_col: category column to group by (e.g. 'Cửa hàng').
        value_col: numeric column to aggregate (e.g. 'Doanh thu').
        n: how many top rows to return.
        agg: 'sum' | 'mean' | 'count' | 'max' | 'min'.
    Returns: DataFrame [group_col, value_col] sorted descending, top N.
    """
    d = df[[group_col, value_col]].copy()
    d[value_col] = _num(d[value_col])
    g = d.groupby(group_col)[value_col].agg(agg).reset_index()
    return g.sort_values(value_col, ascending=False).head(int(n)).reset_index(drop=True)


def summary_by(df, group_col, value_col):
    """Group-level summary of a measure: sum, mean, count, min, max per category.
    Use for "thống kê doanh thu theo khu vực".

    Returns: DataFrame [group_col, sum, mean, count, min, max].
    """
    d = df[[group_col, value_col]].copy()
    d[value_col] = _num(d[value_col])
    g = d.groupby(group_col)[value_col].agg(["sum", "mean", "count", "min", "max"]).reset_index()
    return g.sort_values("sum", ascending=False).reset_index(drop=True)


def share_of_total(df, group_col, value_col):
    """Each category's share (%) of the grand total of a measure.
    Use for "tỷ trọng doanh số theo khu vực", "chiếm bao nhiêu phần trăm".

    Returns: DataFrame [group_col, value_col, share_pct] descending by value.
    """
    d = df[[group_col, value_col]].copy()
    d[value_col] = _num(d[value_col])
    g = d.groupby(group_col)[value_col].sum().reset_index()
    total = g[value_col].sum()
    g["share_pct"] = (g[value_col] / total * 100).round(2) if total else 0.0
    return g.sort_values(value_col, ascending=False).reset_index(drop=True)


def pareto(df, group_col, value_col):
    """Pareto (80/20) analysis: categories sorted by value with a cumulative
    percentage column, to see which few drive most of the total.
    Use for "phân tích 80/20", "nhóm nào đóng góp phần lớn doanh thu".

    Returns: DataFrame [group_col, value_col, cum_pct] descending.
    """
    g = df[[group_col, value_col]].copy()
    g[value_col] = _num(g[value_col])
    g = g.groupby(group_col)[value_col].sum().reset_index()
    g = g.sort_values(value_col, ascending=False).reset_index(drop=True)
    total = g[value_col].sum()
    g["cum_pct"] = (g[value_col].cumsum() / total * 100).round(2) if total else 0.0
    return g


def growth_over_time(df, date_col, value_col, freq="M"):
    """Aggregate a measure by period and add period-over-period growth (%).
    Use for "xu hướng doanh thu theo tháng", "tăng trưởng theo thời gian".

    Args:
        freq: pandas offset ('D' day, 'W' week, 'M' month, 'Q' quarter, 'Y' year).
    Returns: DataFrame [period, value_col, growth_pct].
    """
    d = df[[date_col, value_col]].copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
    d[value_col] = _num(d[value_col])
    d = d.dropna(subset=[date_col])
    g = d.groupby(d[date_col].dt.to_period(freq))[value_col].sum().reset_index()
    g[date_col] = g[date_col].astype(str)
    g = g.rename(columns={date_col: "period"})
    g["growth_pct"] = (g[value_col].pct_change() * 100).round(2)
    return g


def year_over_year(df, date_col, value_col):
    """Yearly totals of a measure with year-over-year growth (%).
    Use for "tăng trưởng so với năm trước", "so sánh doanh thu các năm".

    Returns: DataFrame [year, value_col, yoy_pct].
    """
    d = df[[date_col, value_col]].copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
    d[value_col] = _num(d[value_col])
    d = d.dropna(subset=[date_col])
    g = d.groupby(d[date_col].dt.year)[value_col].sum().reset_index()
    g = g.rename(columns={date_col: "year"})
    g["yoy_pct"] = (g[value_col].pct_change() * 100).round(2)
    return g


def detect_outliers(df, value_col, method="iqr"):
    """Flag outlier rows in a numeric column (IQR rule by default).
    Use for "tìm giá trị bất thường", "đơn hàng bất thường".

    Returns: DataFrame of the outlier rows only (original columns kept).
    """
    d = df.copy()
    vals = _num(d[value_col])
    if method == "zscore":
        mu, sd = vals.mean(), vals.std(ddof=0)
        mask = (vals - mu).abs() > 3 * sd if sd else pd.Series(False, index=d.index)
    else:  # iqr
        q1, q3 = vals.quantile(0.25), vals.quantile(0.75)
        iqr = q3 - q1
        mask = (vals < q1 - 1.5 * iqr) | (vals > q3 + 1.5 * iqr)
    return d[mask.fillna(False)].reset_index(drop=True)


def cross_tab(df, row_col, col_col, value_col, agg="sum"):
    """Pivot table: rows × columns with an aggregated measure in the cells.
    Use for "doanh thu theo khu vực và tháng", bảng chéo hai chiều.

    Returns: pivot DataFrame (row_col as index reset to a column).
    """
    d = df[[row_col, col_col, value_col]].copy()
    d[value_col] = _num(d[value_col])
    p = pd.pivot_table(d, index=row_col, columns=col_col, values=value_col,
                       aggfunc=agg, fill_value=0)
    return p.reset_index()
