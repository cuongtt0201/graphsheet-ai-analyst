"""One shared understanding for every path.

The bug this prevents actually shipped: the incomplete-final-period fix lived
in only one of the two context builders, so a live dashboard printed
"+12.6% tăng trưởng" in its commentary directly above a KPI card reading
"▼83.9%" — same measure, same file, two answers. Any context the chat path and
the dashboard path assemble separately can drift apart like that, so the parts
describing what the data MEANS and what it SAYS come from one function.
"""

import pandas as pd

from app.data.context import shared_understanding
from app.data.eda import profile_facts

STATE = {
    "semantics": {
        "f::S1": {
            "grain_type": "transaction_line",
            "grain_description": "Mỗi dòng là một mặt hàng trong hoá đơn",
            "primary_measure": "Thành tiền",
            "measure_unit": "VNĐ",
            "sheet_role": "fact",
            "caveats": ["Cột Tồn kho là snapshot"],
        }
    },
    "eda_facts": {"f::S1": ['"khu_vuc": nhóm "Miền Bắc" chiếm 83% tổng doanh thu.']},
}


def test_both_paths_get_identical_understanding():
    chat_block = shared_understanding(STATE)
    dashboard_block = shared_understanding(STATE, df=None, date_col=None)
    assert chat_block == dashboard_block
    assert chat_block != ""


def test_block_carries_meaning_and_observations_together():
    block = shared_understanding(STATE)
    assert "Mỗi dòng là một mặt hàng" in block   # grain
    assert "VNĐ" in block                        # unit
    assert "Tồn kho là snapshot" in block        # caveat
    assert "Miền Bắc" in block                   # observed fact


def test_coverage_warning_only_on_the_path_that_has_a_frame():
    """Chat spans sheets with different date columns, so a single coverage
    warning there would be attached to the wrong table."""
    rows = [{"ngay": d, "tien": 1} for d in pd.date_range("2025-01-01", "2025-09-30", freq="D")]
    rows += [{"ngay": d, "tien": 1} for d in pd.date_range("2025-10-01", "2025-10-05", freq="D")]
    df = pd.DataFrame(rows)

    assert "CHƯA TRỌN VẸN" in shared_understanding(STATE, df=df, date_col="ngay")
    assert "CHƯA TRỌN VẸN" not in shared_understanding(STATE)


def test_empty_session_yields_empty_block():
    """A session with nothing understood yet must add nothing to the prompt
    rather than a header with no content under it."""
    assert shared_understanding({}) == ""
    assert shared_understanding({"semantics": {}, "eda_facts": {}}) == ""


def test_observed_facts_are_computed_not_guessed():
    """Facts come from pandas, so the numbers can be asserted exactly — that is
    what makes them safe to paste into a prompt as ground truth.

    Asserts the measured SHARE, not the wording around it: the layer must state
    what it measured and must NOT classify it ("rất lệch" / "khá đều"), since
    whether 99% concentration is alarming depends on the domain."""
    rows = [{"kv": "Bắc", "tien": 1000} for _ in range(90)]
    rows += [{"kv": "Nam", "tien": 100} for _ in range(10)]
    joined = " ".join(profile_facts(pd.DataFrame(rows), {"primary_measure": "tien"}))
    assert "Bắc" in joined
    assert "98.9%" in joined or "99.0%" in joined          # 90000 / 91000
    # No verdicts — the backend measures, the LLM judges.
    for verdict in ("rất lệch", "khá đều", "bất thường cao"):
        assert verdict not in joined


def test_negative_values_are_surfaced_without_being_explained():
    """Negatives change how a total should be read, so their share must be
    stated. But whether they are refunds, corrections or a sign convention is
    a business question — the data layer must not assert one."""
    df = pd.DataFrame({"kv": ["A"] * 100, "tien": [100] * 90 + [-50] * 10})
    joined = " ".join(profile_facts(df, {"primary_measure": "tien"}))
    assert "10.0%" in joined and "âm" in joined.lower()
    assert "hàng trả lại" not in joined


def test_long_tail_is_reported_as_mean_vs_median():
    """A mean pulled by a long tail is the most common way a correct number
    misleads; stating both figures lets the reader see it."""
    df = pd.DataFrame({"tien": [100] * 99 + [1_000_000]})
    joined = " ".join(profile_facts(df, {"primary_measure": "tien"}))
    assert "trung vị" in joined


def test_profile_facts_survive_empty_and_malformed_input():
    assert profile_facts(None) == []
    assert profile_facts(pd.DataFrame()) == []
