"""Observed facts about a dataset, computed once at upload and shared everywhere.

data/semantics.py answers "what IS this data" (grain, unit, fact vs dimension).
This module answers "what does it SAY" — which groups dominate, which periods
are unusual, where the distribution is lopsided, what is missing. Those are the
observations a human analyst makes in the first two minutes, and they are what
decide which charts are worth building at all: knowing three stores hold 60% of
revenue is what turns "make a dashboard" into "make a concentration chart".

Deliberately deterministic pandas, no LLM:
  - it is free and instant, so it can run on every upload;
  - it cannot hallucinate, so its output is safe to feed straight into prompts
    as ground truth;
  - the model then SPENDS its reasoning on what to do about the facts, instead
    of on rediscovering them.

Everything is best-effort: any failure yields fewer facts, never an error.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Cheap guards so a 250k-row upload stays fast: all work is vectorised
# aggregation, and wide/high-cardinality columns are skipped rather than
# crunched into facts nobody can act on.
MAX_CATEGORY_DISTINCT = 200
TOP_N = 3
# z=2 is a statistical convention (~5% of a normal distribution), not a guess
# about business data - it is the one threshold here with a real justification.
OUTLIER_Z = 2.0


def _measures(df: pd.DataFrame) -> list[str]:
    out = []
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]):
            continue
        if c in ("year", "month", "quarter"):
            continue
        s = df[c].dropna()
        # Skip only columns with NO variation. Requiring 3+ distinct values
        # silently dropped legitimate measures - a quantity column that is
        # always 1 or 2, or an amount with two price points - and with the
        # primary measure gone, no facts were produced for the table at all.
        if len(s) < 3 or s.nunique() < 2:
            continue
        out.append(c)
    return out


def _categories(df: pd.DataFrame) -> list[str]:
    out = []
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_datetime64_any_dtype(df[c]):
            continue
        try:
            n = df[c].nunique(dropna=True)
        except Exception:  # noqa: BLE001
            continue
        if 1 < n <= MAX_CATEGORY_DISTINCT:
            out.append(c)
    return out


def _concentration_facts(df: pd.DataFrame, cats: list[str], measure: str) -> list[str]:
    """How the measure is spread across each grouping.

    Reports the MEASUREMENT and nothing else. Earlier versions classified the
    result ("phân bố rất lệch" above 60%, "khá đều" below 50%) using cut-offs
    invented from one sample file — but whether 60% concentration is alarming
    or normal depends entirely on the domain, which the backend cannot know and
    the LLM can. Stating the share and the group count leaves that judgement
    where it belongs."""
    facts = []
    for col in cats[:4]:
        try:
            grouped = df.groupby(col, dropna=True)[measure].sum().sort_values(ascending=False)
        except Exception:  # noqa: BLE001
            continue
        total = float(grouped.sum())
        if total <= 0 or grouped.empty:
            continue
        top = grouped.head(TOP_N)
        share = float(top.sum()) / total
        biggest_share = float(grouped.iloc[0]) / total
        names = ", ".join(f'"{i}"' for i in top.index[:TOP_N])
        facts.append(
            f'"{col}": {len(grouped)} nhóm; nhóm lớn nhất "{grouped.index[0]}" chiếm '
            f'{biggest_share * 100:.1f}% tổng {measure}, top {min(TOP_N, len(grouped))} '
            f'({names}) chiếm {share * 100:.1f}%.'
        )
    return facts


def _time_facts(df: pd.DataFrame, date_col: str, measure: str | None) -> list[str]:
    facts = []
    try:
        dt = pd.to_datetime(df[date_col], errors="coerce").dropna()
        if dt.empty:
            return facts
        facts.append(f"Khoảng thời gian: {dt.min().date()} → {dt.max().date()} ({dt.dt.date.nunique():,} ngày có dữ liệu).")

        if measure is None:
            return facts
        s = df[[date_col, measure]].dropna()
        s = s.assign(_p=pd.to_datetime(s[date_col], errors="coerce").dt.to_period("M"))
        series = s.groupby("_p")[measure].sum().sort_index()
        if len(series) < 3:
            return facts

        # Gaps: months inside the range with no rows at all. A chart drawn over
        # a gap implies continuity that isn't there.
        full = pd.period_range(series.index.min(), series.index.max(), freq="M")
        missing = [str(p) for p in full if p not in series.index]
        if missing:
            facts.append(f"THIẾU DỮ LIỆU ở các tháng: {', '.join(missing[:6])}"
                         + (" ..." if len(missing) > 6 else ""))

        values = series.values.astype(float)
        mean, std = values.mean(), values.std()
        if std > 0:
            spikes = [(str(p), v) for p, v in zip(series.index, values) if abs((v - mean) / std) >= OUTLIER_Z]
            if spikes:
                shown = ", ".join(f"{p} ({'cao' if v > mean else 'thấp'} bất thường)" for p, v in spikes[:4])
                facts.append(f"Tháng bất thường về {measure}: {shown}.")
    except Exception:  # noqa: BLE001
        pass
    return facts


def _quality_facts(df: pd.DataFrame, measures: list[str]) -> list[str]:
    """Composition of each measure: how much of it is negative, zero, and how
    skewed it is.

    No interpretation attached. Negative values might be refunds, corrections,
    losses or a sign convention; zeros might be free items, cancellations or
    padding. Which one it is depends on the business, so the numbers are stated
    and the meaning is left to the layer that can actually know it."""
    facts = []
    for col in measures[:4]:
        s = df[col].dropna()
        if s.empty:
            continue
        neg = float((s < 0).mean())
        zero = float((s == 0).mean())
        parts = []
        if neg > 0:
            parts.append(f"{neg * 100:.1f}% giá trị âm")
        if zero > 0:
            parts.append(f"{zero * 100:.1f}% bằng 0")

        # Mean-vs-median gap is the readable form of skew: it says directly
        # whether the average is being pulled by a long tail, which decides
        # whether reporting a mean is honest.
        try:
            mean, median = float(s.mean()), float(s.median())
            if median and abs(mean - median) / max(abs(median), 1e-9) > 0.2:
                parts.append(f"trung bình {mean:,.0f} vs trung vị {median:,.0f} (phân bố đuôi dài)")
        except Exception:  # noqa: BLE001
            pass

        if parts:
            facts.append(f'Cột "{col}": ' + ", ".join(parts) + ".")
    return facts


def profile_facts(df: pd.DataFrame | None, semantic: dict | None = None) -> list[str]:
    """The observed facts for one table, as prompt-ready Vietnamese lines."""
    if df is None or df.empty:
        return []
    facts: list[str] = []
    try:
        measures = _measures(df)
        cats = _categories(df)
        date_cols = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]

        # Prefer the measure the semantic pass called primary; else the one with
        # the largest magnitude, which is almost always the money column.
        primary = (semantic or {}).get("primary_measure")
        if primary not in measures:
            primary = max(measures, key=lambda c: abs(float(df[c].sum() or 0)), default=None)

        if date_cols:
            facts += _time_facts(df, date_cols[0], primary)
        if primary and cats:
            facts += _concentration_facts(df, cats, primary)
        facts += _quality_facts(df, measures)
    except Exception as exc:  # noqa: BLE001 - upload must never fail over this
        logger.warning(f"[eda] skipped: {exc}")
    return facts[:12]


def format_facts_for_prompt(facts_by_sheet: dict[str, list[str]] | None) -> str:
    """One block, injected next to the semantic profile so every prompt reasons
    from the same observations."""
    if not facts_by_sheet:
        return ""
    lines = ["QUAN SÁT THỰC TẾ TỪ DỮ LIỆU (tính bằng pandas, KHÔNG phải AI ước lượng — dùng để quyết định nên phân tích gì):"]
    for sid, facts in facts_by_sheet.items():
        if not facts:
            continue
        lines.append(f'- Bảng "{sid}":')
        for f in facts:
            lines.append(f"    • {f}")
    if len(lines) == 1:
        return ""
    lines.append(
        "Hãy DÙNG các quan sát này để chọn góc phân tích: nhóm nào áp đảo thì đáng có biểu đồ tập trung/xếp hạng; "
        "kỳ bất thường thì đáng làm nổi bật; tháng thiếu dữ liệu thì đừng vẽ như thể liên tục."
    )
    return "\n".join(lines)
