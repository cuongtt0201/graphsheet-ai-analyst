"""Unit tests for Sandbox-Verified Spreadsheet Copilot."""

from unittest.mock import MagicMock
import pandas as pd
from app.agent.sheet_copilot import APPLY_MAX_ROWS, apply_sheet_copilot_mutation


def test_sheet_copilot_successful_verification():
    """Verify formula that runs cleanly in sandbox is approved."""
    df = pd.DataFrame({"Revenue": [100, 200, 300], "Cost": [60, 120, 180]})
    dfs = {"Sales": df}

    mock_call_ai = MagicMock(return_value={
        "mutation_type": "add_column_formula",
        "target_column": "Profit",
        "excel_formula": "=A2-B2",
        "python_verification_code": "result = df.assign(Profit=df['Revenue'] - df['Cost'])",
        "explanation": "Tính lợi nhuận bằng Doanh thu trừ Chi phí",
    })

    mock_run_pandas = MagicMock(return_value={
        "ok": True,
        "kind": "table",
        "result": {"columns": ["Revenue", "Cost", "Profit"], "rows": [[100, 60, 40], [200, 120, 80], [300, 180, 120]]},
    })

    res = apply_sheet_copilot_mutation(
        user_prompt="Thêm cột Lợi nhuận",
        dataframes=dfs,
        schema_context="Sales(Revenue, Cost)",
        call_ai_fn=mock_call_ai,
        run_pandas_fn=mock_run_pandas,
    )

    assert res["ok"] is True
    assert res["applied"] is True
    assert res["excel_formula"] == "=A2-B2"
    assert res["target_column"] == "Profit"
    assert res["verified_in_sandbox"] is True


def test_sheet_copilot_blocks_invalid_formula():
    """Verify formula that fails in sandbox is blocked from corrupting the sheet."""
    df = pd.DataFrame({"Revenue": [100, 200]})
    dfs = {"Sales": df}

    # AI suggests broken python test code
    mock_call_ai = MagicMock(return_value={
        "mutation_type": "add_column_formula",
        "target_column": "Profit",
        "excel_formula": "=A2-Z2",
        "python_verification_code": "result = df['NonExistent'] * 2",
        "explanation": "Test",
    })

    # Sandbox consistently returns KeyError
    mock_run_pandas = MagicMock(return_value={
        "ok": False,
        "error": "KeyError: 'NonExistent'",
    })

    res = apply_sheet_copilot_mutation(
        user_prompt="Tính chỉ số",
        dataframes=dfs,
        schema_context="Sales(Revenue)",
        call_ai_fn=mock_call_ai,
        run_pandas_fn=mock_run_pandas,
    )

    assert res["ok"] is False
    assert res["applied"] is False
    assert "Sandbox" in res["error"]


def test_sheet_copilot_returns_full_grid_for_the_sheet():
    """The verified result crosses back as a header row plus every data row."""
    df = pd.DataFrame({"Revenue": [100, 200, 300], "Cost": [60, 120, 180]})

    mock_call_ai = MagicMock(return_value={
        "mutation_type": "add_column_formula",
        "target_column": "Profit",
        "excel_formula": "=A2-B2",
        "python_verification_code": "result = df.assign(Profit=df['Revenue'] - df['Cost'])",
        "explanation": "Doanh thu trừ chi phí",
    })
    mock_run_pandas = MagicMock(return_value={
        "ok": True,
        "kind": "table",
        "result": {
            "columns": ["Revenue", "Cost", "Profit"],
            "rows": [[100, 60, 40], [200, 120, 80], [300, 180, 120]],
            "total_rows": 3,
            "truncated": False,
        },
    })

    res = apply_sheet_copilot_mutation(
        user_prompt="Thêm cột Lợi nhuận",
        dataframes={"Sales": df},
        schema_context="Sales(Revenue, Cost)",
        call_ai_fn=mock_call_ai,
        run_pandas_fn=mock_run_pandas,
    )

    assert res["ok"] is True
    assert res["grid"][0] == ["Revenue", "Cost", "Profit"]
    assert len(res["grid"]) == 4  # header + 3 rows
    assert res["total_rows"] == 3
    # The cap has to be lifted, or a real sheet would come back sampled.
    assert mock_run_pandas.call_args.kwargs["max_rows"] == APPLY_MAX_ROWS


def test_sheet_copilot_refuses_a_truncated_result():
    """A partial grid written into a live sheet would delete the missing rows."""
    df = pd.DataFrame({"Revenue": [1, 2, 3]})

    mock_call_ai = MagicMock(return_value={
        "mutation_type": "add_column_formula",
        "target_column": "Double",
        "excel_formula": "=A2*2",
        "python_verification_code": "result = df.assign(Double=df['Revenue'] * 2)",
        "explanation": "Nhân đôi",
    })
    mock_run_pandas = MagicMock(return_value={
        "ok": True,
        "kind": "table",
        "result": {
            "columns": ["Revenue", "Double"],
            "rows": [[1, 2]],
            "total_rows": 90_000,
            "truncated": True,
        },
    })

    res = apply_sheet_copilot_mutation(
        user_prompt="Nhân đôi",
        dataframes={"Sales": df},
        schema_context="Sales(Revenue)",
        call_ai_fn=mock_call_ai,
        run_pandas_fn=mock_run_pandas,
    )

    assert res["ok"] is False
    assert res["applied"] is False
    assert "90,000" in res["error"]
    assert "grid" not in res


def test_sheet_copilot_refuses_a_non_table_result():
    """A scalar cannot become a sheet; applying one would blank the grid."""
    df = pd.DataFrame({"Revenue": [1, 2, 3]})

    mock_call_ai = MagicMock(return_value={
        "mutation_type": "transform",
        "target_column": "Total",
        "excel_formula": "=SUM(A:A)",
        "python_verification_code": "result = df['Revenue'].sum()",
        "explanation": "Tổng",
    })
    mock_run_pandas = MagicMock(return_value={"ok": True, "kind": "scalar", "result": 6})

    res = apply_sheet_copilot_mutation(
        user_prompt="Tính tổng",
        dataframes={"Sales": df},
        schema_context="Sales(Revenue)",
        call_ai_fn=mock_call_ai,
        run_pandas_fn=mock_run_pandas,
    )

    assert res["ok"] is False
    assert "bảng dữ liệu" in res["error"]
