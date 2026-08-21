"""Unit tests for writing a verified copilot grid back into session state."""

import pandas as pd
from app.routers.agent import _persist_copilot_result


def _state():
    df = pd.DataFrame({"Revenue": [100, 200], "Cost": [60, 120]})
    return {
        "dataframes": {"Sales": df},
        "raw_grids": {"Sales": {"grid": [["Revenue", "Cost"], [100, 60], [200, 120]]}},
        "cleaned_df": "stale",
    }


def test_added_column_is_persisted_into_state():
    state = _state()
    grid = [["Revenue", "Cost", "Profit"], ["100", "60", "40"], ["200", "120", "80"]]

    assert _persist_copilot_result(state, "Sales", grid) is True

    df = state["dataframes"]["Sales"]
    assert list(df.columns) == ["Revenue", "Cost", "Profit"]
    assert len(df) == 2
    # Numbers crossed as JSON strings; the analysis path needs them numeric.
    assert pd.api.types.is_numeric_dtype(df["Profit"])
    assert float(df["Profit"].iloc[0]) == 40.0
    # The raw grid is the file as uploaded; a copilot column is not in the file,
    # and the header-row reparse reads this back.
    assert state["raw_grids"]["Sales"]["grid"] == [["Revenue", "Cost"], [100, 60], [200, 120]]
    # A merged view built from the old columns must not survive the edit.
    assert "cleaned_df" not in state


def test_aggregation_is_not_persisted():
    """Fewer rows means the result summarises the sheet rather than extending it."""
    state = _state()
    grid = [["Revenue", "Cost"], ["300", "180"]]

    assert _persist_copilot_result(state, "Sales", grid) is False
    assert len(state["dataframes"]["Sales"]) == 2
    assert state["cleaned_df"] == "stale"


def test_dropped_column_is_not_persisted():
    """Losing an original column is data loss, whatever the row count says."""
    state = _state()
    grid = [["Revenue"], ["100"], ["200"]]

    assert _persist_copilot_result(state, "Sales", grid) is False
    assert list(state["dataframes"]["Sales"].columns) == ["Revenue", "Cost"]


def test_unknown_source_is_not_persisted():
    state = _state()
    grid = [["A"], ["1"], ["2"]]

    assert _persist_copilot_result(state, "KhongCo", grid) is False
    assert _persist_copilot_result(state, None, grid) is False
    assert _persist_copilot_result(state, "Sales", None) is False
