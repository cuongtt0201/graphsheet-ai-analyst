"""Join-key handling.

Both bugs covered here were real and both corrupted numbers SILENTLY, which is
why they get tests: one crashed loudly (dtype mismatch) and one did not
(NaN↔NaN matching quietly multiplied rows).
"""

import pandas as pd

from app.data.merge import _canonical_key, apply_join_plan


def _join(left, right, col="k"):
    return apply_join_plan(
        {"L": left, "R": right},
        [{"left_file": "L", "left_column": col, "right_file": "R", "right_column": col}],
        "L",
    )


def test_float_key_joins_string_key():
    """REGRESSION: "You are trying to merge on float64 and string columns".
    The trap in fixing it: float64 renders 12345 as "12345.0", which would
    still never match the text "12345" — so a naive astype(str) "fixes" the
    crash while joining nothing at all."""
    left = pd.DataFrame({"k": [12345.0, 67890.0, 11111.0], "sl": [5, 3, 8]})
    right = pd.DataFrame({"k": ["12345", "67890", "22222"], "ten": ["A", "B", "C"]})
    out = _join(left, right)
    assert len(out) == 3
    assert list(out["ten"]) == ["A", "B", ""]


def test_mixed_alphanumeric_keys_still_match_numeric_ones():
    left = pd.DataFrame({"k": [12345.0, 67890.0], "v": [1, 2]})
    right = pd.DataFrame({"k": ["12345", "SP-001"], "ten": ["A", "X"]})
    assert list(_join(left, right)["ten"]) == ["A", ""]


def test_blank_keys_never_join_to_each_other():
    """REGRESSION: pandas matches NaN to NaN, so every key-less row on the
    right attached itself to every key-less row on the left — inventing
    relationships and inflating totals with nothing visible going wrong."""
    left = pd.DataFrame({"k": [1.0, None], "a": [10, 20]})
    right = pd.DataFrame({"k": ["1", ""], "b": ["x", "MUST-NOT-APPEAR"]})
    out = _join(left, right)
    assert len(out) == 2
    assert "MUST-NOT-APPEAR" not in list(out["b"])


def test_nan_keys_do_not_multiply_rows():
    left = pd.DataFrame({"k": [None, None], "v": [1, 2]})
    right = pd.DataFrame({"k": [None, None], "n": ["p", "q"]})
    assert len(_join(left, right)) == 2  # not 4


def test_decimal_floats_are_not_coerced_to_integers():
    left = pd.DataFrame({"k": [1.5, 2.5], "v": [1, 2]})
    right = pd.DataFrame({"k": ["1.5", "9.9"], "n": ["ok", "no"]})
    assert list(_join(left, right)["n"]) == ["ok", ""]


def test_matching_dtypes_are_left_untouched():
    """A join that already worked must not be altered by the repair path —
    including keeping its original dtype."""
    left = pd.DataFrame({"k": [1, 2], "v": [9, 8]})
    right = pd.DataFrame({"k": [1, 2], "n": ["a", "b"]})
    out = _join(left, right)
    assert list(out["n"]) == ["a", "b"]
    assert str(out["k"].dtype).startswith("int")


def test_canonical_key_renders_whole_floats_without_decimal_point():
    got = list(_canonical_key(pd.Series([12345.0, 67890.0])))
    assert got == ["12345", "67890"]


def test_canonical_key_maps_blanks_to_na():
    out = _canonical_key(pd.Series(["", "  ", "7"]))
    assert out.isna().tolist() == [True, True, False]


def test_missing_join_column_raises_clearly():
    left = pd.DataFrame({"k": [1]})
    right = pd.DataFrame({"other": [1]})
    try:
        _join(left, right)
    except ValueError as exc:
        assert "không có trong bảng" in str(exc)
    else:
        raise AssertionError("expected a ValueError naming the missing column")
