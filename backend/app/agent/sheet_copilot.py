"""Sandbox-Verified Spreadsheet Copilot for UniverGrid.

Generates dynamic Excel formulas, conditional formatting rules, and column calculations
for live spreadsheets — with mandatory PRE-FLIGHT SANDBOX VERIFICATION before applying
mutations to the user's live sheet.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd
from app.agent.sandbox import run_pandas
from app.data.profiling import MAX_GRID_ROWS as GRID_DISPLAY_ROWS
from app.ai.pool import call_ai, progress_emit

logger = logging.getLogger(__name__)

_COPILOT_SCHEMA = {
    "type": "object",
    "required": ["mutation_type", "explanation", "python_verification_code", "excel_formula"],
    "properties": {
        "mutation_type": {
            "type": "string",
            "enum": ["add_column_formula", "summary", "conditional_formatting"],
        },
        "target_column": {"type": "string", "description": "Tên cột mới hoặc cột cần áp dụng"},
        "excel_formula": {"type": "string", "description": "Công thức Excel chuẩn cho UniverGrid, vd: '=C2-D2' hoặc '=IF(B2>0, C2/B2, 0)'"},
        "python_verification_code": {
            "type": "string",
            "description": "Mã Python/Pandas thực hiện phép tính tương đương để test trong Sandbox trước",
        },
        "conditional_format_rule": {
            "type": "object",
            "properties": {
                "condition": {"type": "string", "enum": ["greater_than", "less_than", "equal_to", "negative", "outlier"]},
                # One type only: Gemini rejects a JSON-Schema union list, and
                # _numeric() parses "100" and 100 identically anyway.
                "threshold": {"type": "string"},
                "color_bg": {"type": "string", "description": "Mã màu hex, vd: '#FEE2E2' cho đỏ nhạt"},
                "color_text": {"type": "string", "description": "Mã màu hex chữ"},
                "scope": {
                    "type": "string",
                    "enum": ["row", "cell"],
                    "description": "'row' tô cả dòng (vd 'tô đỏ các dòng lỗ'), 'cell' chỉ tô ô của cột đó",
                },
            },
        },
        "explanation": {"type": "string", "description": "Giải thích ngắn gọn cho người dùng"},
    },
}

_COPILOT_PROMPT = """Bạn là Spreadsheet AI Copilot cho bảng tính trực tiếp (UniverGrid).
Người dùng muốn thao tác / chèn công thức / định dạng bảng tính.

DỮ LIỆU CÁC BẢNG:
{schema_context}

YÊU CẦU CỦA NGƯỜI DÙNG:
"{user_prompt}"

YÊU CẦU:
1. Xác định `mutation_type` — câu hỏi quyết định là: **kết quả có một giá trị RIÊNG cho từng dòng không?**

   - "add_column_formula" — CHỈ khi mỗi dòng có một giá trị khác nhau, tính từ chính
     các ô của dòng đó. Vd: `Lợi nhuận = Doanh thu - Chi phí`, `% hoàn thành = TH / KH`.

   - "summary" — khi câu trả lời là MỘT con số, hoặc một bảng tổng hợp/đếm/nhóm.
     Vd: "đếm tổng số chương trình", "doanh thu theo miền", "top 10 cửa hàng".
     Kết quả sẽ mở ra thành một SHEET MỚI, không dán đè lên bảng gốc.

   - "conditional_formatting" — tô màu theo điều kiện.

   TUYỆT ĐỐI CẤM: lấy một con số tổng hợp rồi lặp lại nó xuống mọi dòng của một cột
   mới (kiểu `df.assign(Tổng=len(df))` cho ra 113 ở cả 113 dòng). Đó là rác, không
   phải câu trả lời. Gặp trường hợp này thì dùng "summary".

2. `excel_formula`: Công thức Excel bắt đầu bằng `=`, tham chiếu ô ở dòng 2 (vd `=C2-D2`).
   Với "summary" thì đây là công thức tổng hợp (vd `=COUNTA(A:A)-1`) chỉ để người dùng
   tham khảo, không dán vào bảng.

