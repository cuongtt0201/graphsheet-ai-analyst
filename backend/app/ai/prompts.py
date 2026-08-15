"""Prompt builders + JSON schemas for the two AI touchpoints in the pipeline.

Same shape as model_eval/test_cases.py's JOIN_PLAN_*/DASHBOARD_PLAN_* (that's what
was actually tested against the model pool) but templated with the user's real
uploaded file schemas / cleaned data schema instead of fixed sample data.
"""

def build_join_schema(source_ids: list[str]) -> dict:
    """left_file/right_file are constrained to the real "filename::sheet"
    identifiers of the uploaded sheets. base_table represents the primary
    starting table to merge from.
    """
    return {
        "type": "object",
        "required": ["base_table", "joins"],
        "properties": {
            "base_table": {
                "type": "string",
                "enum": source_ids,
                "description": "Bảng chính được chọn làm gốc để bắt đầu phân tích"
            },
            "joins": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["left_file", "left_column", "right_file", "right_column", "confidence"],
                    "properties": {
                        "left_file": {"type": "string", "enum": source_ids},
                        "left_column": {"type": "string"},
                        "right_file": {"type": "string", "enum": source_ids},
                        "right_column": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                },
            }
        },
    }


def _format_sheet_schema(p: dict) -> str:
    # Show each column with its inferred role so the model can reason about join
    # keys (ids) vs measures vs dimensions, not just names.
    col_lines = []
    for c in p.get("column_profiles", []):
        col_lines.append(f'    - "{c["name"]}" [{c["role"]}, {c["dtype"]}]')
    cols_block = "\n".join(col_lines) or f'    {", ".join(p["columns"])}'
    header = " | ".join(p["columns"])
    rows = "\n".join(" | ".join(str(row.get(c, "")) for c in p["columns"]) for row in p["sample_rows"])
    return (
        f'Bảng "{p["source_id"]}" (file "{p["filename"]}", sheet "{p["sheet"]}", {p["row_count"]} dòng)\n'
        f'  Cột (vai trò suy luận):\n{cols_block}\n'
        f'  Mẫu:\n| {header} |\n{rows}\n'
    )


def build_join_prompt(sheet_profiles: list[dict], user_prompt: str = "") -> str:
    """sheet_profiles from data/profiling.py - one entry per sheet across all
    uploaded files, each carrying column_profiles with inferred roles."""
    tables_text = "\n".join(_format_sheet_schema(p) for p in sheet_profiles)
    user_prompt = user_prompt.strip() or "tạo dashboard tổng quan cho dữ liệu này"
    
    return f"""Bạn là một Data Analyst. Dưới đây là schema và vài dòng mẫu của {len(sheet_profiles)} bảng dữ liệu
(có thể đến từ nhiều sheet trong cùng 1 file, hoặc nhiều file khác nhau) người dùng vừa upload.

{tables_text}

Yêu cầu phân tích của người dùng: "{user_prompt}"

Nhiệm vụ của bạn:
1. Xác định bảng gốc/chính cần thiết nhất để bắt đầu phân tích và gán vào "base_table".
2. Chỉ đề xuất các ghép (join) cho những bảng thực sự cần thiết để phục vụ yêu cầu phân tích của người dùng. Bỏ qua các bảng phụ không chứa thông tin liên quan đến yêu cầu.
3. Nếu yêu cầu của người dùng chỉ cần dùng đúng 1 bảng duy nhất (base_table), hãy để "joins": [] (mảng rỗng).
4. Luôn dùng đúng giá trị trong dấu ngoặc kép "..." ở tên bảng làm left_file/right_file.

Trả lời DUY NHẤT bằng JSON theo đúng cấu trúc sau, không thêm giải thích:
{{
  "base_table": "tên bảng gốc được chọn",
  "joins": [
    {{"left_file": "...", "left_column": "...", "right_file": "...", "right_column": "...", "confidence": "high|medium|low"}}
  ]
}}
"""


