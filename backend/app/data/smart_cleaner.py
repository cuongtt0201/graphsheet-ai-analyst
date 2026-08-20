"""Autonomous Silent Data Cleaner & Normalizer.

Executes transparently at file ingestion time (0ms user friction) to sanitize,
repair, and standardize messy real-world business spreadsheets before downstream
profiling and AI analysis.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Currency & Numeric string regex pattern.
# NOTE: `đ`/`Đ` are stripped as the dong sign, but they are also ordinary
# Vietnamese letters — which is one more reason the identifier guard below has
# to run BEFORE anything here decides a column is numeric.
_CURRENCY_SYMBOLS = re.compile(r"[₫đĐ$€¥£\s,]+", re.UNICODE)
_PERCENT_PATTERN = re.compile(r"^[+-]?[\d.,\s]+%$")
_NULL_LITERALS = {"n/a", "na", "null", "none", "-", "--", "nil", "nan", "#n/a", "#ref!", "#value!"}

# A digit string with a leading zero is an IDENTIFIER, not a quantity: order
# codes ("0001"), phone numbers ("0912345678"), tax codes ("0101243150"),
# national IDs, bank accounts, barcodes, postcodes. float() destroys them
# irreversibly — "0001" becomes 1.0 — and this module runs silently on every
# upload, so the damage would never surface as an error.
_LEADING_ZERO = re.compile(r"^0\d")

# Column names that mean "identifier" even when every value is digits and no
# leading zero happens to appear in the sample.
_ID_NAME_HINTS = (
    "mã", "ma_", "_ma", "code", "id", "sđt", "sdt", "phone", "điện thoại",
    "dien thoai", "cccd", "cmnd", "mst", "thuế", "thue", "tax", "stk",
    "tài khoản", "tai khoan", "account", "barcode", "serial", "zip",
    "postcode", "bưu chính", "buu chinh", "invoice", "hóa đơn", "hoa don",
)


def _is_identifier_column(col_name: str, sample: pd.Series) -> bool:
    """True when this column carries codes rather than quantities.

    Two independent signals, either is enough:
      1. any sampled value has a leading zero (structural proof — a real
         quantity is never written "0001");
      2. the column name says so.
    """
    for val in sample:
        if _LEADING_ZERO.match(str(val).strip()):
            return True
    name = str(col_name).strip().lower()
    return any(hint in name for hint in _ID_NAME_HINTS)


def clean_dataframe_silently(df: pd.DataFrame) -> pd.DataFrame:
    """Sanitize and repair a DataFrame transparently in-memory without user interruption.
    
    1. Removes completely empty spacer rows and columns.
    2. Standardizes column names (stripping whitespace, deduplicating names).
    3. Converts formatted numeric/currency/percentage strings to real numeric floats.
    4. Replaces text null markers ('N/A', '-', 'null') with true np.nan.
    5. Strips stray whitespace from categorical text.
    """
    if df is None or len(df) == 0:
        return df

    cleaned = df.copy()

    # 1. Drop completely empty rows and columns
    cleaned = cleaned.dropna(how="all", axis=0).dropna(how="all", axis=1)
    if len(cleaned) == 0 or len(cleaned.columns) == 0:
        return cleaned

    # 2. Standardize column names
    new_cols = []
    seen_cols: dict[str, int] = {}
    for col in cleaned.columns:
        c_str = str(col).strip()
        c_str = re.sub(r"\s+", " ", c_str)  # normalize internal whitespace
        if not c_str or c_str.lower().startswith("unnamed:"):
            c_str = "Cot_Khong_Ten"
        
        # Deduplicate
        if c_str in seen_cols:
            seen_cols[c_str] += 1
            c_str = f"{c_str}_{seen_cols[c_str]}"
        else:
            seen_cols[c_str] = 0
        new_cols.append(c_str)
    
    cleaned.columns = new_cols

    # 3. Clean each column
    for col in cleaned.columns:
        series = cleaned[col]
        
        # Skip if already pure numeric or datetime
        if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
            continue

        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            # Replace literal string nulls
            cleaned[col] = series.apply(lambda x: np.nan if isinstance(x, str) and x.strip().lower() in _NULL_LITERALS else x)
            non_null = cleaned[col].dropna()
            if len(non_null) == 0:
                continue

            # Check if this column is actually a numeric/currency column stored as string
            sample = non_null.head(30).astype(str).str.strip()

            # Identifiers must survive untouched — see _is_identifier_column.
            if _is_identifier_column(col, sample):
                cleaned[col] = cleaned[col].apply(lambda x: x.strip() if isinstance(x, str) else x)
                continue

            # Check percentage
            if sample.str.match(_PERCENT_PATTERN).mean() > 0.7:
                try:
                    cleaned[col] = (
                        cleaned[col].astype(str)
                        .str.replace("%", "", regex=False)
                        .str.replace(",", ".", regex=False)
                        .str.strip()
                        .astype(float) / 100.0
                    )
                    continue
                except Exception:
                    pass

            # Check currency/formatted numbers (e.g. "1.500.000 ₫", "$1,250.50", "45.000")
            num_like_count = 0
            for val_str in sample:
                val_clean = _CURRENCY_SYMBOLS.sub("", val_str).replace(".", "")
                if val_clean.replace("-", "").isdigit():
                    num_like_count += 1

            if num_like_count / len(sample) > 0.75:
                try:
                    # Parse Vietnamese / European format ("1.500.000,50 ₫") or US format ("$1,500,000.50")
                    def _parse_num_str(v):
                        if pd.isna(v) or v is None:
                            return np.nan
                        if isinstance(v, (int, float)):
                            return float(v)
                        s = str(v).strip()
                        s = re.sub(r"[₫đĐ$€¥£\s]+", "", s)
                        if not s:
                            return np.nan
                        # Handle dot vs comma decimal separator
                        if "," in s and "." in s:
                            if s.rfind(",") > s.rfind("."):  # 1.500,50 -> 1500.50
                                s = s.replace(".", "").replace(",", ".")
                            else:  # 1,500.50 -> 1500.50
                                s = s.replace(",", "")
                        elif "," in s and "." not in s:
                            # If only 1 comma with 2 decimals e.g. "12,50" -> 12.50
                            parts = s.split(",")
                            if len(parts) == 2 and len(parts[1]) <= 2:
                                s = s.replace(",", ".")
                            else:
                                s = s.replace(",", "")
                        elif "." in s and "," not in s:
                            parts = s.split(".")
                            if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):  # 1.500.000 or 50.000
                                s = s.replace(".", "")
                        return float(s)

                    cleaned[col] = cleaned[col].apply(_parse_num_str)
                    continue
                except Exception:
                    pass

            # If still text, strip whitespace
            if pd.api.types.is_object_dtype(cleaned[col]):
                cleaned[col] = cleaned[col].apply(lambda x: x.strip() if isinstance(x, str) else x)

    return cleaned
