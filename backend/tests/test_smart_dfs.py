import pandas as pd
import pytest
from app.agent.sandbox import SmartDataframeDict


def test_smart_dataframe_dict_lookup_variations():
    df1 = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    df2 = pd.DataFrame({"x": [10, 20], "y": [30, 40]})
    
    raw = {
        "Báo Cáo Tháng 1.xlsx::Doanh Thu": df1,
        "KhachHang.xlsx::Sheet1": df2,
    }
    
    smart = SmartDataframeDict(raw)
    
    # 1. Full source_id lookup
    assert smart["Báo Cáo Tháng 1.xlsx::Doanh Thu"] is df1
    
    # 2. Sheet name part lookup
    assert smart["Doanh Thu"] is df1
    assert smart["Sheet1"] is df2
    
    # 3. Case-insensitive lookup
    assert smart["doanh thu"] is df1
    assert smart["sheet1"] is df2
    
    # 4. Clean variable identifier
    assert smart["doanh_thu"] is df1


def test_smart_dataframe_single_sheet_fallback():
    df = pd.DataFrame({"sales": [100, 200]})
    smart = SmartDataframeDict({"MyFile.csv::Data": df})
    
    # If only 1 sheet exists, any sheet name gracefully points to it
    assert smart["any_random_name"] is df


def test_smart_dataframe_missing_key_error():
    df1 = pd.DataFrame({"a": [1]})
    df2 = pd.DataFrame({"b": [2]})
    smart = SmartDataframeDict({"f1::s1": df1, "f2::s2": df2})
    
    with pytest.raises(KeyError) as exc_info:
        _ = smart["non_existent"]
    
    assert "không tồn tại" in str(exc_info.value)


def test_run_pandas_max_rows_lifts_the_default_cap():
    """The sheet copilot needs every row; the default 200 stays for everyone else."""
    import pandas as pd
    from app.agent import sandbox

    df = pd.DataFrame({"n": range(500)})
    code = "result = df.assign(double=df['n'] * 2)"

    capped = sandbox.run_pandas(code, {"T": df})
    assert len(capped["result"]["rows"]) == sandbox.MAX_RESULT_ROWS
    assert capped["result"]["truncated"] is True

    full = sandbox.run_pandas(code, {"T": df}, max_rows=10_000)
    assert len(full["result"]["rows"]) == 500
    assert full["result"]["truncated"] is False
    assert full["result"]["total_rows"] == 500
