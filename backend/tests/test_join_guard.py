"""Joining a hand-built workbook without corrupting its totals.

Real uploads are not one clean fact table — they are `Đơn hàng` sitting beside
`Tổng hợp theo tháng`, both already carrying formulas. Merging those is where
numbers quietly become multiples of themselves, so both distortions are pinned
down here on frames small enough that the right answer is countable by hand.

Nothing in these tests names a business concept. The guard reasons about key
cardinality only, so the same protection holds for payroll, inventory or sensor
logs — the generality is the point, not an accident.
"""

import pandas as pd

from app.data.join_guard import inspect_join
from app.data.merge import apply_join_plan


def _orders():
    """6 transactions across 2 months."""
    return pd.DataFrame({
        "ma_dh": [1, 2, 3, 4, 5, 6],
        "thang": ["2024-01"] * 3 + ["2024-02"] * 3,
        "tien": [100, 200, 300, 400, 500, 600],
    })


def _monthly_summary():
    """The pre-computed sheet: 1 row per month, totals already added up."""
    return pd.DataFrame({
        "thang": ["2024-01", "2024-02"],
        "tong_thang": [600, 1500],
        "so_don": [3, 3],
    })


def test_broadcast_is_detected_when_the_right_table_is_coarser():
    found = inspect_join(_orders(), _monthly_summary(), "thang", "thang", "TongHop")
    assert set(found["non_additive"]) == {"tong_thang", "so_don"}
    assert found["warnings"]


def test_broadcast_really_would_have_multiplied_the_total():
    """The failure the guard exists for, demonstrated: the correct grand total
    is 2,100 but summing the broadcast column returns 6,300."""
    merged = _orders().merge(_monthly_summary(), on="thang", how="left")
    assert merged["tien"].sum() == 2100          # untouched, still right
    assert merged["tong_thang"].sum() == 6300    # 3x the truth
    assert len(merged) == 6                      # and NO row count change to notice


def test_fanout_is_detected_when_the_right_key_repeats():
    """A right key that repeats duplicates left rows, inflating the LEFT
    table's own measures — the ones the user trusts most."""
    lines = pd.DataFrame({"ma_dh": [1, 1, 2], "chi_tiet": ["a", "b", "c"]})
    found = inspect_join(_orders(), lines, "ma_dh", "ma_dh", "ChiTiet")
    assert any("nhân lên" in w for w in found["warnings"])


def test_clean_one_to_one_join_is_left_alone():
    """A genuine lookup table must produce no warning at all, or the guard
    becomes noise everyone learns to ignore."""
    lookup = pd.DataFrame({"ma_dh": [1, 2, 3, 4, 5, 6], "kenh": list("abcdef")})
    found = inspect_join(_orders(), lookup, "ma_dh", "ma_dh", "Kenh")
    assert found["warnings"] == []
    assert found["non_additive"] == []


def test_dimension_table_with_no_measures_is_not_flagged():
    """Region names broadcast across many rows are harmless — there is nothing
    to sum. Flagging them would train the user to dismiss the warning."""
    orders = pd.DataFrame({"ma_kv": [1, 1, 1, 2, 2], "tien": [10, 20, 30, 40, 50]})
    dim = pd.DataFrame({"ma_kv": [1, 2], "ten_kv": ["Bắc", "Nam"]})
    found = inspect_join(orders, dim, "ma_kv", "ma_kv", "KhuVuc")
    assert found["non_additive"] == []


def test_apply_join_plan_reports_without_changing_the_merge():
    """The guard observes; it must not alter the frame callers already rely on."""
    dfs = {"f::DonHang": _orders(), "f::TongHop": _monthly_summary()}
    joins = [{"left_file": "f::DonHang", "left_column": "thang",
              "right_file": "f::TongHop", "right_column": "thang"}]

    plain = apply_join_plan(dfs, joins, "f::DonHang")
    report: dict = {}
    guarded = apply_join_plan(dfs, joins, "f::DonHang",
                              semantics={"f::TongHop": {"grain_type": "aggregate"}},
                              report=report)

    pd.testing.assert_frame_equal(plain, guarded)
    assert set(report["non_additive"]) == {"tong_thang", "so_don"}
    assert "đã tổng hợp sẵn" in " ".join(report["warnings"])


def test_report_is_optional_and_absent_semantics_still_works():
    dfs = {"f::DonHang": _orders(), "f::TongHop": _monthly_summary()}
    joins = [{"left_file": "f::DonHang", "left_column": "thang",
              "right_file": "f::TongHop", "right_column": "thang"}]
    assert len(apply_join_plan(dfs, joins, "f::DonHang")) == 6


def test_guard_failure_never_breaks_the_merge():
    """A guard that raises is worse than the problem it guards against."""
    broken = pd.DataFrame({"x": [1]})
    assert inspect_join(broken, broken, "khong_ton_tai", "x", "T") == {
        "warnings": [], "non_additive": []
    }
