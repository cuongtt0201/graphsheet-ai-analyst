"""Representative prompts + JSON schemas for the pipeline's two real AI touchpoints:
join-plan (merging uploaded files) and dashboard-plan (choosing KPIs/charts).
"""

JOIN_PLAN_PROMPT = """Bạn là một Data Analyst. Dưới đây là schema và 5 dòng mẫu của 3 file Excel người dùng vừa upload.
Hãy đề xuất cách join các file này lại với nhau.

File 1: "don_hang.xlsx"
Cột: order_id, customer_code, order_date, store_name, total_amount
Mẫu:
| order_id | customer_code | order_date | store_name | total_amount |
| DH001 | KH100 | 2025-01-05 | Cua hang Q1 | 1200000 |
| DH002 | KH101 | 2025-01-06 | Cua hang Q3 | 850000 |

File 2: "khach_hang.xlsx"
Cột: ma_khach_hang, ten_khach_hang, khu_vuc
Mẫu:
| ma_khach_hang | ten_khach_hang | khu_vuc |
| KH100 | Nguyen Van A | Mien Nam |
| KH101 | Tran Thi B | Mien Bac |

File 3: "khuyen_mai.xlsx"
Cột: order_id, ma_khuyen_mai, phan_tram_giam
Mẫu:
| order_id | ma_khuyen_mai | phan_tram_giam |
| DH001 | KM01 | 10 |

Trả lời DUY NHẤT bằng JSON theo đúng cấu trúc sau, không thêm giải thích:
{
  "joins": [
    {"left_file": "...", "left_column": "...", "right_file": "...", "right_column": "...", "confidence": "high|medium|low"}
  ]
}
"""

JOIN_PLAN_SCHEMA = {
    "type": "object",
    "required": ["joins"],
    "properties": {
        "joins": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["left_file", "left_column", "right_file", "right_column", "confidence"],
                "properties": {
                    "left_file": {"type": "string"},
                    "left_column": {"type": "string"},
                    "right_file": {"type": "string"},
                    "right_column": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
            },
        }
    },
}

DASHBOARD_PLAN_PROMPT = """Bạn là một Data Analyst. Dữ liệu đã được làm sạch, có schema sau:
order_id, order_date, month, region, store_name, total_amount, quantity

Yêu cầu của người dùng: "tạo dashboard doanh số"

Hãy chọn các KPI và biểu đồ phù hợp cho dashboard này. Trả lời DUY NHẤT bằng JSON theo đúng cấu trúc sau, không thêm giải thích:
{
  "template": "sales_dashboard",
  "kpis": [
    {"name": "...", "source_column": "...", "aggregation": "sum|count|avg"}
  ],
  "charts": [
    {"type": "line|bar|pie", "title": "...", "group_by": "...", "value_column": "..."}
  ]
}
"""

DASHBOARD_PLAN_SCHEMA = {
    "type": "object",
    "required": ["template", "kpis", "charts"],
    "properties": {
        "template": {"type": "string"},
        "kpis": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "source_column", "aggregation"],
                "properties": {
                    "name": {"type": "string"},
                    "source_column": {"type": "string"},
                    "aggregation": {"type": "string", "enum": ["sum", "count", "avg"]},
                },
            },
        },
        "charts": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["type", "title", "group_by", "value_column"],
                "properties": {
                    "type": {"type": "string", "enum": ["line", "bar", "pie"]},
                    "title": {"type": "string"},
                    "group_by": {"type": "string"},
                    "value_column": {"type": "string"},
                },
            },
        },
    },
}

TEST_CASES = [
    {"name": "join_plan", "prompt": JOIN_PLAN_PROMPT, "schema": JOIN_PLAN_SCHEMA},
    {"name": "dashboard_plan", "prompt": DASHBOARD_PLAN_PROMPT, "schema": DASHBOARD_PLAN_SCHEMA},
]
