"""Read an uploaded file and, per sheet, run the deterministic profiler
(clean + type-coerce + profile) so both the join and the plan stages see
accurate types and a rich column description - not just names + sample rows.
Each sheet is an independent table identified by source_id (filename::sheet).
"""

import io

import pandas as pd

from app.data.profiler import clean_and_profile
from app.data.smart_cleaner import clean_dataframe_silently
from app.data.smart_read import smart_read_grid
from app.data.encoding_fix import read_csv_smart, fix_mojibake_df


def read_sheets(filename: str, content: bytes) -> dict[str, tuple[pd.DataFrame, dict]]:
    """Returns {sheet_name: (cleaned_df, profile)}. A CSV has one implicit sheet.

    Each sheet is read raw (header=None) and passed through smart header/table
    detection so messy business files — title rows, blank spacers, merged group
    headers, trailing totals — still yield a correctly-headed table instead of
    `Unnamed: N` columns. Detection falls back to the naive header=0 reading
    when unsure, so clean files never regress. The detection meta is stashed
    on the profile as `detection` for the UI to show/override."""
    if filename.lower().endswith(".csv"):
        raw = {"Sheet1": read_csv_smart(content, header=None, dtype=object)}
    else:
        raw = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, dtype=object, engine="calamine")
    out: dict[str, tuple[pd.DataFrame, dict]] = {}
    for name, raw_df in raw.items():
        raw_df = fix_mojibake_df(raw_df)
        df, meta = smart_read_grid(raw_df)
        df = clean_dataframe_silently(df)
        cleaned, profile = clean_and_profile(df)
        profile["detection"] = meta
        out[name] = (cleaned, profile)
    return out


def make_source_id(filename: str, sheet: str) -> str:
    return f"{filename}::{sheet}"


MAX_GRID_ROWS = 10000  # per-sheet display cap; sheets load one at a time (lazy)
MAX_GRID_COLS = 60


def _grid_from_df(df: pd.DataFrame) -> list[list]:
    """Turn a raw (header=None) DataFrame into a JSON matrix of cells exactly as
    they sit in the sheet - NaN -> "", numbers kept as numbers, everything else
    stringified. No cleaning, no coercion, no header inference: this is the
    faithful 'what the file actually looks like' view for display."""
    df = df.iloc[:MAX_GRID_ROWS, :MAX_GRID_COLS]
    grid: list[list] = []
    for row in df.itertuples(index=False, name=None):
        cells = []
        for v in row:
            if v is None or (isinstance(v, float) and pd.isna(v)):
                cells.append("")
            elif isinstance(v, (int, float, bool)):
                cells.append(v)
            else:
                cells.append(str(v))
        grid.append(cells)
    return grid


def read_raw_grids(filename: str, content: bytes) -> dict[str, dict]:
    """Returns {source_id: {"sheet": name, "grid": [[cell,...],...]}} read with
    header=None so no rows/columns are dropped or transformed - the exact sheet
    layout for the frontend grid. Kept separate from clean_and_profile (whose
    output is for *analysis*, not display)."""
    if filename.lower().endswith(".csv"):
        raw = {"Sheet1": read_csv_smart(content, header=None, dtype=object)}
    else:
        raw = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, dtype=object, engine="calamine")
    return {
        make_source_id(filename, name): {"sheet": name, "grid": _grid_from_df(fix_mojibake_df(df))}
        for name, df in raw.items()
    }


def read_single_sheet_raw(filename: str, content: bytes, sheet: str) -> pd.DataFrame | None:
    """Return ONE sheet's raw grid (header=None, dtype=object) from the original
    file bytes — the full data, not the row-capped display grid. Used when the
    LLM proposes a different header row than the heuristic chose, so a big sheet
    keeps all its rows."""
    if filename.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(content), header=None, dtype=object)
    raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet, header=None, dtype=object, engine="calamine")
    return raw if isinstance(raw, pd.DataFrame) else None


def profile_dataframe(filename: str, sheet: str, df: pd.DataFrame, profile: dict | None = None) -> dict:
    profile = profile or clean_and_profile(df)[1]
    sample = df.head(5).fillna("").astype(str).to_dict(orient="records")
    return {
        "source_id": make_source_id(filename, sheet),
        "filename": filename,
        "sheet": sheet,
        "columns": list(df.columns),
        "dtypes": {c: str(t) for c, t in df.dtypes.items()},
        "sample_rows": sample,
        "row_count": len(df),
        "column_profiles": profile["column_profiles"],
        "flags": profile["flags"],
        "coercions": profile["coercions"],
        "detection": profile.get("detection") or {},
    }


def profile_file(filename: str, content: bytes) -> tuple[list[dict], dict[str, pd.DataFrame]]:
    """Returns (profiles, dataframes_by_source_id) - one profile per sheet in
    the file, and the corresponding cleaned DataFrames keyed by source_id."""
    sheets = read_sheets(filename, content)
    profiles = [profile_dataframe(filename, sheet, df, prof) for sheet, (df, prof) in sheets.items()]
    dataframes = {make_source_id(filename, sheet): df for sheet, (df, _prof) in sheets.items()}
    return profiles, dataframes
