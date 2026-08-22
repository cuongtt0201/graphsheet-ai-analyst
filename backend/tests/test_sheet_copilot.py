"""Unit tests for the Sandbox-Verified Spreadsheet Copilot."""

from unittest.mock import MagicMock

import pandas as pd
from app.agent.sheet_copilot import _FULL_FRAME_KEY, apply_sheet_copilot_mutation


def _frame_run(df: pd.DataFrame) -> MagicMock:
    """The sandbox hands back a real DataFrame, however many rows it has."""
    return MagicMock(return_value={"ok": True, "kind": "dataframes", "result": {_FULL_FRAME_KEY: df}})


def _plan(**overrides) -> MagicMock:
    plan = {
        "mutation_type": "add_column_formula",
        "target_column": "Profit",
        "excel_formula": "=A2-B2",
        "python_verification_code": "result = df.assign(Profit=df['Revenue'] - df['Cost'])",
        "explanation": "Doanh thu trừ chi phí",
    }
    plan.update(overrides)
    return MagicMock(return_value=plan)


SALES = pd.DataFrame({"Revenue": [100, 200, 300, 400], "Cost": [60, 120, 180, 240]})
SALES_OUT = SALES.assign(Profit=SALES["Revenue"] - SALES["Cost"])


def test_verified_formula_is_approved():
    res = apply_sheet_copilot_mutation(
        user_prompt="Thêm cột Lợi nhuận",
        dataframes={"Sales": SALES},
        schema_context="Sales(Revenue, Cost)",
        sheet_id="Sales",
        call_ai_fn=_plan(),
        run_pandas_fn=_frame_run(SALES_OUT),
    )

    assert res["ok"] is True
    assert res["applied"] is True
    assert res["verified_in_sandbox"] is True
    assert res["excel_formula"] == "=A2-B2"
    assert res["target"] == "sheet"
    assert res["grid"][0] == ["Revenue", "Cost", "Profit"]
    assert len(res["grid"]) == 5  # header + 4 rows
    assert res["total_rows"] == 4
    # The full frame rides along for the caller to persist.
    assert len(res[_FULL_FRAME_KEY]) == 4


def test_a_formula_that_fails_the_sandbox_is_blocked():
    """A broken formula must never reach the user's sheet."""
    mock_run = MagicMock(return_value={"ok": False, "error": "KeyError: 'NonExistent'"})

    res = apply_sheet_copilot_mutation(
        user_prompt="Tính chỉ số",
        dataframes={"Sales": SALES},
        schema_context="Sales(Revenue, Cost)",
        call_ai_fn=_plan(python_verification_code="result = df['NonExistent'] * 2"),
        run_pandas_fn=mock_run,
    )

    assert res["ok"] is False
    assert res["applied"] is False
    assert "Sandbox" in res["error"]


def test_a_big_sheet_is_edited_in_full_while_the_grid_stays_capped():
    """A 268k-row file is the real case, and it used to be refused outright.

    The dataframe is the source of truth and takes every row; the grid is a view
    and is capped at the same 10k every other sheet is drawn under.
    """
    from app.agent.sheet_copilot import GRID_DISPLAY_ROWS

    n = GRID_DISPLAY_ROWS + 5_000
    # Cost must not track Revenue exactly, or Profit is 0 on every row and the
    # broadcast guard correctly reroutes it as a constant.
    big = pd.DataFrame({"Revenue": range(n), "Cost": [i % 7 for i in range(n)]})
    out = big.assign(Profit=big["Revenue"] - big["Cost"])

    res = apply_sheet_copilot_mutation(
        user_prompt="Thêm cột lợi nhuận",
        dataframes={"Sales": big},
        schema_context="Sales(Revenue, Cost)",
        sheet_id="Sales",
        call_ai_fn=_plan(),
        run_pandas_fn=_frame_run(out),
    )

    assert res["ok"] is True
    assert res["total_rows"] == n              # every row was computed
    assert len(res[_FULL_FRAME_KEY]) == n      # and handed back
    assert len(res["grid"]) == GRID_DISPLAY_ROWS + 1   # but only 10k are drawn
    assert res["grid_truncated"] is True


