"""Messy-spreadsheet header detection.

The governing rule for this module: a heuristic may improve a file it would
otherwise get wrong, but it must NEVER make an already-correct file worse.
Every guard here exists because a false positive was found in testing — each
one silently mangles every column name in the sheet when it misfires.
"""

import pandas as pd

from app.data.smart_read import smart_read_grid


def _grid(rows):
    return pd.DataFrame(rows)


def test_plain_table_keeps_first_row_as_header():
    df, meta = smart_read_grid(_grid([
        ["Cửa hàng", "Doanh thu", "Khu vực"],
        ["A", 100, "Bắc"],
        ["B", 200, "Nam"],
    ]))
    assert list(df.columns) == ["Cửa hàng", "Doanh thu", "Khu vực"]
    assert meta["header_row"] == 0
    assert len(df) == 2


def test_title_banner_above_the_table_is_skipped():
    df, meta = smart_read_grid(_grid([
        ["CÔNG TY TNHH ABC", None, None],
        ["Báo cáo doanh thu quý 1", None, None],
        [None, None, None],
        ["Cửa hàng", "Doanh thu", "Khu vực"],
        ["A", 100, "Bắc"],
        ["B", 200, "Nam"],
    ]))
    assert list(df.columns) == ["Cửa hàng", "Doanh thu", "Khu vực"]
    assert meta["header_row"] == 3


def test_date_rows_are_not_mistaken_for_header_text():
    """REGRESSION: dates look "texty" to a naive numeric check (they contain
    dashes), so a date-first data row could outscore the real header row."""
    df, meta = smart_read_grid(_grid([
        ["Ngày", "Doanh thu"],
        ["2025-01-01", 100],
        ["2025-01-02", 200],
        ["2025-01-03", 300],
    ]))
    assert list(df.columns) == ["Ngày", "Doanh thu"]
    assert meta["header_row"] == 0


def test_full_first_data_row_is_not_treated_as_a_second_header():
    """REGRESSION: two-level header support once swallowed a completely filled
    first data row (['Q1', 100, 200, 'ok']) as a sub-label row, which renamed
    every column and dropped a row of real data."""
    df, meta = smart_read_grid(_grid([
        ["Quý", "Doanh thu", "Chi phí", "Ghi chú"],
        ["Q1", 100, 200, "ok"],
        ["Q2", 150, 210, "ok"],
    ]))
    assert list(df.columns) == ["Quý", "Doanh thu", "Chi phí", "Ghi chú"]
    assert meta.get("two_level_header") is not True
    assert len(df) == 2


def test_two_level_header_is_combined():
    """A merged group header spanning sub-labels: only the first cell of the
    group is populated, the rest blank — that sparseness is the signal."""
    df, meta = smart_read_grid(_grid([
        ["Cửa hàng", "Doanh thu", None, "Ghi chú"],
        [None, "Đầu kỳ", "Cuối kỳ", None],
        ["A", 10, 20, "x"],
        ["B", 30, 40, "y"],
    ]))
    assert meta.get("two_level_header") is True
    assert any("Đầu kỳ" in str(c) for c in df.columns)
    assert any("Cuối kỳ" in str(c) for c in df.columns)
    assert len(df) == 2


def test_empty_input_degrades_quietly():
    df, meta = smart_read_grid(_grid([]))
    assert len(df) == 0
    assert meta["low_confidence"] is True


def test_metadata_shape_is_stable():
    """The frontend banner and the /api/reparse override both read these keys."""
    _, meta = smart_read_grid(_grid([["A", "B"], [1, 2], [3, 4]]))
    for key in ("header_row", "confidence", "totals_dropped", "low_confidence"):
        assert key in meta