3. `python_verification_code`: Mã Python gán vào biến `result`.

   RÀNG BUỘC SANDBOX (vi phạm là bị chặn, không chạy):
   - CẤM mọi câu lệnh `import`. `pd` (pandas), `np` (numpy), `df` (bảng đầu tiên)
     và `dfs` (tất cả các bảng) đã có sẵn trong phạm vi — cứ dùng thẳng.
   - Không đọc/ghi file, không gọi mạng.
   - Với "conditional_formatting" thì KHÔNG biến đổi dữ liệu: viết `result = df`.
     Việc tô màu do `conditional_format_rule` mô tả, không phải do mã Python làm.

   Nội dung theo từng loại:
   - "add_column_formula": `result` = TOÀN BỘ DataFrame gốc kèm cột mới
     (vd `result = df.assign(**{{"Lợi nhuận": df["Doanh thu"] - df["Chi phí"]}})`).
   - "summary": `result` = DataFrame tổng hợp GỌN, chỉ gồm dòng/cột cần thiết
     (vd `result = df.groupby("Miền", as_index=False)["Doanh thu"].sum()`).
     Nếu chỉ là một con số thì gói thành bảng 1 dòng có tên cột rõ nghĩa
     (vd `result = pd.DataFrame({{"Tổng số chương trình": [len(df)]}})`).

