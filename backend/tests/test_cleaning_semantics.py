"""Cleaning and the grain-aware dedup decision.

The bug these guard against: clean_and_profile used to call drop_duplicates()
unconditionally. On a line-item sales table two identical rows are legitimate
(same product bought twice on one invoice at the same price), so every such
pair silently understated revenue — with no error, no flag, nothing to notice.

Deleting rows is irreversible, so the rule encoded here is: keep and flag by
default; delete only when the grain proves duplicates are impossible.
"""

import pandas as pd

from app.data.profiler import clean_and_profile, _parse_number
from app.data.semantics import apply_grain_dedup, format_semantics_for_prompt


def _sales_with_legit_duplicate():
    # HD01 contains "Cafe 50000" twice — a real second unit, not a defect.
    return pd.DataFrame({
        "Ma HD": ["HD01", "HD01", "HD01", "HD02"],
        "San pham": ["Cafe", "Cafe", "Banh", "Cafe"],
        "Thanh tien": [50000, 50000, 30000, 50000],
    })


def test_duplicates_are_kept_by_default():
    df, prof = clean_and_profile(_sales_with_legit_duplicate())
    assert len(df) == 4
    assert df["Thanh tien"].sum() == 180000  # 130000 if the duplicate is dropped
    assert prof["duplicate_rows_found"] == 1
    assert prof["duplicate_rows_removed"] == 0


def test_kept_duplicates_are_flagged_so_the_user_can_judge():
    _, prof = clean_and_profile(_sales_with_legit_duplicate())
    assert any("trùng hoàn toàn" in f for f in prof["flags"])


def test_opt_in_flag_restores_deletion():
    df, prof = clean_and_profile(_sales_with_legit_duplicate(), drop_dups=True)
    assert len(df) == 3
    assert prof["duplicate_rows_removed"] == 1


def test_transaction_line_grain_blocks_dedup_even_if_model_says_safe():
    """dedup_safe is the model's opinion; grain_type is the structural fact.
    Both must agree before anything is deleted."""
    df, _ = clean_and_profile(_sales_with_legit_duplicate())
    out, removed = apply_grain_dedup(df, {"grain_type": "transaction_line", "dedup_safe": True})
    assert removed == 0
    assert len(out) == 4


def test_entity_grain_allows_dedup():
    df, _ = clean_and_profile(pd.DataFrame({"Ma": ["A", "A", "B"], "Ten": ["x", "x", "y"]}))
    out, removed = apply_grain_dedup(df, {"grain_type": "entity", "dedup_safe": True})
    assert removed == 1
    assert len(out) == 2


def test_missing_or_unknown_semantics_keeps_everything():
    df, _ = clean_and_profile(pd.DataFrame({"Ma": ["A", "A"], "Ten": ["x", "x"]}))
    assert apply_grain_dedup(df, None)[1] == 0
    assert apply_grain_dedup(df, {"grain_type": "unknown", "dedup_safe": True})[1] == 0
    assert apply_grain_dedup(df, {"grain_type": "entity", "dedup_safe": False})[1] == 0


# ── number parsing: Vietnamese vs US separators ──────────────────────────────

def test_parse_number_handles_both_locales():
    assert _parse_number("1.200.000") == 1200000      # vi thousands
    assert _parse_number("1,200,000") == 1200000      # en thousands
    assert _parse_number("1.200") == 1200             # vi: 3 trailing digits
    assert _parse_number("1.5") == 1.5                # en decimal
    assert _parse_number("94.338,58") == 94338.58     # vi with decimals
    assert _parse_number("1,234.56") == 1234.56       # en with decimals


def test_text_columns_are_coerced_under_the_new_string_dtype():
    """REGRESSION: pandas 3 reads text as StringDtype, not object. The coercion
    loop selected only `object`, and worked purely on a backward-compat shim
    that pandas 4 removes. When it goes, money stored as text stops becoming
    numbers — every KPI silently breaks with no error anywhere."""
    df = pd.DataFrame({"tien": ["1.200.000", "2.500.000"], "ngay": ["2025-01-01", "2025-01-02"]})
    assert all(str(d) != "object" for d in df.dtypes)  # StringDtype, as pandas 3 does
    cleaned, prof = clean_and_profile(df)
    assert pd.api.types.is_numeric_dtype(cleaned["tien"])
    assert cleaned["tien"].sum() == 3700000
    assert pd.api.types.is_datetime64_any_dtype(cleaned["ngay"])


def test_parse_number_strips_currency_and_rejects_text():
    assert _parse_number("1.200.000 ₫") == 1200000
    assert _parse_number("abc") is None
    assert _parse_number("") is None
    assert _parse_number(None) is None


# ── prompt block ─────────────────────────────────────────────────────────────

def test_semantics_prompt_block_warns_about_row_counting():
    block = format_semantics_for_prompt({
        "f::S1": {"grain_type": "transaction_line",
                  "grain_description": "Mỗi dòng là một mặt hàng trong hoá đơn",
                  "primary_measure": "Thành tiền", "measure_unit": "VNĐ",
                  "sheet_role": "fact", "caveats": ["Tồn kho là snapshot"]},
    })
    assert "Mỗi dòng là một mặt hàng" in block
    assert "VNĐ" in block
    assert "Tồn kho là snapshot" in block
    # The whole point of knowing the grain: stop row-count == order-count.
    assert "ĐẾM SỐ DÒNG" in block


def test_semantics_prompt_block_empty_when_unavailable():
    assert format_semantics_for_prompt(None) == ""
    assert format_semantics_for_prompt({}) == ""
