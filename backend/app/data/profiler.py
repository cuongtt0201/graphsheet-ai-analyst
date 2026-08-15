"""The deterministic "Loại 1" engine: clean → coerce types → profile → flag.

This runs in BE before any LLM sees the data. Its job is to make the data
*accurate and well-described* so the LLM's judgment (which column is revenue,
what KPIs to build) has a correct foundation. The LLM never fixes types or
computes numbers - it only reads the profile this module produces.

Public: clean_and_profile(df) -> (cleaned_df, profile_dict)
"""

import re

import pandas as pd

COERCE_THRESHOLD = 0.80          # convert a column if >= this share of non-nulls parse
MOSTLY_EMPTY_THRESHOLD = 0.95    # drop / flag columns at least this fraction null
CATEGORY_MAX_DISTINCT = 50       # text with few distinct values => category (dimension)
ID_DISTINCT_RATIO = 0.9          # distinct/row_count above this => looks like an id

# A numeric column is only called an id when its NAME says so - otherwise a
# high-cardinality integer measure (e.g. revenue in whole VND, all distinct in
# a small sample) gets mis-labelled "id" and excluded from KPIs. Text columns
# can still be inferred as ids purely by cardinality.
_ID_NAME = re.compile(r"(^|[_\s])(id|ma|mã|code|key|sku|so_hd|bill|order|hoa_?don|stt)(s?)([_\s]|$)", re.IGNORECASE)

_CURRENCY_CHARS = "₫đĐ$€% "  # currency symbols + non-breaking space


# ── type coercion ────────────────────────────────────────────

def _parse_number(value) -> float | None:
    """Parse one messy string into a float, handling Vietnamese and US number
    formats. Returns None if it isn't a number."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    for ch in _CURRENCY_CHARS:
        s = s.replace(ch, "")
    s = s.replace(" ", "").strip()
    if not s or not re.search(r"\d", s):
        return None
    # Only digits, separators, and an optional leading sign are allowed.
    if not re.fullmatch(r"[+-]?[\d.,]+", s):
        return None

    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        # The right-most separator is the decimal point; the other is thousands.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma:
        # Only commas: decimal if exactly one comma with 1-2 trailing digits,
        # else thousands (e.g. "1,200,000").
        if s.count(",") == 1 and len(s.split(",")[1]) in (1, 2):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_dot:
        # Only dots: thousands if multiple dots, or one dot with exactly 3
        # trailing digits (e.g. "1.200"); otherwise a real decimal ("1.5").
        parts = s.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _try_numeric(series: pd.Series) -> tuple[pd.Series | None, float]:
    nonnull = series.dropna()
    if nonnull.empty:
        return None, 0.0
    parsed = nonnull.map(_parse_number)
    ratio = parsed.notna().mean()
    if ratio < COERCE_THRESHOLD:
        return None, ratio
    out = series.map(lambda v: _parse_number(v) if pd.notna(v) else None)
    return pd.to_numeric(out, errors="coerce"), ratio


# Common date formats tried (in order) on a sample before parsing the whole
# column, so a uniform column is parsed vectorized with ONE explicit format —
# fast and silent — instead of pandas falling back to per-element dateutil
# (slow on big columns + the "Could not infer format" UserWarning).
_DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y",
    "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M:%S",
]


def _to_datetime_quiet(series: pd.Series, dayfirst: bool = True) -> pd.Series:
    """Parse without the 'could not infer format' warning: use format='mixed'
    (explicit, so no warning) as the general fallback."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pd.to_datetime(series, errors="coerce", dayfirst=dayfirst, format="mixed")


