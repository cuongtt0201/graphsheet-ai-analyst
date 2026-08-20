"""Unit tests for Alpha Meta-Cognitive Orchestrator.

Covers:
1. InnerMonologueState updates (without hardcoded confidence in summary).
2. Skills environment propagation to Sandbox worker.
3. Code Retry Loop on KeyError / execution errors (healing with columns_reference).
4. Mind Shift loop on true statistical anomalies from Critic.
5. Join warnings propagation to Storyteller.
6. Vega-Lite chart verification.
"""

from unittest.mock import MagicMock, patch
import pandas as pd

from app.agent.alpha import InnerMonologueState, MindShift, run_alpha_cognition


def test_inner_monologue_state_recording():
    """Verify state updates and summary formatting without fake confidence."""
    state = InnerMonologueState(
        user_intent="Tìm hiểu doanh thu quý 3",
        current_hypothesis="Doanh thu tăng trưởng đều",
    )
    
    assert len(state.mind_shifts) == 0
    
    shift = state.record_mind_shift(
        trigger="Lợi nhuận âm ở nhóm hàng A",
        new_hypothesis="Đào sâu vào nhóm hàng A bị lỗ",
    )
    
    assert shift.previous_hypothesis == "Doanh thu tăng trưởng đều"
    assert shift.adapted_hypothesis == "Đào sâu vào nhóm hàng A bị lỗ"
    assert state.current_hypothesis == "Đào sâu vào nhóm hàng A bị lỗ"
    assert len(state.mind_shifts) == 1
    
    summary = state.to_summary()
    assert "Ý THỨC NỘI TÂM" in summary
    assert "LỊCH SỬ BẺ LÁI" in summary
    assert "Độ tin cậy: 80%" not in summary


def test_alpha_skills_propagation_to_sandbox():
    """Verify skills_env and skills_source are forwarded to run_pandas_fn."""
    fake_df = pd.DataFrame({"revenue": [100, 200, 300]})
    dfs = {"main": fake_df}
    fake_skills_env = {"custom_calc": lambda x: x * 2}
    fake_skills_source = "def custom_calc(x): return x * 2"

    mock_run_pandas = MagicMock(return_value={
        "ok": True,
        "kind": "scalar",
        "result": 1200,
        "join_warnings": [],
        "non_additive": [],
    })

    mock_call_ai = MagicMock(return_value={
        "answer": "Tổng doanh thu sau khi tính custom_calc là 1200.",
        "chart": None,
        "follow_up": ["Xem chi tiết theo từng dòng?"],
    })

    res = run_alpha_cognition(
        question="Tính doanh thu theo công thức custom_calc",
        dataframes=dfs,
        schema_context="main(revenue: int)",
        initial_code="result = custom_calc(df['revenue'].sum())",
        initial_hypothesis="Dùng custom skill",
        skills_env=fake_skills_env,
        skills_source=fake_skills_source,
        call_ai_fn=mock_call_ai,
        run_pandas_fn=mock_run_pandas,
    )

    assert res["ok"] is True
    assert res["scalar"] == 1200
    mock_run_pandas.assert_called_once_with(
        "result = custom_calc(df['revenue'].sum())",
        dfs,
        skills_env=fake_skills_env,
        skills_source=fake_skills_source,
    )


def test_alpha_code_retry_loop_on_key_error():
    """Verify execution failure triggers code fix retry with columns_reference (NOT a fake Mind Shift)."""
    fake_df = pd.DataFrame({"actual_sales": [10, 20, 30]})
    dfs = {"orders": fake_df}

    # First run fails with KeyError, second run succeeds
    run_mock = MagicMock(side_effect=[
        {"ok": False, "error": "KeyError: 'sales'"},
        {"ok": True, "kind": "scalar", "result": 60, "join_warnings": [], "non_additive": []},
    ])

    ai_responses = [
        # AI returns corrected code
        {"code": "result = dfs['orders']['actual_sales'].sum()", "fix_explanation": "Đổi cột 'sales' thành 'actual_sales'"},
        # Storyteller response
        {"answer": "Tổng doanh số là 60.", "chart": None, "follow_up": []},
    ]
    call_ai_mock = MagicMock(side_effect=ai_responses)

    res = run_alpha_cognition(
        question="Tính tổng doanh số",
        dataframes=dfs,
        schema_context="orders(actual_sales: int)",
        initial_code="result = dfs['orders']['sales'].sum()",
        call_ai_fn=call_ai_mock,
        run_pandas_fn=run_mock,
    )

    assert res["ok"] is True
    assert res["scalar"] == 60
    assert run_mock.call_count == 2
    # Ensure no mind shifts were falsely recorded for a syntax/key error
    assert len(res["mind_shifts"]) == 0


