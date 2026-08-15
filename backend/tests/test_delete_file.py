"""Deleting an uploaded file must delete what was DERIVED from it.

The dataframes are the easy half. The upload pipeline also builds a layer of
understanding on top of them — grain, column formulas, statistical signals,
foreign keys, the merged frame — and every piece of it is rendered into the
shared context block that goes into every single prompt.

Left behind, that layer does not sit idle. It keeps describing a sheet the user
just removed, and nothing contradicts it: no exception, no empty result, just an
assistant confidently citing totals from data that is gone. So the test that
matters is not "is the dataframe gone" but "can the deleted sheet still appear
in a prompt".
"""

import pandas as pd
import pytest

from app.data.context import shared_understanding
from app.routers.upload import delete_file


class _Req:
    """delete_file only ever reaches the session through get_state(request)."""


@pytest.fixture
def state(monkeypatch):
    s = {
        "profiles": [
            {"source_id": "keep.xlsx::S1", "filename": "keep.xlsx", "row_count": 5},
            {"source_id": "bo.xlsx::S1", "filename": "bo.xlsx", "row_count": 9},
        ],
        "dataframes": {"keep.xlsx::S1": pd.DataFrame({"a": [1]}),
                       "bo.xlsx::S1": pd.DataFrame({"b": [2]})},
        "semantics": {
            "keep.xlsx::S1": {"grain_type": "transaction", "grain_description": "1 giao dịch giữ lại"},
            "bo.xlsx::S1": {"grain_type": "entity", "grain_description": "1 dòng cần bị xoá"},
        },
        "eda_facts": {"keep.xlsx::S1": {}, "bo.xlsx::S1": {}},
        "formulas": {"keep.xlsx::S1": [], "bo.xlsx::S1": [
            {"target": "cot_bi_xoa", "left": "x", "op": "×", "right": "y", "ratio": 1.0}]},
        "signals": {"keep.xlsx::S1": [], "bo.xlsx::S1": []},
        "file_fingerprints": {"keep.xlsx::S1": "aa", "bo.xlsx::S1": "bb"},
        "raw_grids": {"keep.xlsx::S1": [], "bo.xlsx::S1": []},
        "fk_links": [{"child": "bo.xlsx::S1", "child_col": "b",
                      "parent": "keep.xlsx::S1", "parent_col": "a",
                      "containment": 1.0, "rows_per_key": 1.0}],
        "cleaned_df": pd.DataFrame({"merged": [1, 2]}),
        "cleaned_schema": {"columns": ["merged"]},
        "join_warnings": ["cảnh báo từ lần ghép cũ"],
        "non_additive_columns": ["cot_bi_xoa"],
    }
    monkeypatch.setattr("app.routers.upload.get_state", lambda _r: s)
    return s


def test_deleted_sheet_cannot_reappear_in_any_prompt(state):
    """The whole point: after the delete, nothing about the removed sheet may
    survive into the context every prompt receives."""
    before = shared_understanding(state)
    assert "bo.xlsx" in before                      # it really was in there

    delete_file(_Req(), "bo.xlsx")

    after = shared_understanding(state)
    assert "bo.xlsx" not in after
    assert "cần bị xoá" not in after
    assert "cot_bi_xoa" not in after


def test_the_surviving_file_is_untouched(state):
    delete_file(_Req(), "bo.xlsx")
    assert "keep.xlsx" in shared_understanding(state)
    assert list(state["dataframes"]) == ["keep.xlsx::S1"]
    assert state["semantics"]["keep.xlsx::S1"]["grain_type"] == "transaction"


def test_cross_sheet_derivations_are_dropped_not_filtered(state):
    """The merged frame and the foreign keys were computed FROM the full set of
    sheets. With one removed they are not partly right — they are stale, and
    keeping a filtered version would preserve numbers nobody can reproduce."""
    delete_file(_Req(), "bo.xlsx")
    for key in ("cleaned_df", "cleaned_schema", "fk_links",
                "join_warnings", "non_additive_columns"):
        assert key not in state, f"{key} survived the delete"


def test_remaining_count_reflects_files_not_sheets(state):
    assert delete_file(_Req(), "bo.xlsx") == {"status": "ok", "remaining_files": 1}


def test_deleting_the_last_file_leaves_an_empty_but_usable_session(state):
    delete_file(_Req(), "bo.xlsx")
    out = delete_file(_Req(), "keep.xlsx")
    assert out["remaining_files"] == 0
    assert state["profiles"] == []
    assert shared_understanding(state) == ""


def test_deleting_an_unknown_file_changes_nothing_about_the_data(state):
    delete_file(_Req(), "khong-ton-tai.xlsx")
    assert set(state["dataframes"]) == {"keep.xlsx::S1", "bo.xlsx::S1"}
    assert set(state["semantics"]) == {"keep.xlsx::S1", "bo.xlsx::S1"}


def test_prefix_match_does_not_delete_a_similarly_named_file(state):
    """"bo.xlsx" must not take "bo.xlsx.bak" or "bo.xlsx2" with it."""
    state["dataframes"]["bo.xlsx2::S1"] = pd.DataFrame({"c": [3]})
    state["semantics"]["bo.xlsx2::S1"] = {"grain_type": "entity"}
    delete_file(_Req(), "bo.xlsx")
    assert "bo.xlsx2::S1" in state["dataframes"]
    assert "bo.xlsx2::S1" in state["semantics"]
