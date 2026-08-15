"""LLM fallback for the *uncertain* sheets only.

The heuristic in smart_read handles the common cases cheaply. When its
confidence is low (weird layouts: multi-row banners, sparse forms, sideways
labels) we spend one LLM call to have the model point at the real header row.
This is the hybrid pipeline's key idea — heuristics first, LLM only on the hard
minority — which is what pushes accuracy up without paying a call per upload.

Contract: never raises. Returns a 0-based header row index into the provided
grid, or None to keep the heuristic's answer.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SCHEMA = {
    "type": "object",
    "required": ["header_row"],
    "properties": {
        "header_row": {"type": "integer"},
        "reason": {"type": "string"},
    },
}

_PROMPT = """You are cleaning a messy spreadsheet. Below are the first rows of a sheet,
shown as "row_index: cell1 | cell2 | ...". Some top rows may be a company title,
a report caption, blank spacers, or merged banners — NOT the column headers.

Identify the single row that holds the real COLUMN HEADERS (the row whose cells
are the field names for the data table beneath it).

Rows:
{rows}

Return JSON: {{"header_row": <0-based index of the header row>, "reason": "<short>"}}.
If the very first row is already the header, return 0."""


# How many rows the model is shown. An answer outside this range is rejected:
# it would point at a row the model never actually saw.
_MAX_ROWS = 20


def _format_rows(grid: list[list], max_rows: int = _MAX_ROWS, max_cols: int = 12) -> str:
    lines = []
    for i, row in enumerate(grid[:max_rows]):
        cells = ["" if c is None else str(c) for c in row[:max_cols]]
        lines.append(f"{i}: " + " | ".join(cells))
    return "\n".join(lines)


def llm_detect_header(grid: list[list]) -> int | None:
    """Ask the model which row is the header. Returns a valid 0-based index into
    `grid`, or None on any failure / out-of-range answer (keep the heuristic)."""
    if not grid or len(grid) < 2:
        return None
    try:
        from app.ai.pool import call_ai, AllModelsFailedError
    except Exception:
        return None
    try:
        res = call_ai(_PROMPT.format(rows=_format_rows(grid)), _SCHEMA, tier="fast")
    except Exception as exc:  # AllModelsFailedError or anything else
        logger.warning(f"[llm_header] validation skipped: {exc}")
        return None
    return _valid_index(res.get("header_row"), grid)


def _valid_index(idx, grid: list[list]) -> int | None:
    """A row index the model returned is only usable if it points inside the
    rows it was actually shown."""
    if not isinstance(idx, int):
        return None
    if idx < 0 or idx >= min(len(grid), _MAX_ROWS):
        return None
    return idx


# One call carries this many sheets. Each sheet contributes ~20 short rows, so
# a batch stays small; the cap exists because batching concentrates failure —
# beyond this, one malformed answer would cost too many sheets at once.
BATCH_MAX_SHEETS = 8


_BATCH_CONTEXT = """Bạn đang làm sạch nhiều sheet bảng tính cùng lúc. Với MỖI sheet dưới đây,
các dòng được hiển thị dạng "chỉ_số: ô1 | ô2 | ...". Một số dòng đầu có thể là tên công ty,
tiêu đề báo cáo, dòng trống, hoặc ô gộp — KHÔNG phải tiêu đề cột.

Nhiệm vụ cho mỗi sheet: chỉ ra DUY NHẤT dòng chứa TIÊU ĐỀ CỘT thật (dòng mà các ô là tên
trường của bảng dữ liệu bên dưới nó). Nếu dòng đầu tiên đã là tiêu đề, trả về 0.

Các sheet độc lập với nhau — đừng để cấu trúc của sheet này ảnh hưởng phán đoán sheet khác."""


def llm_detect_headers(grids: dict[str, list[list]]) -> dict[str, int | None]:
    """Header row for many sheets in ONE call: {source_id: row index or None}.

    Each sheet only needs a one-line answer, so N separate calls spent N
    round-trips (and N rate-limit slots) to move very little information.

    Batching concentrates failure, though — a single malformed answer would
    otherwise cost every sheet — so a failed batch falls back to the per-sheet
    path rather than leaving all sheets unchecked.
    """
    usable = {sid: g for sid, g in (grids or {}).items() if g and len(g) >= 2}
    if not usable:
        return {}

    out: dict[str, int | None] = {}
    items = list(usable.items())
    for start in range(0, len(items), BATCH_MAX_SHEETS):
        chunk = items[start:start + BATCH_MAX_SHEETS]
        out.update(_detect_chunk(chunk))
    return out


def _detect_chunk(chunk: list[tuple[str, list[list]]]) -> dict[str, int | None]:
    try:
        from app.ai.harness import batch_tasks
    except Exception:  # noqa: BLE001
        return {}

    # Synthetic task keys: a source_id contains "::", dots and Vietnamese text,
    # none of which are safe as JSON-schema property names.
    keys = {f"sheet_{i}": (sid, grid) for i, (sid, grid) in enumerate(chunk)}
    tasks = {
        k: f'Sheet "{sid}":\n{_format_rows(grid)}'
        for k, (sid, grid) in keys.items()
    }
    schemas = {k: _SCHEMA for k in keys}

    try:
        results = batch_tasks(_BATCH_CONTEXT, tasks, tier="fast", schemas=schemas)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[llm_header] batch failed, falling back per-sheet: {exc}")
        return _detect_chunk_individually(chunk)

    out: dict[str, int | None] = {}
    for k, (sid, grid) in keys.items():
        res = results.get(k)
        out[sid] = _valid_index(res.get("header_row"), grid) if isinstance(res, dict) else None

    # A batch that came back empty-handed for everything is indistinguishable
    # from no answer at all; retry the sheets individually rather than silently
    # skipping the check for all of them.
    if all(v is None for v in out.values()):
        return _detect_chunk_individually(chunk)
    return out


def _detect_chunk_individually(chunk: list[tuple[str, list[list]]]) -> dict[str, int | None]:
    from concurrent.futures import ThreadPoolExecutor

    if not chunk:
        return {}
    with ThreadPoolExecutor(max_workers=min(4, len(chunk))) as pool:
        answers = list(pool.map(lambda item: llm_detect_header(item[1]), chunk))
    return {sid: ans for (sid, _), ans in zip(chunk, answers)}
