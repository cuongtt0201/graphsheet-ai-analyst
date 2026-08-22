"""A copilot edit must survive a refresh without raw_grids ever lying.

raw_grids is defined as the faithful "what the file actually looks like" view,
and the header-row reparse reads it back, so a computed column cannot be written
there. It lives in derived_grids instead, which /api/sheet prefers.
"""

import pandas as pd
from app.routers.agent import _persist_copilot_result


RAW = [["BÁO CÁO", "", ""], ["Mã", "Doanh thu", "Chi phí"], ["0001", 100, 60], ["0002", 200, 120]]


def _state():
    return {
        "dataframes": {"S": pd.DataFrame({"Mã": ["0001", "0002"], "Doanh thu": [100, 200], "Chi phí": [60, 120]})},
        "raw_grids": {"S": {"grid": RAW}},
    }


def test_edit_goes_to_derived_and_leaves_raw_untouched():
    state = _state()
    grid = [["Mã", "Doanh thu", "Chi phí", "Lợi nhuận"], ["0001", "100", "60", "40"], ["0002", "200", "120", "80"]]

    assert _persist_copilot_result(state, "S", grid) is True
    assert state["derived_grids"]["S"]["grid"] == grid
    # The file view is what the reparse and the header detector read back.
    assert state["raw_grids"]["S"]["grid"] == RAW


def test_a_rejected_edit_creates_no_derived_grid():
    """An aggregation is display-only, so nothing should persist for the sheet."""
    state = _state()
    assert _persist_copilot_result(state, "S", [["Mã"], ["0001"]]) is False
    assert "derived_grids" not in state


def test_sheet_endpoint_prefers_the_derived_grid():
    from app.routers.upload import sheet
    import asyncio

    state = _state()
    grid = [["Mã", "Doanh thu", "Chi phí", "Lợi nhuận"], ["0001", "100", "60", "40"], ["0002", "200", "120", "80"]]
    _persist_copilot_result(state, "S", grid)

    class _Req:
        pass

    req = _Req()
    import app.routers.upload as upload_mod
    original = upload_mod.get_state
    upload_mod.get_state = lambda r: state
    try:
        res = asyncio.run(sheet(req, {"source_id": "S"}))
        assert res["derived"] is True
        assert res["grid"] == grid

        del state["derived_grids"]["S"]
        raw = asyncio.run(sheet(req, {"source_id": "S"}))
        assert raw["derived"] is False
        assert raw["grid"] == RAW
    finally:
        upload_mod.get_state = original


def test_derived_grids_are_cleaned_up_with_their_sheet():
    """An entry left behind after a file delete would describe a sheet that is gone."""
    from app.routers.upload import _SHEET_KEYED_STATE

    assert "derived_grids" in _SHEET_KEYED_STATE


def test_the_profile_learns_about_the_new_column():
    """A column the profile does not know about is invisible to every prompt.

    It would also reach the grid with no display format, so a computed money
    column would sit unformatted beside the money columns it came from.
    """
    state = _state()
    grid = [
        ["Mã", "Doanh thu", "Chi phí", "Lợi nhuận"],
        ["0001", "100", "60", "40"],
        ["0002", "200", "120", "80"],
    ]
    state["profiles"] = [{
        "source_id": "S",
        "columns": ["Mã", "Doanh thu", "Chi phí"],
        "column_profiles": [{"name": "Mã", "role": "id"}],
        "row_count": 2,
    }]

    assert _persist_copilot_result(state, "S", grid) is True

    prof = state["profiles"][0]
    assert prof["columns"] == ["Mã", "Doanh thu", "Chi phí", "Lợi nhuận"]
    assert {c["name"] for c in prof["column_profiles"]} == {"Mã", "Doanh thu", "Chi phí", "Lợi nhuận"}
    assert prof["row_count"] == 2

    # And that new column is now formattable as the money it is.
    from app.data.display_format import column_formats
    assert column_formats(prof, None)["Lợi nhuận"] == "currency"


def test_other_sheets_profiles_are_left_alone():
    state = _state()
    state["profiles"] = [
        {"source_id": "S", "columns": ["Mã"], "column_profiles": []},
        {"source_id": "KHAC", "columns": ["X"], "column_profiles": []},
    ]
    grid = [["Mã", "Doanh thu", "Chi phí", "Lợi nhuận"], ["0001", "100", "60", "40"], ["0002", "200", "120", "80"]]
    _persist_copilot_result(state, "S", grid)

    other = next(p for p in state["profiles"] if p["source_id"] == "KHAC")
    assert other == {"source_id": "KHAC", "columns": ["X"], "column_profiles": []}
