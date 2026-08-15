"""Turn a *messy* raw spreadsheet grid into a clean, header-aligned DataFrame.

Real business files (sales/marketing/accounting) rarely put a clean header on
row 0: there are company title rows, blank spacer rows, logo/merged banners, a
"Tổng cộng" summary row at the bottom, and horizontally-merged group headers.
Pandas' default `header=0` then produces `Unnamed: N` columns holding a mix of
the real header text and data — which both breaks analysis and (before this)
crashed the parquet save.

There is no universal parser for this — spreadsheet table detection is an
open, heuristic problem. This module implements the pragmatic pipeline that
covers the large majority of business files, and ALWAYS falls back to the
naive `header=0` interpretation when it is not confident, so files that already
imported correctly never regress.

Public: smart_read_grid(raw_df) -> cleaned DataFrame with a real header.
"""

from __future__ import annotations

import re

import pandas as pd

# How many top rows to consider as a possible header (title/blank rows sit above).
_MAX_HEADER_SCAN = 15
# How many rows below a candidate to sample when judging "is there data here".
_DATA_LOOKAHEAD = 12
# Minimum confidence for the detected header to be used instead of header=0.
_MIN_HEADER_SCORE = 0.35

_NUMBER_RE = re.compile(r"^[+-]?[\d.,\s₫đĐ$€%]+$")

# Label of a totals / subtotal / running-total row. Kept broad but anchored to
# the accounting/sales vocabulary so a product name like "Total War" won't trip
# it alone — the row also has to be numeric-heavy with almost no other text.
_TOTAL_MARKER = re.compile(
    r"(?:t[oổ]ng\s*c[oộ]ng|t[oổ]ng\s*s[oố]|t[oổ]ng\b|c[oộ]ng\s*d[oồ]n|lũy\s*kế|luy\s*ke|"
    r"grand\s*total|subtotal|sub-total|\btotal\b|\bsum\b|\btotals\b|\bc[oộ]ng\b)",
    re.IGNORECASE,
)

# Keep unicode word chars (incl. Vietnamese) for measuring how much of a label
# cell the total-marker actually covers.
_WORD_ONLY = re.compile(r"[\W_]+", re.UNICODE)


def _is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    return isinstance(v, str) and v.strip() == ""


def _looks_numeric(v) -> bool:
    if isinstance(v, (int, float)) and not (isinstance(v, float) and pd.isna(v)):
        return True
    if isinstance(v, str):
        s = v.strip()
        return bool(s) and bool(_NUMBER_RE.match(s)) and any(c.isdigit() for c in s)
    return False


def _looks_texty(v) -> bool:
    """A header-ish cell: non-blank text that is not just a number."""
    return not _is_blank(v) and not _looks_numeric(v)


_DATE_RE = re.compile(r"^\d{1,4}[-/]\d{1,2}[-/]\d{1,4}(\s+\d{1,2}:\d{2}(:\d{2})?)?$")


def _looks_datey(v) -> bool:
    return isinstance(v, str) and bool(_DATE_RE.match(v.strip()))


def _looks_structured(v) -> bool:
    """Numeric OR date-like — the two shapes a real DATA cell usually takes,
    as opposed to a wordy header label. `_looks_numeric` alone misses dates
    ("2024-01-01" has dashes, not in the number charset), which previously let
    a date column's row score as deceptively header-like ("text-heavy") and
    get mistaken for a second header row in the 2-level-header heuristic."""
    return _looks_numeric(v) or _looks_datey(v)


def _row_fill(row: list) -> float:
    if not row:
        return 0.0
    return sum(0 if _is_blank(v) else 1 for v in row) / len(row)


def _score_header_row(grid: list[list], r: int) -> float:
    """How much row *r* looks like the real column-header row: it should be
    well-filled with text, and have typed data rows beneath it."""
    row = grid[r]
    non_blank = [v for v in row if not _is_blank(v)]
    if len(non_blank) < 2:
        return 0.0

    fill = _row_fill(row)
    text_ratio = sum(1 for v in non_blank if not _looks_structured(v)) / len(non_blank)

    below = grid[r + 1: r + 1 + _DATA_LOOKAHEAD]
    if not below:
        return 0.0
    data_fill = sum(_row_fill(x) for x in below) / len(below)
    # Headers are wordy (text_ratio), span most columns (fill), and sit atop
    # actual data (data_fill). Multiplying rewards rows that satisfy all three.
    return text_ratio * fill * data_fill


