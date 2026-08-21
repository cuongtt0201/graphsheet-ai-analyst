"""Unit tests for turning a colour rule into cells the grid should tint."""

from app.agent.sheet_copilot import _compute_highlights, _matching_rows


def _table():
    return {
        "columns": ["CuaHang", "LoiNhuan"],
        "rows": [["A", 50], ["B", -20], ["C", 300], ["D", -5]],
        "total_rows": 4,
        "truncated": False,
    }


def test_negative_rule_tints_whole_rows_by_default():
    rule = {"condition": "negative", "color_bg": "#FEE2E2", "color_text": "#991B1B"}
    hits = _compute_highlights(_table(), "LoiNhuan", rule)

    # Rows 2 and 4 of the grid: index 0 is the header, so data starts at 1.
    assert {h["row"] for h in hits} == {2, 4}
    # Whole row means every column, not just the one the rule names.
    assert {h["col"] for h in hits} == {0, 1}
    assert hits[0]["bg"] == "#FEE2E2"


def test_cell_scope_tints_only_the_named_column():
    rule = {"condition": "negative", "scope": "cell"}
    hits = _compute_highlights(_table(), "LoiNhuan", rule)

    assert {h["col"] for h in hits} == {1}
    assert {h["row"] for h in hits} == {2, 4}


def test_greater_than_reads_numbers_that_crossed_as_strings():
    """The grid arrives as JSON, so "1,500" has to compare as 1500."""
    table = {"columns": ["X"], "rows": [["1,500"], ["90"], ["2 000"]]}
    hits = _compute_highlights(table, "X", {"condition": "greater_than", "threshold": "100"})

    assert {h["row"] for h in hits} == {1, 3}


def test_unknown_column_or_missing_rule_tints_nothing():
    assert _compute_highlights(_table(), "KhongCo", {"condition": "negative"}) == []
    assert _compute_highlights(_table(), "LoiNhuan", None) == []
    assert _compute_highlights(_table(), "LoiNhuan", {}) == []


def test_outlier_needs_enough_points_before_it_will_judge():
    """Quartiles from a handful of rows are noise, so nothing is flagged."""
    few = [10, 12, 500, 11, 13]
    assert _matching_rows(few, {"condition": "outlier"}) == []

    many = [10, 11, 12, 13, 10, 12, 11, 13, 500]
    assert _matching_rows(many, {"condition": "outlier"}) == [8]


def test_equal_to_compares_text_as_written():
    column = ["Miền Bắc", "Miền Nam", "Miền Bắc"]
    assert _matching_rows(column, {"condition": "equal_to", "threshold": "Miền Bắc"}) == [0, 2]
