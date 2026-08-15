"""Relationships BETWEEN columns and BETWEEN tables.

Everything else in the data layer looks at one column at a time. That misses
the two facts that most change how a dataset should be analysed:

  formulas — "Thành tiền = Số lượng × Đơn giá". Knowing this means unit price
             must never be summed, revenue is derived rather than a source, and
             a drop can be split into "price effect" vs "volume effect". No
             per-column profile can reveal it.
  keys     — which column in the fact table points at which dimension. Joins
             are currently re-guessed by an LLM on every request; containment
             is an exact set operation, so it should be established once and
             trusted thereafter.

Both are fully deterministic. A human analyst guesses these and rarely checks;
here they are verified against every row, which is the whole point.

Cost control: candidates are filtered on a sample first, and only survivors are
re-tested on the full frame — so a 268k-row file costs a handful of vectorised
passes rather than a combinatorial explosion.
"""

from __future__ import annotations

import itertools
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MAX_NUMERIC_COLS = 12       # combinations grow cubically; beyond this, skip
SAMPLE_ROWS = 500
REL_TOL = 0.01              # 1% — survives float rounding and VND cent drift
MIN_MATCH_RATIO = 0.98      # a formula must hold on ~all rows, not most
MIN_CONTAINMENT = 0.95      # foreign keys tolerate a few orphan codes
MAX_KEY_CANDIDATES = 8


# ── formulas between columns ────────────────────────────────────────────────

_OPS = {
    "×": lambda a, b: a * b,
    "+": lambda a, b: a + b,
    "−": lambda a, b: a - b,
}


def _match_ratio(target: np.ndarray, computed: np.ndarray) -> float:
    """Share of rows where computed ≈ target, ignoring rows where either side
    is missing. Relative tolerance, with an absolute floor so near-zero values
    do not fail on floating-point dust."""
    valid = np.isfinite(target) & np.isfinite(computed)
    if valid.sum() == 0:
        return 0.0
    t, c = target[valid], computed[valid]
    tol = np.maximum(np.abs(t) * REL_TOL, 1e-6)
    return float((np.abs(t - c) <= tol).mean())


def detect_formulas(df: pd.DataFrame) -> list[dict]:
    """[{target, left, op, right, ratio}] for arithmetic relationships that hold
    across essentially the whole column."""
    if df is None or len(df) < 20:
        return []

    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    numeric = [c for c in numeric if c not in ("year", "month", "quarter")]
    if len(numeric) < 3:
        return []
    if len(numeric) > MAX_NUMERIC_COLS:
        # Keep the largest-magnitude columns: money and quantity, not flags.
        numeric = sorted(numeric, key=lambda c: abs(float(df[c].sum() or 0)), reverse=True)[:MAX_NUMERIC_COLS]

    sample = df[numeric].head(SAMPLE_ROWS).to_numpy(dtype="float64", na_value=np.nan)
    idx = {c: i for i, c in enumerate(numeric)}

    candidates = []
    for target in numeric:
        t = sample[:, idx[target]]
        if not np.isfinite(t).any():
            continue
        for left, right in itertools.combinations([c for c in numeric if c != target], 2):
            for sym, fn in _OPS.items():
                # Subtraction is not commutative: both orders are distinct.
                orders = [(left, right)] if sym != "−" else [(left, right), (right, left)]
                for a, b in orders:
                    if _match_ratio(t, fn(sample[:, idx[a]], sample[:, idx[b]])) >= MIN_MATCH_RATIO:
                        candidates.append({"target": target, "left": a, "op": sym, "right": b})

    if not candidates:
        return []

    # Confirm on the full frame — a relationship that holds on the first 500
    # rows but breaks later is worse than no relationship at all, because
    # everything downstream would treat it as certain.
    full = df[numeric].to_numpy(dtype="float64", na_value=np.nan)
    confirmed = []
    seen_targets = set()
    for c in candidates:
        if c["target"] in seen_targets:
            continue
        ratio = _match_ratio(
            full[:, idx[c["target"]]],
            _OPS[c["op"]](full[:, idx[c["left"]]], full[:, idx[c["right"]]]),
        )
        if ratio >= MIN_MATCH_RATIO:
            confirmed.append({**c, "ratio": round(ratio, 4)})
            seen_targets.add(c["target"])
    return confirmed


# ── keys between tables ─────────────────────────────────────────────────────

