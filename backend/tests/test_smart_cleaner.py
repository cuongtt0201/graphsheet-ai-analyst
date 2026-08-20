"""Unit tests for Autonomous Silent Data Cleaner."""

import numpy as np
import pandas as pd
from app.data.smart_cleaner import clean_dataframe_silently


def test_clean_dataframe_removes_empty_rows_and_cols():
    df = pd.DataFrame({
        "A": ["x", np.nan, "y", np.nan],
        "EmptyCol": [np.nan, np.nan, np.nan, np.nan],
        "B": [1, np.nan, 2, np.nan],
    })
    cleaned = clean_dataframe_silently(df)
    assert "EmptyCol" not in cleaned.columns
    assert len(cleaned) == 2


def test_clean_dataframe_auto_casts_currency_strings():
    df = pd.DataFrame({
        "Customer": ["CTy A", "CTy B", "CTy C"],
        "Revenue": [" 1.500.000 ₫", " 2.300.000 đ ", "450.000"],
    })
    cleaned = clean_dataframe_silently(df)
    assert pd.api.types.is_numeric_dtype(cleaned["Revenue"])
    assert cleaned["Revenue"].iloc[0] == 1500000.0
    assert cleaned["Revenue"].iloc[1] == 2300000.0
    assert cleaned["Revenue"].iloc[2] == 450000.0


def test_clean_dataframe_auto_casts_percentages():
    df = pd.DataFrame({
        "Product": ["SP1", "SP2", "SP3"],
        "Margin": ["15.5%", "20%", " 8.2 % "],
    })
    cleaned = clean_dataframe_silently(df)
    assert pd.api.types.is_numeric_dtype(cleaned["Margin"])
    assert np.isclose(cleaned["Margin"].iloc[0], 0.155)
    assert np.isclose(cleaned["Margin"].iloc[1], 0.20)


def test_clean_dataframe_normalizes_null_literals():
    df = pd.DataFrame({
        "Category": ["Electronics", "N/A", "Clothing", "null", "-"],
        "Sales": [100, 200, 300, 400, 500],
    })
    cleaned = clean_dataframe_silently(df)
    assert pd.isna(cleaned["Category"].iloc[1])
    assert pd.isna(cleaned["Category"].iloc[3])
    assert pd.isna(cleaned["Category"].iloc[4])


def test_identifier_columns_survive_numeric_coercion():
    """Codes must never be coerced to float: "0001" -> 1.0 is irreversible, and
    this cleaner runs silently on every upload, so nothing would surface it."""
    df = pd.DataFrame({
        "Mã đơn": ["0001", "0002", "0003", "0004", "0005"],
        "SĐT": ["0912345678", "0987654321", "0901112223", "0938887776", "0977665544"],
        "MST": ["0101243150", "0316871226", "0304998888", "0312345678", "0398765432"],
        "Doanh thu": ["1.500.000 ₫", "2.300.000 ₫", "900.000 ₫", "1.200.000 ₫", "3.100.000 ₫"],
    })
    out = clean_dataframe_silently(df)

    # Leading zeros intact, values unchanged.
    assert list(out["Mã đơn"]) == ["0001", "0002", "0003", "0004", "0005"]
    assert list(out["SĐT"])[0] == "0912345678"
    assert list(out["MST"])[0] == "0101243150"

    # The genuine money column still converts.
    assert pd.api.types.is_numeric_dtype(out["Doanh thu"])
    assert float(out["Doanh thu"].iloc[0]) == 1_500_000.0


def test_identifier_detected_by_column_name_without_leading_zero():
    """Name alone is enough when no sampled value happens to lead with a zero."""
    df = pd.DataFrame({
        "Mã KH": ["1001", "1002", "1003", "1004", "1005"],
        "Số lượng": ["12", "45", "7", "103", "88"],
    })
    out = clean_dataframe_silently(df)
    assert list(out["Mã KH"]) == ["1001", "1002", "1003", "1004", "1005"]
    assert pd.api.types.is_numeric_dtype(out["Số lượng"])


def test_identifier_hints_match_whole_words_not_substrings():
    """"Covid" contains "id" and "Market" contains "ma" — neither is an identifier.

    Substring matching froze these numeric columns as text on every upload,
    which never surfaced as an error because the cleaner runs silently.
    """
    df = pd.DataFrame({
        "Covid cases": ["1.200", "3.400", "5.600", "7.800", "9.100"],
        "Market share": ["12", "45", "7", "103", "88"],
        "Video views": ["1.000", "2.000", "3.000", "4.000", "5.000"],
    })
    out = clean_dataframe_silently(df)
    for col in df.columns:
        assert pd.api.types.is_numeric_dtype(out[col]), f"{col} bị nhận nhầm là cột định danh"
    assert float(out["Covid cases"].iloc[0]) == 1200.0


def test_identifier_hints_still_match_across_separators():
    """Real identifier columns keep working whatever separator joins the tokens."""
    df = pd.DataFrame({
        "Ma_KH": ["1001", "1002", "1003", "1004", "1005"],
        "Số hoá đơn": ["7001", "7002", "7003", "7004", "7005"],
        "customer-id": ["5001", "5002", "5003", "5004", "5005"],
        "Doanh thu": ["1.500.000", "2.400.000", "900.000", "3.100.000", "750.000"],
    })
    out = clean_dataframe_silently(df)
    assert list(out["Ma_KH"]) == ["1001", "1002", "1003", "1004", "1005"]
    assert list(out["Số hoá đơn"]) == ["7001", "7002", "7003", "7004", "7005"]
    assert list(out["customer-id"]) == ["5001", "5002", "5003", "5004", "5005"]
    assert pd.api.types.is_numeric_dtype(out["Doanh thu"])
