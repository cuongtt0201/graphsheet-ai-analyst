"""Unit tests for Sandbox-Verified Spreadsheet Copilot."""

from unittest.mock import MagicMock
import pandas as pd
from app.agent.sheet_copilot import apply_sheet_copilot_mutation


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
