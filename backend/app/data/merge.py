"""Deterministic merge/clean, executed only after the user has confirmed the
AI's proposed join plan (or edited it). No AI involvement here - pandas only.
"""

import pandas as pd


def _canonical_key(s: pd.Series) -> pd.Series:
    """Canonical string form of a join key, so the same ID compares equal no
    matter which dtype Excel produced for it.

    Real workbooks store the same product/customer code as a number in one
    sheet ("Mã hàng" all digits -> float64) and as text in another (one stray
    "SP-001", or the column formatted as text). pandas then refuses the merge
    outright ("trying to merge on float64 and string columns").

    The trap when normalizing: float64 renders 12345 as "12345.0", which would
    never match the text "12345" - so whole numbers must go through Int64
    first. Blank/NaN keys are kept as NA rather than "", otherwise every
    key-less row on the left would join every key-less row on the right.
    """
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        num = pd.to_numeric(s, errors="coerce")
        is_whole = bool(num.dropna().mod(1).eq(0).all()) if num.notna().any() else True
        out = num.astype("Int64").astype("string") if is_whole else num.astype("string")
    else:
        out = s.astype("string").str.strip()
        # Text that is really a whole number ("12345", or "12345.0" left over
        # from an earlier float round-trip) collapses to the same Int64 form.
        as_num = pd.to_numeric(out, errors="coerce")
        whole_mask = as_num.notna() & as_num.mod(1).eq(0)
        if whole_mask.any():
            out = out.mask(whole_mask, as_num.astype("Int64").astype("string"))

    return out.replace({"": pd.NA, "nan": pd.NA, "NaN": pd.NA, "None": pd.NA, "<NA>": pd.NA})


def apply_join_plan(dataframes: dict[str, pd.DataFrame], joins: list[dict], base_table: str = None,
                    semantics: dict | None = None, report: dict | None = None) -> pd.DataFrame:
    """dataframes: {filename: DataFrame}. joins: confirmed [{left_file, left_column,
    right_file, right_column}, ...] (order matters: applied left-to-right).
    base_table: the primary table to start merging from.

    semantics/report are optional: when a dict is passed as `report` it is filled
    with {"warnings": [...], "non_additive": [...]} describing the ways this
    particular merge distorts totals (see data/join_guard.py). Passing neither
    leaves the merge behaviour completely unchanged.
    """
    if base_table is None:
        if joins:
            base_table = joins[0]["left_file"]
        else:
            if len(dataframes) == 1:
                base_table = next(iter(dataframes.keys()))
            else:
                raise ValueError("Multiple files uploaded but no join plan or base table was confirmed.")

    if not joins:
        # single file, or user confirmed "no join needed"
        merged = dataframes[base_table].copy()
    else:
        for join in joins:
            for key in (join["left_file"], join["right_file"]):
                if key not in dataframes:
                    raise ValueError(f"Join references unknown table '{key}' - it wasn't in the uploaded sheets.")

        merged = dataframes[base_table].copy()
        merged_files = {base_table}

        for join in joins:
            left_file, right_file = join["left_file"], join["right_file"]
            if left_file not in merged_files and right_file not in merged_files:
                raise ValueError(f"Join {join} doesn't connect to any already-merged file.")

            base_is_left = left_file in merged_files
            other_file = right_file if base_is_left else left_file
            other_col = join["right_column"] if base_is_left else join["left_column"]
            base_col = join["left_column"] if base_is_left else join["right_column"]

            other_df = dataframes[other_file].copy()
            suffix = "_" + other_file.replace("::", "_").replace(".xlsx", "").replace(".csv", "").replace(".xls", "")
            
            # Prevent Pandas duplicate suffix error by renaming clashing columns in other_df first
            clashing_cols = [c for c in other_df.columns if c in merged.columns and c != other_col]
            if clashing_cols:
                other_df = other_df.rename(columns={c: f"{c}{suffix}" for c in clashing_cols})

            if base_col not in merged.columns:
                raise ValueError(f"Cột ghép '{base_col}' không có trong bảng '{base_table}'.")
            if other_col not in other_df.columns:
                raise ValueError(f"Cột ghép '{other_col}' không có trong bảng '{other_file}'.")

            # Only touch the keys when pandas would actually refuse the merge:
            # a working same-dtype join must never be altered by this path.
            if merged[base_col].dtype != other_df[other_col].dtype:
                merged[base_col] = _canonical_key(merged[base_col])
                other_df[other_col] = _canonical_key(other_df[other_col])

            # A blank key is not an identity. pandas merge happily matches NaN
            # to NaN, so every key-less row in the lookup table would attach
            # itself to every key-less row on the left - inventing relationships
            # and multiplying rows. Blank-key rows can never be match
            # candidates; left rows with a blank key are still kept by the left
            # join, they just find nothing.
            other_df = other_df[other_df[other_col].notna()]

            # Inspect the real frames at the moment of truth — after key
            # canonicalization and renaming, so the reported column names are
            # the ones that will actually exist in the merged result.
            if report is not None:
                from app.data.join_guard import inspect_join

                grain = ((semantics or {}).get(other_file) or {}).get("grain_type")
                found = inspect_join(merged, other_df, base_col, other_col, other_file, grain)
                report.setdefault("warnings", []).extend(found["warnings"])
                report.setdefault("non_additive", []).extend(found["non_additive"])

            merged = merged.merge(
                other_df, how="left", left_on=base_col, right_on=other_col,
                suffixes=("", "_temp_dup"),
            )
            merged_files.add(other_file)

    _clean_in_place(merged)
    return merged


def _clean_in_place(df: pd.DataFrame) -> None:
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].fillna(0)
        else:
            df[col] = df[col].fillna("")

    for col in df.columns:
        if df[col].dtype == object:
            continue
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            _add_date_parts(df, col)
            break

    for col in list(df.columns):
        if df[col].dtype == object and "date" in col.lower():
            parsed = pd.to_datetime(df[col], errors="coerce")
            if parsed.notna().mean() > 0.8:
                df[col] = parsed
                _add_date_parts(df, col)
                break


def _add_date_parts(df: pd.DataFrame, date_col: str) -> None:
    df["month"] = df[date_col].dt.strftime("%Y-%m")
    df["quarter"] = "Q" + df[date_col].dt.quarter.astype(str) + "-" + df[date_col].dt.year.astype(str)
    df["year"] = df[date_col].dt.year
