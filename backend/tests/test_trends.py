"""Trend signals — the two artifacts that produced confidently wrong stories.

Both were spotted on the live dashboard, both are arithmetically "correct" and
both are analytically meaningless:

  1. A month with only 5 days of data compared against full months reads as a
     catastrophic collapse ("-83.9%").
  2. A branch that opened mid-period has a near-zero first half, so its growth
     rate explodes ("+14258.8%") and the AI narrates it as a star performer.
"""

import numpy as np
import pandas as pd

from app.data.trends import analyze_trend, format_trend_for_prompt, period_coverage_note


def _daily(start, end, stores, value=100):
    rows = []
    for d in pd.date_range(start, end, freq="D"):
        for s in stores:
            rows.append({"date": d, "store": s, "rev": value})
    return rows


def test_partial_last_period_does_not_read_as_a_collapse():
    """9 full months at a steady level, then 5 days of the 10th month. The raw
    last bucket is ~6x smaller purely because the month is unfinished."""
    rows = _daily("2025-01-01", "2025-09-30", ["A"], 100)
    rows += _daily("2025-10-01", "2025-10-05", ["A"], 100)
    sig = analyze_trend(pd.DataFrame(rows), "date", "rev", group_col="store")

    assert sig["last_period_coverage"] < 0.9
    # Pro-rated, a flat business must not look like a crash.
    assert sig["growth_pct"] > -20
    assert sig["trend"] in ("flat", "up")


def test_partial_period_is_announced_in_the_prompt():
    rows = _daily("2025-01-01", "2025-09-30", ["A"]) + _daily("2025-10-01", "2025-10-05", ["A"])
    block = format_trend_for_prompt(analyze_trend(pd.DataFrame(rows), "date", "rev"))
    assert "chưa kết thúc" in block
    assert "KHÔNG được diễn giải là sụt giảm" in block


def test_complete_data_is_left_alone():
    """Regression guard: the pro-rating must not touch a finished period."""
    rows = _daily("2025-01-01", "2025-12-31", ["A"])
    sig = analyze_trend(pd.DataFrame(rows), "date", "rev")
    assert sig["last_period_coverage"] == 1.0


def test_branch_opened_mid_period_is_reported_as_new_not_as_growth():
    rows = _daily("2025-01-01", "2025-12-31", ["Old"], 100)
    rows += _daily("2025-10-01", "2025-12-31", ["New"], 500)   # opened in Q4
    sig = analyze_trend(pd.DataFrame(rows), "date", "rev", group_col="store")

    movers = {m["group"]: m for m in sig["top_movers"]}
    assert movers["New"].get("new") is True
    assert "change_pct" not in movers["New"]          # no bogus +14000%

    block = format_trend_for_prompt(sig)
    assert "Nhóm MỚI" in block
    assert 'KHÔNG được nói là "tăng trưởng đột biến"' in block


def test_established_groups_still_get_a_normal_percentage():
    rng = np.random.default_rng(0)
    rows = []
    for d in pd.date_range("2025-01-01", "2025-12-31", freq="D"):
        rows.append({"date": d, "store": "A", "rev": 100 + int(rng.integers(0, 5))})
        rows.append({"date": d, "store": "B", "rev": 100 + int(rng.integers(0, 5))})
    sig = analyze_trend(pd.DataFrame(rows), "date", "rev", group_col="store")
    movers = {m["group"]: m for m in sig["top_movers"] if not m.get("new")}
    assert set(movers) == {"A", "B"}
    for m in movers.values():
        assert abs(m["change_pct"]) < 25


def test_incomplete_period_is_flagged_before_code_generation():
    """REGRESSION: fixing the artifact inside analyze_trend only corrected the
    prose, because the KPI deltas are written by AI pandas that runs EARLIER.
    A live dashboard ended up showing "+12.6%" in the commentary and "▼83.9%"
    on the KPI card for the same measure. The warning has to reach the model
    before it writes any comparison."""
    rows = _daily("2025-05-01", "2026-01-31", ["A"]) + _daily("2026-02-01", "2026-02-05", ["A"])
    note = period_coverage_note(pd.DataFrame(rows), "date")
    assert "CHƯA TRỌN VẸN" in note
    assert "compare_value" in note


def test_no_warning_for_complete_data_or_missing_date_column():
    """A spurious warning would push the model into dropping a period it should
    have used — the guard must stay silent unless there is a real problem."""
    rows = _daily("2025-01-01", "2025-12-31", ["A"])
    df = pd.DataFrame(rows)
    assert period_coverage_note(df, "date") == ""
    assert period_coverage_note(df, None) == ""
    assert period_coverage_note(df, "khong_ton_tai") == ""


def test_insufficient_data_returns_none_instead_of_raising():
    tiny = pd.DataFrame({"date": pd.to_datetime(["2025-01-01"]), "rev": [1]})
    assert analyze_trend(tiny, "date", "rev") is None


def test_malformed_input_never_raises():
    """A failed analysis must never take the dashboard down with it."""
    junk = pd.DataFrame({"date": ["not-a-date"] * 5, "rev": ["x"] * 5})
    assert analyze_trend(junk, "date", "rev") is None
    assert format_trend_for_prompt(None) == ""