Trả về DUY NHẤT JSON đúng schema."""


def _emit(event: dict) -> None:
    fn = progress_emit.get()
    if fn is not None:
        fn(event)


def verify_excel_formula_column_mapping(excel_formula: str, dataframes: dict[str, pd.DataFrame], target_sheet: str | None = None) -> tuple[bool, str]:
    """Parse cell references in excel_formula (e.g. A2, C2, $D$2) and verify they map to valid columns."""
    import re
    if not excel_formula or not isinstance(excel_formula, str):
        return True, ""

    # Determine target dataframe columns
    df = None
    if target_sheet and target_sheet in dataframes:
        df = dataframes[target_sheet]
    elif dataframes:
        df = next(iter(dataframes.values()))

    if df is None or len(df.columns) == 0:
        return True, ""

    num_cols = len(df.columns)
    # Match cell references like A2, C2, $D$2, AB100
    cell_refs = re.findall(r"\$?([A-Za-z]+)\$?(\d+)", excel_formula)
    if not cell_refs:
        return True, ""

    referenced_cols = []
    for col_str, _ in cell_refs:
        col_str = col_str.upper()
        # Convert Excel column string (A, B, Z, AA) to 0-based column index
        col_idx = 0
        for char in col_str:
            col_idx = col_idx * 26 + (ord(char) - ord('A') + 1)
        col_idx -= 1

        if col_idx < 0 or col_idx >= num_cols:
            return False, f"Công thức Excel '{excel_formula}' tham chiếu cột {col_str} (chỉ số {col_idx+1}) vượt quá số cột thực tế của bảng ({num_cols} cột)."

        col_name = str(df.columns[col_idx])
        referenced_cols.append(f"{col_str} ({col_name})")

    return True, f"Tham chiếu hợp lệ: {', '.join(set(referenced_cols))}"



# Getting the result back as JSON forced a choice between truncating a real
# sheet and refusing to touch it: a 268k-row file blew past any ceiling worth
# setting. So the frame comes back as parquet instead -- `run_pandas` already
# does this for a dict of DataFrames -- and no row limit is needed at all. The
# dataframe is the source of truth; the grid below is a capped VIEW of it, the
# same 10k cap every other sheet in the app is displayed under.
_FULL_FRAME_KEY = "__copilot_result__"

# Appended to the model's code. Wrapping a DataFrame in a dict is what routes it
# through the parquet path; anything else (a scalar, a stray dict) is left alone
# and serializes as before.
_FULL_FRAME_WRAP = '''
if isinstance(result, pd.DataFrame):
    result = {'__copilot_result__': result}
'''


def _frame_of(run: dict) -> pd.DataFrame | None:
    """The full DataFrame a wrapped run produced, if it produced one."""
    if run.get("kind") != "dataframes":
        return None
    frames = run.get("result") or {}
    frame = frames.get(_FULL_FRAME_KEY)
    return frame if isinstance(frame, pd.DataFrame) else None


def _table_of(df: pd.DataFrame, cap: int) -> dict[str, Any]:
    """A JSON-safe table view of `df`, capped for display."""
    head = df.head(cap).copy()
    for col in head.columns:
        if pd.api.types.is_datetime64_any_dtype(head[col]):
            head[col] = head[col].astype(str)
    rows = head.astype(object).where(pd.notna(head), "").values.tolist()
    return {
        "columns": [str(c) for c in head.columns],
        "rows": rows,
        "total_rows": int(len(df)),
        "truncated": len(df) > cap,
    }


def _broadcast_columns(
    table: dict[str, Any], source_df: pd.DataFrame | None
) -> list[str]:
    """Columns the model added that hold the SAME value on every row.

    This is the failure that made the feature look broken: asked to "đếm tổng số
    chương trình", the model wrote `df.assign(Tổng=len(df))` and 113 appeared in
    all 113 rows. It answers the question and ruins the sheet at the same time.
    A per-row column that never varies is not a per-row column, so the result is
    re-routed to a summary sheet instead of being pasted over the data.
    """
    rows = table.get("rows") or []
    columns = [str(c) for c in (table.get("columns") or [])]
    if source_df is None or len(rows) < 3:
        return []

    existing = {str(c) for c in source_df.columns}
    added = [(i, c) for i, c in enumerate(columns) if c not in existing]
    constant = []
    for idx, name in added:
        values = {str(r[idx]) for r in rows if idx < len(r)}
        if len(values) == 1:
            constant.append(name)
    return constant


def _summary_from_broadcast(table: dict[str, Any], names: list[str]) -> dict[str, Any]:
    """Collapse repeated constants into the one-row table they always were."""
    columns = [str(c) for c in (table.get("columns") or [])]
    rows = table.get("rows") or []
    first = rows[0]
    picked = [(c, first[columns.index(c)]) for c in names if columns.index(c) < len(first)]
    return {
        "columns": [c for c, _ in picked],
        "rows": [[v for _, v in picked]],
        "total_rows": 1,
        "truncated": False,
    }


_DEFAULT_BG = "#FEE2E2"   # đỏ nhạt
_DEFAULT_FG = "#991B1B"


def _numeric(values: list[Any]) -> list[float | None]:
    """Best-effort numbers out of cells that crossed as JSON strings."""
    out: list[float | None] = []
    for v in values:
        if isinstance(v, bool) or v is None or v == "":
            out.append(None)
            continue
        if isinstance(v, (int, float)):
            out.append(float(v))
            continue
        try:
            out.append(float(str(v).replace(",", "").replace(" ", "")))
        except (TypeError, ValueError):
            out.append(None)
    return out


def _matching_rows(column: list[Any], rule: dict[str, Any]) -> list[int]:
    """Row indices (0-based into the data rows) the rule selects."""
    condition = str(rule.get("condition") or "").strip()
    nums = _numeric(column)

    if condition == "equal_to":
        # The only condition that is meaningful on text, so compare as written.
        target = str(rule.get("threshold"))
        return [i for i, v in enumerate(column) if str(v) == target]

    if condition == "negative":
        return [i for i, v in enumerate(nums) if v is not None and v < 0]

    if condition == "outlier":
        present = [v for v in nums if v is not None]
        # Quartiles from a handful of points are noise, not statistics - the
        # same floor the data critic uses before it will call anything extreme.
        if len(present) < 8:
            return []
        ordered = sorted(present)
        q1 = ordered[len(ordered) // 4]
        q3 = ordered[(3 * len(ordered)) // 4]
        iqr = q3 - q1
        if iqr <= 0:
            return []
        low, high = q1 - 3.0 * iqr, q3 + 3.0 * iqr
        return [i for i, v in enumerate(nums) if v is not None and (v < low or v > high)]

    threshold = _numeric([rule.get("threshold")])[0]
    if threshold is None:
        return []
    if condition == "greater_than":
        return [i for i, v in enumerate(nums) if v is not None and v > threshold]
    if condition == "less_than":
        return [i for i, v in enumerate(nums) if v is not None and v < threshold]
    return []


def _compute_highlights(
    table: dict[str, Any], target_column: str, rule: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Turn a declarative colour rule into the cells the grid should tint.

    Evaluated here rather than in the browser: this is where the values are
    still typed, and where "âm" and "ngoại lai" already have definitions.
    Coordinates are grid rows, so index 0 is the header and data starts at 1.
    """
    if not rule or not isinstance(rule, dict):
        return []
    columns = [str(c) for c in (table.get("columns") or [])]
    rows = table.get("rows") or []
    if target_column not in columns or not rows:
        return []

    col_idx = columns.index(target_column)
    values = [r[col_idx] if col_idx < len(r) else None for r in rows]
    hits = _matching_rows(values, rule)
    if not hits:
        return []

    bg = rule.get("color_bg") or _DEFAULT_BG
    fg = rule.get("color_text") or _DEFAULT_FG
    whole_row = str(rule.get("scope") or "row") == "row"

    highlights = []
    for i in hits:
        targets = range(len(columns)) if whole_row else [col_idx]
        for c in targets:
            highlights.append({"row": i + 1, "col": c, "bg": bg, "color": fg})
    return highlights