def _try_datetime(series: pd.Series) -> tuple[pd.Series | None, float]:
    nonnull = series.dropna()
    if nonnull.empty:
        return None, 0.0

    sample = nonnull.astype(str).head(200)
    # Fast path: if one common format parses the sample well, apply it to the
    # whole column vectorized (no per-element dateutil, no warning).
    for fmt in _DATE_FORMATS:
        parsed_sample = pd.to_datetime(sample, format=fmt, errors="coerce")
        if parsed_sample.notna().mean() >= COERCE_THRESHOLD:
            full = pd.to_datetime(series, format=fmt, errors="coerce")
            return full, float(full.notna().mean())

    # Mixed/irregular formats: fall back to quiet mixed parsing.
    parsed = _to_datetime_quiet(nonnull)
    ratio = parsed.notna().mean()
    if ratio < COERCE_THRESHOLD:
        return None, float(ratio)
    return _to_datetime_quiet(series), float(ratio)


def _looks_like_date(col_name: str, series: pd.Series) -> bool:
    if "date" in col_name.lower() or "ngay" in col_name.lower() or "ngày" in col_name.lower():
        return True
    sample = series.dropna().astype(str).head(20)
    hits = sample.str.contains(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}").mean() if len(sample) else 0
    return hits > 0.5


def _coerce_types(df: pd.DataFrame) -> list[dict]:
    """Mutates df in place, converting text columns that are really numbers or
    dates. Returns a log of what changed."""
    coercions = []
    for col in df.select_dtypes(include=["object", "string"]).columns:
        num, num_ratio = _try_numeric(df[col])
        if num is not None:
            df[col] = num
            coercions.append({"column": col, "to": "number", "parsed_ratio": round(num_ratio, 2)})
            continue
        if _looks_like_date(col, df[col]):
            dt, dt_ratio = _try_datetime(df[col])
            if dt is not None:
                df[col] = dt
                coercions.append({"column": col, "to": "datetime", "parsed_ratio": round(dt_ratio, 2)})
    return coercions


# ── profiling ────────────────────────────────────────────────

def _infer_role(name: str, series: pd.Series, null_pct: float, distinct: int, row_count: int) -> str:
    if null_pct >= MOSTLY_EMPTY_THRESHOLD:
        return "mostly_empty"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    ratio = distinct / row_count if row_count else 0
    if pd.api.types.is_numeric_dtype(series):
        # Numeric -> id ONLY if the name says so (and it's a high-distinct
        # integer). Otherwise it's a measure - revenue must never be demoted.
        nonnull = series.dropna()
        looks_int = bool((nonnull % 1 == 0).all()) if nonnull.size else False
        if _ID_NAME.search(name) and looks_int and ratio >= ID_DISTINCT_RATIO:
            return "id"
        return "measure"
    if ratio >= ID_DISTINCT_RATIO or _ID_NAME.search(name):
        return "id"
    if distinct <= CATEGORY_MAX_DISTINCT:
        return "category"
    return "text"


def _profile_columns(df: pd.DataFrame) -> list[dict]:
    row_count = len(df)
    profiles = []
    for col in df.columns:
        s = df[col]
        null_pct = float(s.isna().mean())
        distinct = int(s.nunique(dropna=True))
        role = _infer_role(col, s, null_pct, distinct, row_count)
        entry = {
            "name": col,
            "dtype": str(s.dtype),
            "role": role,
            "null_pct": round(null_pct, 2),
            "distinct": distinct,
        }
        if pd.api.types.is_numeric_dtype(s):
            nonnull = s.dropna()
            entry["sample"] = [round(float(v), 2) for v in nonnull.head(3)]
            if not nonnull.empty:
                entry["min"] = round(float(nonnull.min()), 2)
                entry["max"] = round(float(nonnull.max()), 2)
                entry["mean"] = round(float(nonnull.mean()), 2)
                entry["sum"] = round(float(nonnull.sum()), 2)
                entry["nonzero_ratio"] = round(float((s != 0).mean()), 2)
        else:
            entry["sample"] = [str(v) for v in s.dropna().astype(str).head(3)]
        profiles.append(entry)
    return profiles