def test_a_non_table_result_is_refused():
    """A scalar cannot become a sheet; applying one would blank the grid."""
    mock_run = MagicMock(return_value={"ok": True, "kind": "scalar", "result": 6})

    res = apply_sheet_copilot_mutation(
        user_prompt="Tính tổng",
        dataframes={"Sales": SALES},
        schema_context="Sales(Revenue)",
        call_ai_fn=_plan(mutation_type="summary", python_verification_code="result = df['Revenue'].sum()"),
        run_pandas_fn=mock_run,
    )

    assert res["ok"] is False
    assert "bảng dữ liệu" in res["error"]


def test_scalar_broadcast_is_rerouted_to_a_summary_sheet():
    """"Đếm tổng số chương trình" must not write 113 into all 113 rows.

    The model answers such a question with df.assign(Tổng=len(df)), which is
    correct arithmetic and a ruined sheet. The constant column is collapsed back
    into the one-row table it always was, and opens as its own sheet.
    """
    df = pd.DataFrame({"CuaHang": [f"CH{i}" for i in range(5)]})
    out = df.assign(**{"Tổng số chương trình": len(df)})

    res = apply_sheet_copilot_mutation(
        user_prompt="1 cột đếm tổng số chương trình",
        dataframes={"KM": df},
        schema_context="KM(CuaHang)",
        sheet_id="KM",
        call_ai_fn=_plan(
            target_column="Tổng số chương trình",
            excel_formula="=COUNTA(A:A)-1",
            python_verification_code="result = df.assign(**{'Tổng số chương trình': len(df)})",
            explanation="Đếm số chương trình",
        ),
        run_pandas_fn=_frame_run(out),
    )

    assert res["ok"] is True
    assert res["target"] == "new_sheet"
    assert res["mutation_type"] == "summary"
    # One row holding the answer, not five rows holding it five times.
    assert res["grid"] == [["Tổng số chương trình"], [5]]


def test_a_real_per_row_column_still_lands_on_the_sheet():
    """The broadcast guard must not catch a column that genuinely varies."""
    res = apply_sheet_copilot_mutation(
        user_prompt="Thêm cột lợi nhuận",
        dataframes={"Sales": SALES},
        schema_context="Sales(Revenue, Cost)",
        sheet_id="Sales",
        call_ai_fn=_plan(),
        run_pandas_fn=_frame_run(SALES_OUT),
    )

    assert res["target"] == "sheet"
    assert len(res["grid"]) == 5


def test_summary_mutation_opens_as_its_own_sheet():
    """A grouped total keeps its own shape and never overwrites the source."""
    df = pd.DataFrame({"Mien": ["Bac", "Nam", "Bac"], "DoanhThu": [10, 20, 30]})
    out = pd.DataFrame({"Mien": ["Bac", "Nam"], "DoanhThu": [40, 20]})

    res = apply_sheet_copilot_mutation(
        user_prompt="Doanh thu theo miền",
        dataframes={"Sales": df},
        schema_context="Sales(Mien, DoanhThu)",
        sheet_id="Sales",
        call_ai_fn=_plan(
            mutation_type="summary",
            target_column="Doanh thu theo miền",
            excel_formula='=SUMIF(A:A,"Bac",B:B)',
            python_verification_code="result = df.groupby('Mien', as_index=False)['DoanhThu'].sum()",
            explanation="Tổng doanh thu theo miền",
        ),
        run_pandas_fn=_frame_run(out),
    )

    assert res["target"] == "new_sheet"
    assert res["grid"] == [["Mien", "DoanhThu"], ["Bac", 40], ["Nam", 20]]


def test_the_wrapper_that_routes_frames_through_parquet_is_appended():
    """Without the wrap the sandbox serializes a capped table, not a frame."""
    from app.agent.sheet_copilot import _FULL_FRAME_WRAP

    run = _frame_run(SALES_OUT)
    apply_sheet_copilot_mutation(
        user_prompt="Thêm cột lợi nhuận",
        dataframes={"Sales": SALES},
        schema_context="Sales(Revenue, Cost)",
        sheet_id="Sales",
        call_ai_fn=_plan(),
        run_pandas_fn=run,
    )

    code_sent = run.call_args.args[0]
    assert code_sent.endswith(_FULL_FRAME_WRAP)
    assert "max_rows" not in run.call_args.kwargs
