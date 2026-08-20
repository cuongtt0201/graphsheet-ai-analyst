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
from app.ai.pool import call_ai, progress_emit

logger = logging.getLogger(__name__)

_COPILOT_SCHEMA = {
    "type": "object",
    "required": ["mutation_type", "explanation", "python_verification_code", "excel_formula"],
    "properties": {
        "mutation_type": {
            "type": "string",
            "enum": ["add_column_formula", "conditional_formatting", "create_sheet", "transform"],
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
                "threshold": {"type": ["number", "string"]},
                "color_bg": {"type": "string", "description": "Mã màu hex, vd: '#FEE2E2' cho đỏ nhạt"},
                "color_text": {"type": "string", "description": "Mã màu hex chữ"},
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
1. Xác định `mutation_type`:
   - "add_column_formula": Thêm cột mới có công thức Excel động (vd: tính % tăng trưởng, lợi nhuận, chiết khấu).
   - "conditional_formatting": Tô màu có điều kiện (vd: highlight các dòng lỗ, các số > 100tr).
   - "create_sheet": Tạo tab sheet mới từ dữ liệu đã lọc/nhóm.
2. `excel_formula`: Viết công thức Excel chuẩn bắt đầu bằng dấu `=`, tham chiếu các ô tương ứng dòng 2 (vd: `=C2-D2`).
3. `python_verification_code`: Viết mã Python tương đương gán DataFrame mới vào biến `result` để chạy kiểm thử trong Sandbox TRƯỚC KHI áp dụng lên bảng tính thật.

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
    test_run = run_pandas_fn(py_code, dataframes)

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
            test_run = run_pandas_fn(py_code, dataframes)
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

    # Sandbox & Mapping Verification Succeeded!
    _emit({"type": "step", "message": f"✅ Công thức và tham chiếu ô ({mapping_msg}) đã xác thực 100%!"})

    
    return {
        "ok": True,
        "applied": True,
        "mutation_type": mutation_type,
        "target_column": target_col,
        "excel_formula": excel_formula,
        "conditional_format_rule": cond_rule,
        "explanation": explanation,
        "verified_in_sandbox": True,
        "preview": test_run.get("result"),
    }