def _flags(column_profiles: list[dict]) -> list[str]:
    flags = []
    for c in column_profiles:
        if c["role"] == "mostly_empty":
            flags.append(f'Cột "{c["name"]}" gần như rỗng ({int(c["null_pct"] * 100)}% trống).')
        if c["role"] == "id" and c["dtype"].startswith(("int", "float")):
            flags.append(f'Cột "{c["name"]}" trông giống mã định danh (ID) - không nên tính tổng/trung bình.')
        if c.get("nonzero_ratio") is not None and c["nonzero_ratio"] < 0.1 and c["role"] == "measure":
            flags.append(f'Cột số "{c["name"]}" gần như toàn số 0 - có thể là cột lỗi/không dùng được.')
    return flags


# ── public entry point ───────────────────────────────────────

def clean_and_profile(df: pd.DataFrame, drop_dups: bool = False) -> tuple[pd.DataFrame, dict]:
    df = df.copy()

    # 1. structural clean
    df.columns = df.columns.astype(str).str.strip()
    for col in df.select_dtypes(include=["object", "string"]).columns:
        # Strip whitespace, and treat now-empty strings as missing so blank
        # cells (common in "Tổng cộng" total rows, sparse accounting sheets)
        # don't block numeric/date coercion or inflate a column's fill.
        df[col] = df[col].apply(
            lambda v: (lambda s: s if s else None)(v.strip()) if isinstance(v, str) else v
        )
    dropped = [c for c in df.columns if df[c].isna().mean() > MOSTLY_EMPTY_THRESHOLD]
    df = df.drop(columns=dropped)

    # Exact-duplicate rows are COUNTED, not deleted. Whether they are a defect
    # depends on the table's grain, which nothing at this layer can know: on a
    # line-item sales table two identical rows are legitimate (a customer buys
    # the same product twice at the same price on one invoice), and silently
    # dropping one understates revenue with nobody ever noticing. Deleting is
    # the irreversible choice, so it is deferred to whoever knows the grain
    # (data/semantics.py -> apply_grain_dedup) and reported to the user either
    # way. Callers that genuinely want the old behaviour pass drop_dups=True.
    dup_count = int(df.duplicated().sum())
    if drop_dups and dup_count:
        df = df.drop_duplicates().reset_index(drop=True)
        dup_removed = dup_count
    else:
        df = df.reset_index(drop=True)
        dup_removed = 0

    # 2. type coercion (auto-fix + log)
    coercions = _coerce_types(df)

    # 2b. Normalize columns that stayed `object` after coercion. Real-world
    # sales/accounting sheets routinely mix types in one column (a stray text
    # header row above the data, a "Tổng cộng" summary row, "N/A" among
    # numbers), leaving object columns holding both ints and strings. pyarrow
    # then refuses to serialize them ("Expected bytes, got a 'int' object")
    # and the whole upload fails. Cast such columns to a uniform nullable
    # string so parquet round-trips and the file is never rejected outright.
    for col in df.select_dtypes(include=["object", "string"]).columns:
        try:
            df[col] = df[col].astype("string")
        except (TypeError, ValueError):
            df[col] = df[col].apply(lambda v: v if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)).astype("string")

    # 3. profile + 4. flags
    column_profiles = _profile_columns(df)
    flags = _flags(column_profiles)
    if dup_count and not dup_removed:
        pct = dup_count / len(df) * 100 if len(df) else 0
        flags.append(
            f"Có {dup_count:,} dòng trùng hoàn toàn ({pct:.1f}%). Đã GIỮ NGUYÊN — "
            "với dữ liệu chi tiết giao dịch, dòng trùng có thể là hợp lệ."
        )

    profile = {
        "row_count": len(df),
        "column_profiles": column_profiles,
        "coercions": coercions,
        "dropped_columns": dropped,
        "duplicate_rows_removed": dup_removed,
        "duplicate_rows_found": dup_count,
        "flags": flags,
    }
    return df, profile