def _is_total_row(row: list) -> bool:
    """A totals/subtotal row: a lone label ("Tổng cộng", "Total") plus numbers,
    with almost no other text. The marker must DOMINATE the label cell so a real
    product like "Total War" (which merely contains the word) is not dropped."""
    non_blank = [v for v in row if not _is_blank(v)]
    if not non_blank:
        return False
    texty = [v for v in non_blank if _looks_texty(v)]
    numeric = [v for v in non_blank if _looks_numeric(v)]
    if not numeric or len(texty) > 2:
        return False
    for v in texty:
        if not isinstance(v, str):
            continue
        m = _TOTAL_MARKER.search(v)
        if not m:
            continue
        cell_chars = _WORD_ONLY.sub("", v)
        marker_chars = _WORD_ONLY.sub("", m.group(0))
        # Marker covers most of the cell → it's a totals label, not data text.
        if cell_chars and len(marker_chars) / len(cell_chars) >= 0.7:
            return True
    return False


def _drop_total_rows(body: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Remove totals/subtotal rows so aggregations aren't double-counted and
    column types aren't polluted. Vectorized prefilter (cheap on huge frames):
    only rows whose text actually contains a marker word are examined closely."""
    if body.empty:
        return body, 0
    marker_mask = pd.Series(False, index=body.index)
    for col in body.columns:
        try:
            marker_mask |= body[col].astype("string").str.contains(_TOTAL_MARKER, na=False)
        except (TypeError, ValueError):
            continue
    if not marker_mask.any():
        return body, 0
    drop_idx = [i for i in body.index[marker_mask] if _is_total_row(list(body.loc[i]))]
    if not drop_idx:
        return body, 0
    return body.drop(index=drop_idx).reset_index(drop=True), len(drop_idx)


def _dedupe_headers(names: list) -> list[str]:
    """Clean, fill, and de-duplicate a header row into safe column names."""
    out: list[str] = []
    seen: dict[str, int] = {}
    last = ""
    for i, v in enumerate(names):
        if _is_blank(v):
            # Horizontally-merged group header: calamine leaves the merged
            # continuation cells blank — carry the last real label rightward.
            name = last or f"Cột {i + 1}"
        else:
            name = str(v).strip()
            last = name
        if name in seen:
            seen[name] += 1
            name = f"{name}.{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out


def _finish_table(filled: pd.DataFrame, header: list[str], body_start: int) -> tuple[pd.DataFrame, int]:
    """Slice a table body out of the (column-trimmed) frame given an already-
    built header and a positional body-start row. Drops blank + totals rows."""
    body = filled.iloc[body_start:].copy()
    body.columns = header
    body = body.dropna(how="all").reset_index(drop=True)
    body, totals = _drop_total_rows(body)
    return body, totals


def _build_table(filled: pd.DataFrame, header_pos: int) -> tuple[pd.DataFrame, int]:
    """Slice a header-aligned table out of the (column-trimmed) frame using a
    positional row index as the header. Drops blank + totals rows. Returns
    (table, n_totals_dropped)."""
    header = _dedupe_headers(filled.iloc[header_pos].tolist())
    return _finish_table(filled, header, header_pos + 1)


def _combine_two_level_header(parent_row: list, child_row: list) -> list[str]:
    """Merge a 2-row header: a merged GROUP row (e.g. "SỐ HÓA ĐƠN" spanning two
    columns, blank in the continuation cell) sitting above a row of actual
    sub-labels (e.g. "Đầu kỳ" / "Cuối kỳ"). Blank parent cells carry the last
    real group label rightward — the same rule pandas/Excel merged cells use.
    Result per column: "Group - Sub", or just whichever half is present."""
    width = max(len(parent_row), len(child_row))
    names = []
    last_parent = ""
    for i in range(width):
        p = parent_row[i] if i < len(parent_row) else None
        c = child_row[i] if i < len(child_row) else None
        if not _is_blank(p):
            last_parent = str(p).strip()
        child_label = "" if _is_blank(c) else str(c).strip()
        if last_parent and child_label:
            names.append(f"{last_parent} - {child_label}")
        elif child_label:
            names.append(child_label)
        elif last_parent:
            names.append(last_parent)
        else:
            names.append(f"Cột {i + 1}")
    return _dedupe_headers(names)


def _is_plausible_second_header(grid: list[list], r: int) -> bool:
    """Row r scores like a header ROW ITSELF (wordy, filled, sits above data) —
    the signal that it's a second header level, not the first data row."""
    if r < 0 or r + 1 >= len(grid):
        return False
    row = grid[r]
    if _row_fill(row) >= 1.0:
        # A genuine sub-label row almost always leaves the columns that AREN'T
        # part of a merged group blank (that's the whole point of a merged
        # group spanning only SOME columns) - a fully-filled row is far more
        # likely an ordinary data row (e.g. a text category column like
        # "Q1"/"Q2" repeating), which would otherwise be mistaken for a
        # second header purely because it also happens to be text-heavy.
        return False
    return _score_header_row(grid, r) >= _MIN_HEADER_SCORE


def smart_read_grid(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Given a sheet read with header=None/dtype=object, locate the real header
    row + table body and return (cleaned_df, meta). Falls back to the naive
    header=0 reading whenever detection is not confident, so this can never make
    an already-correct file worse.

    `meta` describes the detection so the UI can show it and let the user
    override: {header_row, confidence, totals_dropped, low_confidence}. The
    header_row is a 0-based index into the ORIGINAL sheet grid (the same grid the
    frontend renders), so a user override maps straight back onto what they see.

    Only a small window of top rows is materialized for detection; the body is
    sliced vectorized, so this stays cheap on very large sheets."""
    empty_meta = {"header_row": 0, "confidence": 0.0, "totals_dropped": 0, "low_confidence": True}
    if raw_df.empty:
        return pd.DataFrame(), empty_meta

    # Treat empty strings as blanks for all structural decisions.
    filled = raw_df.replace(r"^\s*$", None, regex=True)

    # Column trim (vectorized): drop edge columns that are blank everywhere.
    non_blank_cols = filled.notna().any(axis=0)
    keep = [c for c, ok in zip(filled.columns, non_blank_cols) if ok]
    if not keep:
        return pd.DataFrame(), empty_meta
    filled = filled[keep]

    # Leading fully-blank rows sit above any title/header — skip them.
    non_blank_rows = filled.notna().any(axis=1)
    if not non_blank_rows.any():
        return pd.DataFrame(), empty_meta
    first_real = int(non_blank_rows.values.argmax())

    # Materialize only the detection window (header candidates + a data peek).
    window = filled.iloc[first_real: first_real + _MAX_HEADER_SCAN + _DATA_LOOKAHEAD]
    grid = window.where(window.notna(), None).values.tolist()
    if len(grid) < 2:
        df = pd.DataFrame(columns=_dedupe_headers(grid[0]) if grid else [])
        return df, {**empty_meta, "header_row": first_real}

    best_r, best_score = 0, -1.0
    for r in range(min(_MAX_HEADER_SCAN, len(grid) - 1)):
        score = _score_header_row(grid, r)
        if score > best_score:
            best_r, best_score = r, score

    low_confidence = best_score < _MIN_HEADER_SCORE
    if low_confidence:
        best_r = 0  # behave like the old naive path (header = first real row)

    # 2-level / merged-group header: e.g. "SỐ HÓA ĐƠN" spans two columns with
    # "Đầu kỳ"/"Cuối kỳ" as the real sub-labels one row below. Only even
    # consider this when the chosen header row has NOTABLE blanks (< 85%
    # filled) — a fully-filled single-row header (the common case) never
    # reaches this branch, so ordinary files are completely unaffected.
    two_level = False
    if not low_confidence and _row_fill(grid[best_r]) < 0.85:
        second_r = best_r + 1
        if _is_plausible_second_header(grid, second_r):
            two_level = True

    if two_level:
        header = _combine_two_level_header(grid[best_r], grid[best_r + 1])
        body, totals_dropped = _finish_table(filled, header, first_real + best_r + 2)
    else:
        body, totals_dropped = _build_table(filled, first_real + best_r)

    meta = {
        # Positional row index in the ORIGINAL full sheet grid (what the UI renders).
        "header_row": int(first_real + best_r),
        "confidence": round(float(best_score), 3),
        "totals_dropped": int(totals_dropped),
        "low_confidence": bool(low_confidence),
        "two_level_header": two_level,
    }
    return body, meta


def read_grid_with_header(raw_df: pd.DataFrame, header_row: int) -> pd.DataFrame:
    """Re-parse a raw sheet grid using a USER-chosen header row (0-based index
    into the original grid). Powers the 'sửa dòng tiêu đề' override — no
    detection, just honour the human's pick. Column trim + blank/total-row
    cleanup still apply."""
    if raw_df.empty:
        return pd.DataFrame()
    filled = raw_df.replace(r"^\s*$", None, regex=True)
    non_blank_cols = filled.notna().any(axis=0)
    keep = [c for c, ok in zip(filled.columns, non_blank_cols) if ok]
    if not keep:
        return pd.DataFrame()
    filled = filled[keep].reset_index(drop=True)
    header_row = max(0, min(int(header_row), len(filled) - 1))
    table, _ = _build_table(filled, header_row)
    return table