def detect_keys(dataframes: dict) -> list[dict]:
    """[{child, child_col, parent, parent_col, containment, rows_per_key}] —
    foreign-key links found by set containment.

    A parent column must be UNIQUE (a real primary key) and must contain
    essentially every value the child references. Both conditions are exact set
    operations, so a confirmed link is a fact, not a suggestion."""
    if not dataframes or len(dataframes) < 2:
        return []

    # Pre-compute the value sets once; comparing every column pair directly
    # would re-hash the same columns for every partner sheet.
    profile: dict[tuple[str, str], dict] = {}
    for sid, df in dataframes.items():
        if df is None or df.empty:
            continue
        for col in df.columns:
            s = df[col].dropna()
            if s.empty or pd.api.types.is_datetime64_any_dtype(s):
                continue
            try:
                # unique() first: casting the DISTINCT values to string costs a
                # fraction of casting every row, and a 200k-row column with 500
                # codes is the normal shape here, not the exception.
                uniques = s.unique()
                if not 1 < len(uniques) <= 100_000:
                    continue
                values = set(pd.Series(uniques).astype("string"))
            except Exception:  # noqa: BLE001
                continue
            profile[(sid, col)] = {
                "values": values,
                "unique": len(values) == len(s),
                "rows": len(df),
            }

    # Only unique columns can ever be a parent, and a workbook is mostly
    # non-unique columns. Filtering once turns the inner loop from "every
    # column" into "every primary-key candidate".
    parents = [(k, v) for k, v in profile.items() if v["unique"]]
    if not parents:
        return []

    links = []
    for (child_sid, child_col), child in profile.items():
        n_child = len(child["values"])
        if not n_child:
            continue
        # A parent smaller than MIN_CONTAINMENT × the child cannot possibly
        # contain enough of it — |child ∩ parent| ≤ |parent|. Exact, not a
        # heuristic, and it skips the set intersection entirely.
        min_parent_size = MIN_CONTAINMENT * n_child
        for (parent_sid, parent_col), parent in parents:
            if child_sid == parent_sid or parent is child:
                continue
            if len(parent["values"]) < min_parent_size:
                continue
            hit = len(child["values"] & parent["values"]) / n_child
            if hit < MIN_CONTAINMENT:
                continue
            links.append({
                "child": child_sid, "child_col": child_col,
                "parent": parent_sid, "parent_col": parent_col,
                "containment": round(hit, 4),
                "rows_per_key": round(child["rows"] / max(len(parent["values"]), 1), 1),
            })

    # Strongest links first, capped: a report listing forty weak links is noise.
    links.sort(key=lambda l: (l["containment"], l["rows_per_key"]), reverse=True)
    return links[:MAX_KEY_CANDIDATES]


# ── prompt rendering ────────────────────────────────────────────────────────

def format_relations_for_prompt(formulas_by_sheet: dict, keys: list[dict] | None) -> str:
    """The block every downstream prompt receives. Written as instructions, not
    trivia — a formula is only useful if the model knows what it forbids."""
    lines: list[str] = []

    if formulas_by_sheet:
        lines.append("QUAN HỆ GIỮA CÁC CỘT (kiểm chứng trên toàn bộ dữ liệu, không phải phỏng đoán):")
        for sid, formulas in formulas_by_sheet.items():
            for f in formulas:
                lines.append(
                    f'- Bảng "{sid}": `{f["target"]}` = `{f["left"]}` {f["op"]} `{f["right"]}` '
                    f'(đúng {f["ratio"] * 100:.1f}% số dòng).'
                )
        lines.append(
            "  → HỆ QUẢ BẮT BUỘC: cột dẫn xuất chỉ được cộng ở vế kết quả. TUYỆT ĐỐI không cộng/"
            "trung bình cột đơn giá hay tỷ lệ. Khi chỉ số kết quả thay đổi, hãy tách xem do thừa số "
            "nào (ví dụ doanh thu giảm do SỐ LƯỢNG hay do ĐƠN GIÁ) — đây là phân tích có giá trị nhất."
        )

    if keys:
        lines.append("")
        lines.append("QUAN HỆ GIỮA CÁC BẢNG (kiểm bằng phép bao hàm tập hợp):")
        for k in keys:
            lines.append(
                f'- `{k["child"]}`.`{k["child_col"]}` → `{k["parent"]}`.`{k["parent_col"]}` '
                f'(khớp {k["containment"] * 100:.1f}%, trung bình {k["rows_per_key"]} dòng/khoá).'
            )
        lines.append("  → Dùng đúng các cặp cột này khi cần ghép bảng; đừng tự đoán cặp khác.")

    return "\n".join(lines)