def _result_to_grid(table: dict[str, Any] | None) -> list[list[Any]] | None:
    """Flatten a sandbox table result into the [[header...], [row...]] grid
    UniverGrid renders. Returns None when the result is not a table."""
    if not isinstance(table, dict):
        return None
    columns = table.get("columns")
    rows = table.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return None
    return [[str(c) for c in columns]] + [list(r) for r in rows]


def apply_sheet_copilot_mutation(
    user_prompt: str,
    dataframes: dict[str, pd.DataFrame],
    schema_context: str,
    sheet_id: str | None = None,
    call_ai_fn: Callable = call_ai,
    run_pandas_fn: Callable = run_pandas,
) -> dict[str, Any]:
    """Plans a sheet mutation, executes pre-flight validation in Sandbox, and returns verified patch payload."""
    _emit({"type": "step", "message": "🧮 AI Copilot đang lên công thức bảng tính..."})

    prompt = _COPILOT_PROMPT.format(schema_context=schema_context, user_prompt=user_prompt)
    try:
        plan = call_ai_fn(prompt, _COPILOT_SCHEMA, tier="strong")
    except Exception as exc:
        logger.warning(f"[sheet_copilot] Planning failed: {exc}")
        return {"ok": False, "error": f"Không thể lập công thức: {exc}"}

    py_code = plan.get("python_verification_code", "")
    target_col = plan.get("target_column", "Calculated_Col")
    excel_formula = plan.get("excel_formula", "")
    explanation = plan.get("explanation", "")
    mutation_type = plan.get("mutation_type", "add_column_formula")
    cond_rule = plan.get("conditional_format_rule")

    # PRE-FLIGHT SANDBOX VERIFICATION
    _emit({"type": "step", "message": "🧪 Đang kiểm thử công thức trong Sandbox an toàn..."})
    test_run = run_pandas_fn(py_code + _FULL_FRAME_WRAP, dataframes)

    if not test_run.get("ok"):
        # Pre-flight failed -> Retry once with error feedback
        error_msg = test_run.get("error", "Lỗi tính toán")
        _emit({"type": "step", "message": f"⚙️ Công thức chưa khớp ({error_msg}) — đang tự động sửa lại..."})
        
        retry_prompt = f"""Mã kiểm thử công thức bị lỗi trong Sandbox:
{error_msg}

MÃ PYTHON BỊ LỖI:
{py_code}

Hãy sửa lại công thức Excel và mã kiểm thử Python để chạy thành công.
{_COPILOT_PROMPT.format(schema_context=schema_context, user_prompt=user_prompt)}"""

        try:
            plan = call_ai_fn(retry_prompt, _COPILOT_SCHEMA, tier="strong")
            py_code = plan.get("python_verification_code", "")
            excel_formula = plan.get("excel_formula", "")
            explanation = plan.get("explanation", "")
            test_run = run_pandas_fn(py_code + _FULL_FRAME_WRAP, dataframes)
        except Exception:
            pass

    if not test_run.get("ok"):
        return {
            "ok": False,
            "applied": False,
            "error": f"Công thức không vượt qua kiểm thử an toàn trong Sandbox: {test_run.get('error')}",
            "explanation": "Đã hủy thao tác để bảo vệ tính toàn vẹn của bảng tính người dùng.",
        }

    # CROSS-CHECK EXCEL FORMULA COLUMN MAPPING
    mapping_ok, mapping_msg = verify_excel_formula_column_mapping(excel_formula, dataframes, sheet_id)
    if not mapping_ok:
        return {
            "ok": False,
            "applied": False,
            "error": mapping_msg,
            "explanation": "Công thức Excel tham chiếu ô vượt quá giới hạn số cột thực tế của bảng.",
        }

    # The frame arrives whole, however many rows it has. What gets capped from
    # here on is only what is DRAWN.
    full_df = _frame_of(test_run)
    table = _table_of(full_df, GRID_DISPLAY_ROWS) if full_df is not None else test_run.get("result")

    # Route the result: a per-row edit lands on the sheet, anything that answers
    # with a single number or a grouped total opens as its own sheet.
    source_df = None
    if sheet_id and sheet_id in dataframes:
        source_df = dataframes[sheet_id]
    elif dataframes:
        source_df = next(iter(dataframes.values()))

    target = "new_sheet" if mutation_type == "summary" else "sheet"
    demoted: list[str] = []
    if target == "sheet" and isinstance(table, dict):
        demoted = _broadcast_columns(table, source_df)
        if demoted:
            table = _summary_from_broadcast(table, demoted)
            target = "new_sheet"
            mutation_type = "summary"
            explanation = (
                f"{explanation} Đây là con số chung cho cả bảng chứ không phải giá trị "
                f"riêng từng dòng, nên mở ra sheet riêng thay vì lặp lại xuống mọi dòng."
            ).strip()

    grid = _result_to_grid(table)
    if grid is None:
        return {
            "ok": False,
            "applied": False,
            "error": "Mã kiểm thử không trả về bảng dữ liệu nên không thể áp lên bảng tính.",
            "explanation": "Đã hủy thao tác để bảo vệ tính toàn vẹn của bảng tính người dùng.",
        }

    # Sandbox & Mapping Verification Succeeded!
    _emit({"type": "step", "message": f"✅ Công thức và tham chiếu ô ({mapping_msg}) đã xác thực 100%!"})

    return {
        # The full frame, for the caller to write into session state. Popped
        # before the response is serialized -- it is a DataFrame, not JSON.
        _FULL_FRAME_KEY: full_df,
        "ok": True,
        "applied": True,
        "mutation_type": mutation_type,
        "target_column": target_col,
        "excel_formula": excel_formula,
        "conditional_format_rule": cond_rule,
        "explanation": explanation,
        "verified_in_sandbox": True,
        "target": target,
        "highlights": _compute_highlights(table, target_col, cond_rule),
        "sheet_title": target_col if target == "new_sheet" else None,
        "grid": grid,
        "total_rows": int(table.get("total_rows") or max(len(grid) - 1, 0)),
        # True when the sheet has more rows than the grid draws. The edit still
        # applied to every one of them; only the view is short.
        "grid_truncated": bool(table.get("truncated")),
        "preview": table,
    }
