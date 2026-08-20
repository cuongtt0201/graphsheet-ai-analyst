import pandas as pd
import numpy as np
from app.agent.critic import critique_dataframe, critique_scalar, critique_execution


def test_critique_dataframe_negative_revenue():
    df = pd.DataFrame({
        "Khách Hàng": ["A", "B", "C"],
        "Doanh Thu": [100.0, -50.0, 300.0],
    })
    verdict = critique_dataframe(df)
    assert verdict.has_anomalies is True
    assert any("giá trị âm" in s for s in verdict.anomaly_signals)
    assert len(verdict.suggested_drill_down) > 0


def test_critique_dataframe_extreme_concentration():
    df = pd.DataFrame({
        "Sản phẩm": ["SP1", "SP2", "SP3", "SP4", "SP5"],
        "Doanh số": [1000.0, 10.0, 5.0, 5.0, 2.0],  # SP1 is ~98% of total
    })
    verdict = critique_dataframe(df)
    assert verdict.has_anomalies is True
    assert any("Độ tập trung cao" in s for s in verdict.statistical_insights)


def test_critique_scalar_nan_inf():
    verdict_nan = critique_scalar(float("nan"), "Tỷ lệ tăng trưởng")
    assert verdict_nan.is_valid is False
    
    verdict_inf = critique_scalar(float("inf"), "Tỷ suất lợi nhuận")
    assert verdict_inf.is_valid is False


def test_critique_execution_clean_table():
    df = pd.DataFrame({
        "Tháng": ["T1", "T2", "T3"],
        "Doanh Thu": [100.0, 120.0, 150.0],
    })
    verdict = critique_execution("table", df)
    assert verdict.is_valid is True
    assert verdict.has_anomalies is False