def test_alpha_mind_shift_on_true_critic_anomaly():
    """Verify genuine statistical anomaly detected by Critic triggers a Mind Shift."""
    # Data with extreme concentration
    fake_df = pd.DataFrame({
        "customer": ["A", "B", "C", "D", "E"],
        "revenue": [950, 10, 15, 12, 13],  # Customer A is 95% of total
    })
    dfs = {"sales": fake_df}

    run_mock = MagicMock(side_effect=[
        {
            "ok": True,
            "kind": "table",
            "result": {"columns": ["customer", "revenue"], "rows": [["A", 950], ["B", 10], ["C", 15], ["D", 12], ["E", 13]]},
            "join_warnings": [],
            "non_additive": [],
        },
        {
            "ok": True,
            "kind": "table",
            "result": {"columns": ["customer", "orders_count"], "rows": [["A", 100]]},
            "join_warnings": [],
            "non_additive": [],
        },
    ])

    call_ai_mock = MagicMock(side_effect=[
        # Refine drill-down code
        {"code": "result = dfs['sales'][dfs['sales']['customer'] == 'A']", "reason": "Kiểm tra chi tiết khách hàng A"},
        # Storyteller response
        {"answer": "Khách hàng A chiếm 95% tổng doanh số.", "chart": None, "follow_up": []},
    ])

    res = run_alpha_cognition(
        question="Phân tích doanh thu khách hàng",
        dataframes=dfs,
        schema_context="sales(customer: str, revenue: int)",
        initial_code="result = dfs['sales']",
        max_reflexion_turns=1,
        call_ai_fn=call_ai_mock,
        run_pandas_fn=run_mock,
    )

    assert res["ok"] is True
    assert len(res["mind_shifts"]) >= 1
    assert "Đổi hướng" in res["monologue"] or "LỊCH SỬ BẺ LÁI" in res["monologue"]


def test_alpha_vega_chart_support():
    """Verify Vega-Lite charts returned by Storyteller are sanitized and returned."""
    fake_df = pd.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]})
    dfs = {"data": fake_df}

    mock_run_pandas = MagicMock(return_value={
        "ok": True,
        "kind": "table",
        "result": {"columns": ["x", "y"], "rows": [[1, 10], [2, 20], [3, 30]]},
        "join_warnings": [],
        "non_additive": [],
    })

    vega_spec = {
        "mark": "point",
        "encoding": {
            "x": {"field": "x", "type": "quantitative"},
            "y": {"field": "y", "type": "quantitative"},
        },
        "data": {"values": [{"x": 1, "y": 10}, {"x": 2, "y": 20}, {"x": 3, "y": 30}]},
    }

    mock_call_ai = MagicMock(return_value={
        "answer": "Biểu đồ phân tán giữa X và Y.",
        "chart": {
            "type": "vega",
            "title": "Scatter plot X vs Y",
            "vegaLiteSpec": vega_spec,
        },
        "follow_up": [],
    })

    res = run_alpha_cognition(
        question="Vẽ biểu đồ phân tán giữa x và y",
        dataframes=dfs,
        schema_context="data(x: int, y: int)",
        initial_code="result = dfs['data']",
        call_ai_fn=mock_call_ai,
        run_pandas_fn=mock_run_pandas,
    )

    assert res["ok"] is True
    assert res["chart"] is not None
    assert res["chart"]["type"] == "vega"
    assert res["chart"]["vegaLiteSpec"]["$schema"] == "https://vega.github.io/schema/vega-lite/v5.json"


def test_alpha_investigation_integration():
    """Verify bounded root-cause investigation is attached when triggered."""
    fake_df = pd.DataFrame({"month": ["T1", "T2"], "revenue": [100, 70]})
    dfs = {"sales": fake_df}

    mock_run_pandas = MagicMock(return_value={
        "ok": True,
        "kind": "table",
        "result": {"columns": ["month", "revenue"], "rows": [["T1", 100], ["T2", 70]]},
        "join_warnings": [],
        "non_additive": [],
    })

    mock_call_ai = MagicMock(return_value={
        "answer": "Doanh thu tháng 2 giảm từ 100 xuống 70.",
        "chart": None,
        "follow_up": [],
    })

    fake_inv_result = {
        "findings": ["Doanh thu giảm 30 do lượng khách sụt giảm ở nhóm A."],
        "conclusion": "Nguyên nhân chính do nhóm A giảm mua.",
        "rounds": 2,
    }

    with patch("app.agent.investigator.should_investigate", return_value=(True, "Doanh thu giảm sâu")):
        with patch("app.agent.investigator.run_investigation", return_value=fake_inv_result):
            res = run_alpha_cognition(
                question="Tại sao doanh thu tháng 2 giảm mạnh?",
                dataframes=dfs,
                schema_context="sales(month: str, revenue: int)",
                initial_code="result = dfs['sales']",
                call_ai_fn=mock_call_ai,
                run_pandas_fn=mock_run_pandas,
            )

            assert res["ok"] is True
            assert res["investigation"] is not None
            assert res["investigation"]["rounds"] == 2
            assert "nhóm A" in res["investigation"]["findings"][0]

