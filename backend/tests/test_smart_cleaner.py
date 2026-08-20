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
