"""Structural guards on a join — the two ways a merge silently corrupts totals.

A workbook rarely holds one clean fact table. It holds `Đơn hàng` next to
`Tổng hợp theo tháng` next to `Tổng hợp theo vùng`, all built by hand with
formulas already applied. Join any two of those and the numbers stop meaning
what they say — without an error, without a warning, without a single row
looking wrong.

Two distinct failures, both purely structural (no domain knowledge, no column
names, no vocabulary — they hold for a sales workbook, a payroll workbook, or a
sensor log equally):

  FAN-OUT — the right key is not unique, so every left row matching it is
  duplicated. This is the severe one: it inflates the LEFT table's own measures,
  the ones the user trusts most, and the row count change is the only visible
  symptom.

  BROADCAST — the right key IS unique but coarser than the left grain (one row
  per month joined onto thousands of transactions). No row multiplies, so
  nothing looks wrong at all; but the right table's numbers are now repeated
  across every left row, and SUM over them returns a multiple of the truth.

Broadcast is why a prompt sentence was never enough here. The model is told the
grain, and still has no reason to suspect a column that reads perfectly
normally. So the columns are computed and removed from the measure list the
dashboard is allowed to aggregate — the sum simply becomes unavailable rather
than wrong.
"""

from __future__ import annotations

import pandas as pd

# A key that repeats on the right side multiplies rows. Below this ratio the
# duplication is small enough to be a handful of stray records rather than a
# structural many-to-many, and saying so would be noise.
FANOUT_MIN_RATIO = 1.02
# Same idea on the other side: a coarser right key only matters once left rows
# genuinely stack up per key.
BROADCAST_MIN_ROWS_PER_KEY = 1.05


def _numeric_columns(df: pd.DataFrame, exclude: str) -> list[str]:
    """Columns a downstream SUM could plausibly land on."""
    return [
        c for c in df.columns
        if c != exclude
        and pd.api.types.is_numeric_dtype(df[c])
        and not pd.api.types.is_bool_dtype(df[c])
        and not pd.api.types.is_datetime64_any_dtype(df[c])
    ]


def measure_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    left_col: str,
    right_col: str,
) -> dict | None:
    """The raw structural numbers of one join. No thresholds, no wording.

    Split out from the verdict because the chat path performs its merges inside
    the sandbox CONTAINER, which has pandas but none of this application's code.
    Measuring is a handful of plain pandas calls that can be repeated there;
    deciding what the numbers MEAN stays here, in one place, so the two paths
    can never drift into disagreeing about the same join.

    Returns None when there is nothing to judge (missing column, empty keys).
    """
    try:
        lkey, rkey = left[left_col], right[right_col]
        l_valid, r_valid = int(lkey.notna().sum()), int(rkey.notna().sum())
        if not l_valid or not r_valid:
            return None

        r_distinct = int(rkey.nunique(dropna=True))
        l_distinct = int(lkey.nunique(dropna=True))
        if not r_distinct or not l_distinct:
            return None

        # Measured on the keys that actually match, so an unrelated lookup table
        # with few rows does not look like an aggregate.
        matched = lkey[lkey.isin(set(rkey.dropna().unique()))]
        return {
            "right_col": right_col,
            "r_valid": r_valid,
            "r_distinct": r_distinct,
            "matched_valid": int(matched.notna().sum()),
            "matched_distinct": int(matched.nunique(dropna=True)),
            "right_measures": _numeric_columns(right, right_col),
        }
    except Exception:  # noqa: BLE001 - a guard must never break the merge
        return None


def judge_join(m: dict | None, right_name: str, right_grain: str | None = None) -> dict:
    """Turn measurements into warnings. The only place the thresholds and the
    Vietnamese wording live."""
    out: dict = {"warnings": [], "non_additive": []}
    if not m:
        return out
    try:
        right_col = m["right_col"]

        # ── FAN-OUT: right key repeats -> left rows duplicate ──────────
        r_rows_per_key = m["r_valid"] / m["r_distinct"]
        if r_rows_per_key >= FANOUT_MIN_RATIO:
            out["warnings"].append(
                f'Ghép với "{right_name}" theo cột "{right_col}": cột này BỊ LẶP ở bảng '
                f"phải (trung bình {r_rows_per_key:.1f} dòng cho mỗi giá trị), nên mỗi dòng "
                f"bên trái sẽ bị nhân lên chừng đó lần. Mọi tổng tính trên bảng trái sau khi "
                f"ghép sẽ CAO HƠN THỰC TẾ."
            )

        # ── BROADCAST: right key coarser than left grain ───────────────
        if m["matched_valid"] and m["matched_distinct"]:
            l_rows_per_key = m["matched_valid"] / m["matched_distinct"]
            if l_rows_per_key >= BROADCAST_MIN_ROWS_PER_KEY:
                measures = m.get("right_measures") or []
                if measures:
                    out["non_additive"] = measures
                    reason = (
                        "đã tổng hợp sẵn"
                        if right_grain == "aggregate"
                        else f"thô hơn bảng trái ({l_rows_per_key:.0f} dòng trái / 1 dòng phải)"
                    )
                    out["warnings"].append(
                        f'Bảng "{right_name}" có độ chi tiết {reason}, nên sau khi ghép, các cột '
                        f"số của nó bị LẶP LẠI trên nhiều dòng: {', '.join(measures[:6])}"
                        f"{'…' if len(measures) > 6 else ''}. CỘNG các cột này sẽ ra số sai gấp "
                        f"nhiều lần — chỉ được lấy giá trị đại diện (max/first) theo từng nhóm."
                    )
    except Exception:  # noqa: BLE001 - a guard must never break the merge
        return {"warnings": [], "non_additive": []}
    return out


def inspect_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    left_col: str,
    right_col: str,
    right_name: str,
    right_grain: str | None = None,
) -> dict:
    """One join, inspected on the REAL frames just before pandas merges them.

    Returns {"warnings": [str], "non_additive": [str]}. Never raises: a guard
    that breaks the upload is worse than the problem it guards against.
    """
    return judge_join(measure_join(left, right, left_col, right_col),
                      right_name, right_grain)


def format_join_warnings(warnings: list[str], non_additive: list[str]) -> str:
    """The block injected into every downstream prompt. The non-additive list is
    repeated as a hard rule because it is the half a reader would skim past."""
    if not warnings:
        return ""
    lines = ["CẢNH BÁO GHÉP BẢNG (tính bằng code trên dữ liệu thật, không phải suy đoán):"]
    lines += [f"  ⚠ {w}" for w in warnings]
    if non_additive:
        lines.append(
            "  → TUYỆT ĐỐI KHÔNG dùng sum() trên các cột: " + ", ".join(sorted(set(non_additive)))
        )
    return "\n".join(lines)
